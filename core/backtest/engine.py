"""回测引擎：时间步进 + 信号触发 + 交易执行 + 快照记录。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from core.backtest.models import (
    BacktestConfig,
    BacktestHolding,
    BacktestResult,
    BacktestTradeRecord,
    BenchmarkSnapshot,
    BuyTiming,
    DailySnapshot,
    SignalMode,
)
from core.backtest.buy_scorer import BrickBuyScorer, create_buy_scorer
from core.backtest.sell_strategy import create_sell_strategy
from core.backtest.signal_cache import get_cached_signals, save_cached_signals
from core.data.repository import StockRepository
from core.data.time_index import build_date_index, locate_time_index, locate_time_index_fast
from core.expression.evaluator import EvaluationContext, evaluate_expression
from core.expression.parser.transpiler import transpile_tdx_source
from core.models.brick_pattern import PatternType
from core.screening.brick_pattern_engine import screen_single_stock
from core.screening.engine import ScreeningEngine
from core.stock_pool.manager import StockPoolManager


def _as_bool_result(value, df: pd.DataFrame) -> np.ndarray:
    """将表达式求值结果转换为布尔数组"""
    if isinstance(value, np.ndarray):
        if value.dtype == bool:
            return value
        return np.isfinite(value) & (value != 0)
    if np.isscalar(value):
        return np.full(len(df), bool(value), dtype=bool)
    return np.zeros(len(df), dtype=bool)


def _calc_buy_commission(amount: float, config: BacktestConfig) -> float:
    """计算买入佣金"""
    return max(amount * config.commission_rate, config.min_commission)


def _calc_sell_commission(amount: float, config: BacktestConfig) -> float:
    """计算卖出佣金"""
    return max(amount * config.commission_rate, config.min_commission)


def _calc_stamp_tax(amount: float, config: BacktestConfig) -> float:
    """计算印花税（仅卖出时收取）"""
    return amount * config.stamp_tax_rate


def _extract_trading_days(
    repository: StockRepository,
    start_date: str,
    end_date: str,
) -> list[str]:
    """从大盘股数据中提取实际交易日序列

    使用上证指数（000001）或平安银行（000001）的日线数据提取交易日。
    """
    benchmark_symbols = ["000001", "600000", "000002"]

    for symbol in benchmark_symbols:
        try:
            df = repository.get_daily_frame(symbol)
            if df is not None and not df.empty and "date" in df.columns:
                dates = pd.to_datetime(df["date"], errors="coerce").dropna()
                start = pd.Timestamp(start_date)
                end = pd.Timestamp(end_date)
                filtered = dates[(dates >= start) & (dates <= end)]
                trading_days = sorted(filtered.dt.strftime("%Y-%m-%d").unique().tolist())
                if trading_days:
                    return trading_days
        except Exception:
            continue

    raise ValueError(
        f"无法提取交易日历：尝试了 {benchmark_symbols}，"
        f"请确保至少有一只股票的日线数据覆盖 {start_date} ~ {end_date}"
    )


@dataclass
class BacktestEngine:
    """回测引擎

    职责：
    - 按交易日步进
    - 调用选股引擎生成买入信号
    - 调用卖出策略判断卖出
    - 管理资金和持仓
    - 记录每日快照
    """

    repository: StockRepository
    screening_engine: ScreeningEngine
    stock_pool_manager: StockPoolManager

    @classmethod
    def from_root(cls, root: Path) -> BacktestEngine:
        repository = StockRepository(root)
        stock_pool_manager = StockPoolManager(repository)
        screening_engine = ScreeningEngine.from_root(root)
        return cls(
            repository=repository,
            screening_engine=screening_engine,
            stock_pool_manager=stock_pool_manager,
        )

    def _precompute_signal_table(
        self,
        compiled_expression,
        trading_days: list[str],
        cached_pool,
        daily_data_cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        """预计算选股信号表：每只股票只做一次全量表达式计算

        对每只股票调用一次 evaluate_expression()，得到覆盖整个时间序列的
        布尔数组，然后提取回测区间内的命中日期。总计算量从
        ``交易天数 × 股票池`` 降为 ``股票池`` 次表达式求值。

        Returns:
            信号表 {date_str: [{symbol, name}, ...]}
        """
        trading_days_set = set(trading_days)
        signal_table: dict[str, list[dict[str, str]]] = {d: [] for d in trading_days}
        stock_map = {s.symbol: s for s in cached_pool.stocks}
        total = len(cached_pool.symbols)

        for i, symbol in enumerate(cached_pool.symbols):
            try:
                df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
                if df is None or df.empty:
                    continue

                context = EvaluationContext(df=df)
                result_array = evaluate_expression(compiled_expression, context)
                bool_array = _as_bool_result(result_array, df)

                date_idx = date_index_cache.get(symbol, {})
                name = stock_map[symbol].name if symbol in stock_map else ""

                for date_str, row_idx in date_idx.items():
                    if (
                        date_str in trading_days_set
                        and row_idx < len(bool_array)
                        and bool_array[row_idx]
                    ):
                        signal_table[date_str].append({"symbol": symbol, "name": name})
            except Exception:
                continue

            if progress_callback is not None and ((i + 1) % 50 == 0 or i + 1 == total):
                progress_callback({
                    "phase": "precompute",
                    "current": i + 1,
                    "total": total,
                })

        return signal_table

    def _precompute_pattern_signal_table(
        self,
        trading_days: list[str],
        cached_pool,
        daily_data_cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]],
        min_score: float = 80.0,
        price_limit: float = 0.0,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        """预计算定式验证信号表：遍历每只股票的每个交易日，执行砖形图定式检测。

        对每只股票的全部交易日逐日调用 screen_single_stock()，
        当定式匹配通过（final_matched）且最高评分 ≥ min_score 时，记录为买入信号。

        Returns:
            信号表 {date_str: [{symbol, name, score_reason}, ...]}
        """
        all_patterns = (
            PatternType.N_SHAPE_JUMP,
            PatternType.SIDEWAYS_JUMP,
            PatternType.UPTREND_CONTINUE,
        )
        trading_days_set = set(trading_days)
        signal_table: dict[str, list[dict[str, str]]] = {d: [] for d in trading_days}
        stock_map = {s.symbol: s for s in cached_pool.stocks}
        total = len(cached_pool.symbols)

        for i, symbol in enumerate(cached_pool.symbols):
            try:
                df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
                if df is None or df.empty:
                    continue

                date_idx = date_index_cache.get(symbol, {})
                name = stock_map[symbol].name if symbol in stock_map else ""

                for date_str, row_idx in date_idx.items():
                    if date_str not in trading_days_set:
                        continue
                    if row_idx < 10:
                        continue

                    match = screen_single_stock(
                        df=df,
                        index=row_idx,
                        symbol=symbol,
                        name=name,
                        target_date=date_str,
                        actual_date=date_str,
                        enabled_patterns=all_patterns,
                        price_limit=price_limit,
                    )

                    if not match.final_matched:
                        continue

                    # 取最高定式评分
                    best_score = max(
                        (pm.score for pm in match.pattern_matches if pm.matched),
                        default=0.0,
                    )

                    if best_score >= min_score:
                        signal_table[date_str].append({
                            "symbol": symbol,
                            "name": name,
                            "score_reason": (
                                f"定式验证买入({match.matched_pattern}"
                                f" 评分:{best_score:.0f})"
                            ),
                        })
            except Exception:
                continue

            if progress_callback is not None and ((i + 1) % 50 == 0 or i + 1 == total):
                progress_callback({
                    "phase": "precompute",
                    "current": i + 1,
                    "total": total,
                })

        return signal_table

    def precompute_signals(
        self,
        config: BacktestConfig,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict[str, list[dict[str, str]]]:
        """预计算信号表的公开接口（带缓存），供敏感性分析等外部调用。

        Returns:
            信号表 {date_str: [{symbol, name}, ...]}
        """
        root = self.repository.root

        # 检查信号缓存
        cached = get_cached_signals(
            root, config.tdx_source, config.stock_pool_name,
            config.start_date, config.end_date,
        )
        if cached is not None:
            if progress_callback is not None:
                progress_callback({"phase": "precompute", "current": 1, "total": 1, "cache_hit": True})
            return cached

        # 预编译表达式
        compiled_expression = None
        if config.tdx_source and config.tdx_source.strip():
            try:
                compiled_expression = transpile_tdx_source(config.tdx_source)
            except Exception:
                pass

        if compiled_expression is None:
            return {}

        # 提取交易日
        trading_days = _extract_trading_days(
            self.repository, config.start_date, config.end_date,
        )

        # 加载股票池和数据缓存
        daily_data_cache: dict[str, pd.DataFrame] = {}
        date_index_cache: dict[str, dict[str, int]] = {}
        cached_pool = self.stock_pool_manager.get_default_pool(config.stock_pool_name)

        signal_table = self._precompute_signal_table(
            compiled_expression, trading_days, cached_pool,
            daily_data_cache, date_index_cache, progress_callback,
        )

        # 保存缓存
        try:
            save_cached_signals(
                root, config.tdx_source, config.stock_pool_name,
                config.start_date, config.end_date, signal_table,
            )
        except Exception:
            pass

        return signal_table

    def run(
        self,
        config: BacktestConfig,
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        """执行回测主循环"""
        sell_strategy = create_sell_strategy(
            config.sell_strategy_name,
            config.sell_strategy_params or None,
        )

        # 创建买入评分器（brick_chart 策略默认启用）
        scorer = self._create_scorer(config)

        # 提取交易日序列
        trading_days = _extract_trading_days(
            self.repository, config.start_date, config.end_date,
        )
        total_days = len(trading_days)

        if total_days == 0:
            raise ValueError(f"回测区间 {config.start_date} ~ {config.end_date} 内无交易日")

        # ── 预处理阶段 ──
        daily_data_cache: dict[str, pd.DataFrame] = {}
        date_index_cache: dict[str, dict[str, int]] = {}
        cached_pool = self.stock_pool_manager.get_default_pool(config.stock_pool_name)

        # ── 信号预计算 ──
        root = self.repository.root
        signal_table: dict[str, list[dict[str, str]]] | None = None

        if config.signal_mode == SignalMode.PATTERN_VERIFY:
            # 定式验证模式：使用砖形图定式引擎生成信号
            signal_table = self._precompute_pattern_signal_table(
                trading_days, cached_pool,
                daily_data_cache, date_index_cache,
                config.pattern_min_score,
                config.pattern_price_limit,
                progress_callback,
            )
        else:
            # TDX 表达式模式（默认）：每只股票做一次全量表达式计算
            # 1) 检查信号缓存
            signal_table = get_cached_signals(
                root, config.tdx_source, config.stock_pool_name,
                config.start_date, config.end_date,
            )

            # 2) 缓存未命中 → 预计算信号表
            if signal_table is None:
                compiled_expression = None
                if config.tdx_source and config.tdx_source.strip():
                    try:
                        compiled_expression = transpile_tdx_source(config.tdx_source)
                    except Exception:
                        pass

                if compiled_expression is not None:
                    signal_table = self._precompute_signal_table(
                        compiled_expression, trading_days, cached_pool,
                        daily_data_cache, date_index_cache, progress_callback,
                    )
                    # 保存信号缓存
                    try:
                        save_cached_signals(
                            root, config.tdx_source, config.stock_pool_name,
                            config.start_date, config.end_date, signal_table,
                        )
                    except Exception:
                        pass

        if signal_table is None:
            signal_table = {}

        # 初始化资金和持仓
        cash = config.initial_capital
        holdings: dict[str, BacktestHolding] = {}
        all_trades: list[BacktestTradeRecord] = []
        snapshots: list[DailySnapshot] = []
        prev_total_assets = config.initial_capital

        # 待执行的买入信号（次日开盘价模式下使用）
        pending_buy_signals: list[dict] = []

        for day_index, trade_date in enumerate(trading_days):
            if cancelled_fn is not None and cancelled_fn():
                break

            today_trades: list[BacktestTradeRecord] = []

            # ── 步骤 0：执行前一日的待买入信号（次日开盘价模式）──
            if pending_buy_signals:
                for signal in pending_buy_signals:
                    buy_record = self._execute_buy_fast(
                        signal["symbol"],
                        signal["name"],
                        trade_date,
                        cash,
                        holdings,
                        config,
                        daily_data_cache,
                        date_index_cache,
                        use_open_price=True,
                        buy_reason=signal.get("score_reason", "选股信号买入"),
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)
                pending_buy_signals.clear()

            # ── 步骤 1：更新持仓价格，检查卖出条件 ──
            # 记录今日买入的股票（T+1 限制：买入当天不能卖出）
            today_bought_symbols: set[str] = {
                rec.symbol for rec in today_trades if rec.action == "BUY"
            }

            symbols_to_remove: list[str] = []
            for symbol, holding in list(holdings.items()):
                daily_df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
                if daily_df is None:
                    continue

                # 使用预构建的日期索引进行 O(1) 定位
                date_index = date_index_cache.get(symbol)
                if date_index is not None:
                    time_result = locate_time_index_fast(date_index, trade_date)
                else:
                    time_result = locate_time_index(daily_df, trade_date)

                if not time_result.matched or time_result.index is None:
                    continue

                current_close = float(daily_df.iloc[time_result.index]["close"])
                holding.update_price(current_close)

                # T+1 限制：买入当天不检查卖出
                if symbol in today_bought_symbols:
                    continue

                # 调用卖出策略
                sell_signal = sell_strategy.should_sell(
                    holding, daily_df, time_result.index,
                )

                if sell_signal.action.value == "clear":
                    sell_price = sell_signal.price if sell_signal.price is not None else current_close
                    sell_record = self._execute_sell(
                        holding, sell_price, trade_date,
                        holding.quantity, config, sell_signal.reason,
                    )
                    cash += sell_record.total_cost
                    today_trades.append(sell_record)
                    symbols_to_remove.append(symbol)

                elif sell_signal.action.value == "partial":
                    sell_price = sell_signal.price if sell_signal.price is not None else current_close
                    sell_quantity = max(
                        int(holding.quantity * sell_signal.ratio / 100) * 100,
                        100,
                    )
                    sell_quantity = min(sell_quantity, holding.quantity)
                    if sell_quantity >= 100:
                        sell_record = self._execute_sell(
                            holding, sell_price, trade_date,
                            sell_quantity, config, sell_signal.reason,
                        )
                        cash += sell_record.total_cost
                        today_trades.append(sell_record)
                        holding.quantity -= sell_quantity
                        holding.total_cost = holding.cost_price * holding.quantity
                        holding.partial_sold = True
                        holding.partial_sell_count += 1
                        holding.update_price(current_close)
                        if holding.quantity <= 0:
                            symbols_to_remove.append(symbol)

            for symbol in symbols_to_remove:
                holdings.pop(symbol, None)

            # ── 步骤 2：从预计算信号表获取买入信号（O(1) 查表）──
            matched_stocks = signal_table.get(trade_date, [])

            # ── 步骤 2.5：评分排序 + 禁止过滤 ──
            matched_stocks = self._score_and_filter(
                matched_stocks, scorer, trade_date, holdings,
                daily_data_cache, date_index_cache,
            )

            # ── 步骤 3：执行买入 ──
            for match in matched_stocks:
                symbol = match["symbol"]
                name = match["name"]

                # 已持仓不重复买入
                if symbol in holdings:
                    continue

                # 持仓数量上限
                if len(holdings) >= config.max_positions:
                    break

                buy_reason = match.get("score_reason", "选股信号买入")

                if config.buy_timing == BuyTiming.NEXT_OPEN:
                    # 次日开盘价模式：记录信号，下一个交易日执行
                    pending_buy_signals.append({
                        "symbol": symbol, "name": name,
                        "score_reason": buy_reason,
                    })
                else:
                    # 收盘价模式：当日执行
                    buy_record = self._execute_buy_fast(
                        symbol, name, trade_date, cash, holdings,
                        config, daily_data_cache, date_index_cache,
                        use_open_price=False,
                        buy_reason=buy_reason,
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)

            # ── 步骤 4：记录每日快照 ──
            holdings_value = sum(h.current_value for h in holdings.values())
            total_assets = cash + holdings_value
            daily_return = (total_assets / prev_total_assets - 1) if prev_total_assets > 0 else 0.0
            cumulative_return = (total_assets / config.initial_capital - 1)

            position_ratio = holdings_value / total_assets if total_assets > 0 else 0.0
            snapshot = DailySnapshot(
                date=trade_date,
                total_assets=total_assets,
                cash=cash,
                holdings_value=holdings_value,
                holdings_count=len(holdings),
                position_ratio=position_ratio,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                trades_today=today_trades,
                holdings_detail=[
                    {
                        "symbol": h.symbol,
                        "name": h.name,
                        "quantity": h.quantity,
                        "cost_price": h.cost_price,
                        "current_price": h.current_price,
                        "pnl_percent": h.pnl_percent,
                    }
                    for h in holdings.values()
                ],
            )
            snapshots.append(snapshot)
            all_trades.extend(today_trades)
            prev_total_assets = total_assets

            # 发送进度回调
            if progress_callback is not None:
                progress_callback({
                    "phase": "simulate",
                    "current": day_index + 1,
                    "total": total_days,
                    "date": trade_date,
                    "total_assets": total_assets,
                    "trades_today": len(today_trades),
                })

        # 加载基准数据（沪深300代理：使用大盘股日线数据）
        benchmark_snapshots = self._load_benchmark_snapshots(
            trading_days, daily_data_cache, date_index_cache,
        )

        return BacktestResult(
            config=config,
            trades=all_trades,
            snapshots=snapshots,
            final_cash=cash,
            final_holdings=list(holdings.values()),
            trading_days=len(snapshots),
            benchmark_snapshots=benchmark_snapshots,
        )

    def run_with_signals(
        self,
        config: BacktestConfig,
        signal_table: dict[str, list[dict[str, str]]],
        progress_callback: Callable[[dict], None] | None = None,
        cancelled_fn: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        """使用预计算的信号表执行回测（跳过选股阶段）

        专为敏感性分析设计：选股条件不变、只改卖出参数时，
        多次回测可共享同一张信号表，避免重复计算选股。
        """
        sell_strategy = create_sell_strategy(
            config.sell_strategy_name,
            config.sell_strategy_params or None,
        )

        # 创建买入评分器
        scorer = self._create_scorer(config)

        trading_days = _extract_trading_days(
            self.repository, config.start_date, config.end_date,
        )
        total_days = len(trading_days)
        if total_days == 0:
            raise ValueError(f"回测区间 {config.start_date} ~ {config.end_date} 内无交易日")

        daily_data_cache: dict[str, pd.DataFrame] = {}
        date_index_cache: dict[str, dict[str, int]] = {}

        cash = config.initial_capital
        holdings: dict[str, BacktestHolding] = {}
        all_trades: list[BacktestTradeRecord] = []
        snapshots: list[DailySnapshot] = []
        prev_total_assets = config.initial_capital
        pending_buy_signals: list[dict] = []

        for day_index, trade_date in enumerate(trading_days):
            if cancelled_fn is not None and cancelled_fn():
                break

            today_trades: list[BacktestTradeRecord] = []

            # ── 步骤 0：执行前一日的待买入信号 ──
            if pending_buy_signals:
                for signal in pending_buy_signals:
                    buy_record = self._execute_buy_fast(
                        signal["symbol"], signal["name"], trade_date,
                        cash, holdings, config,
                        daily_data_cache, date_index_cache,
                        use_open_price=True,
                        buy_reason=signal.get("score_reason", "选股信号买入"),
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)
                pending_buy_signals.clear()

            # ── 步骤 1：更新持仓价格，检查卖出条件 ──
            # T+1 限制：买入当天不能卖出
            today_bought_symbols: set[str] = {
                rec.symbol for rec in today_trades if rec.action == "BUY"
            }

            symbols_to_remove: list[str] = []
            for symbol, holding in list(holdings.items()):
                daily_df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
                if daily_df is None:
                    continue

                date_index = date_index_cache.get(symbol)
                if date_index is not None:
                    time_result = locate_time_index_fast(date_index, trade_date)
                else:
                    time_result = locate_time_index(daily_df, trade_date)

                if not time_result.matched or time_result.index is None:
                    continue

                current_close = float(daily_df.iloc[time_result.index]["close"])
                holding.update_price(current_close)

                # T+1 限制：买入当天不检查卖出
                if symbol in today_bought_symbols:
                    continue

                sell_signal = sell_strategy.should_sell(
                    holding, daily_df, time_result.index,
                )

                if sell_signal.action.value == "clear":
                    sell_price = sell_signal.price if sell_signal.price is not None else current_close
                    sell_record = self._execute_sell(
                        holding, sell_price, trade_date,
                        holding.quantity, config, sell_signal.reason,
                    )
                    cash += sell_record.total_cost
                    today_trades.append(sell_record)
                    symbols_to_remove.append(symbol)

                elif sell_signal.action.value == "partial":
                    sell_price = sell_signal.price if sell_signal.price is not None else current_close
                    sell_quantity = max(
                        int(holding.quantity * sell_signal.ratio / 100) * 100,
                        100,
                    )
                    sell_quantity = min(sell_quantity, holding.quantity)
                    if sell_quantity >= 100:
                        sell_record = self._execute_sell(
                            holding, sell_price, trade_date,
                            sell_quantity, config, sell_signal.reason,
                        )
                        cash += sell_record.total_cost
                        today_trades.append(sell_record)
                        holding.quantity -= sell_quantity
                        holding.total_cost = holding.cost_price * holding.quantity
                        holding.partial_sold = True
                        holding.partial_sell_count += 1
                        holding.update_price(current_close)
                        if holding.quantity <= 0:
                            symbols_to_remove.append(symbol)

            for symbol in symbols_to_remove:
                holdings.pop(symbol, None)

            # ── 步骤 2：从信号表获取买入信号 ──
            matched_stocks = signal_table.get(trade_date, [])

            # ── 步骤 2.5：评分排序 + 禁止过滤 ──
            matched_stocks = self._score_and_filter(
                matched_stocks, scorer, trade_date, holdings,
                daily_data_cache, date_index_cache,
            )

            # ── 步骤 3：执行买入 ──
            for match in matched_stocks:
                symbol = match["symbol"]
                name = match["name"]

                if symbol in holdings:
                    continue
                if len(holdings) >= config.max_positions:
                    break

                buy_reason = match.get("score_reason", "选股信号买入")

                if config.buy_timing == BuyTiming.NEXT_OPEN:
                    pending_buy_signals.append({
                        "symbol": symbol, "name": name,
                        "score_reason": buy_reason,
                    })
                else:
                    buy_record = self._execute_buy_fast(
                        symbol, name, trade_date, cash, holdings,
                        config, daily_data_cache, date_index_cache,
                        use_open_price=False,
                        buy_reason=buy_reason,
                    )
                    if buy_record is not None:
                        cash -= buy_record.total_cost
                        today_trades.append(buy_record)

            # ── 步骤 4：记录每日快照 ──
            holdings_value = sum(h.current_value for h in holdings.values())
            total_assets = cash + holdings_value
            daily_return = (total_assets / prev_total_assets - 1) if prev_total_assets > 0 else 0.0
            cumulative_return = (total_assets / config.initial_capital - 1)

            position_ratio = holdings_value / total_assets if total_assets > 0 else 0.0
            snapshot = DailySnapshot(
                date=trade_date,
                total_assets=total_assets,
                cash=cash,
                holdings_value=holdings_value,
                holdings_count=len(holdings),
                position_ratio=position_ratio,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                trades_today=today_trades,
                holdings_detail=[
                    {
                        "symbol": h.symbol, "name": h.name,
                        "quantity": h.quantity, "cost_price": h.cost_price,
                        "current_price": h.current_price, "pnl_percent": h.pnl_percent,
                    }
                    for h in holdings.values()
                ],
            )
            snapshots.append(snapshot)
            all_trades.extend(today_trades)
            prev_total_assets = total_assets

            if progress_callback is not None:
                progress_callback({
                    "phase": "simulate",
                    "current": day_index + 1,
                    "total": total_days,
                    "date": trade_date,
                    "total_assets": total_assets,
                    "trades_today": len(today_trades),
                })

        benchmark_snapshots = self._load_benchmark_snapshots(
            trading_days, daily_data_cache, date_index_cache,
        )

        return BacktestResult(
            config=config,
            trades=all_trades,
            snapshots=snapshots,
            final_cash=cash,
            final_holdings=list(holdings.values()),
            trading_days=len(snapshots),
            benchmark_snapshots=benchmark_snapshots,
        )

    def _load_benchmark_snapshots(
        self,
        trading_days: list[str],
        daily_data_cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]] | None = None,
    ) -> list[BenchmarkSnapshot]:
        """加载基准指数数据，计算每日收益率和累计收益率

        使用大盘股（平安银行 000001 / 浦发银行 600000）的收盘价作为基准代理。
        """
        benchmark_symbols = ["000001", "600000", "000002"]

        for symbol in benchmark_symbols:
            daily_df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
            if daily_df is None or daily_df.empty:
                continue

            # 优先使用预构建的日期索引
            date_index = (
                date_index_cache.get(symbol)
                if date_index_cache is not None
                else None
            )

            snapshots: list[BenchmarkSnapshot] = []
            initial_close: float | None = None
            prev_close: float | None = None

            for trade_date in trading_days:
                if date_index is not None:
                    time_result = locate_time_index_fast(date_index, trade_date)
                else:
                    time_result = locate_time_index(daily_df, trade_date)

                if not time_result.matched or time_result.index is None:
                    continue

                close = float(daily_df.iloc[time_result.index]["close"])

                if initial_close is None:
                    initial_close = close

                daily_return = (
                    (close / prev_close - 1) if prev_close is not None and prev_close > 0 else 0.0
                )
                cumulative_return = (close / initial_close - 1) if initial_close > 0 else 0.0

                snapshots.append(BenchmarkSnapshot(
                    date=trade_date,
                    close=close,
                    daily_return=daily_return,
                    cumulative_return=cumulative_return,
                ))
                prev_close = close

            if snapshots:
                return snapshots

        return []

    @staticmethod
    def _create_scorer(config: BacktestConfig) -> BrickBuyScorer | None:
        """根据配置创建买入评分器

        当 sell_strategy_name == "brick_chart" 且 buy_scorer_name 未显式设置时，
        自动启用砖形图评分器。

        定式验证模式下跳过评分器：定式引擎已包含完整的前提检测、
        定式匹配和风险过滤，无需再做二次评分过滤。
        """
        if config.signal_mode == SignalMode.PATTERN_VERIFY:
            return None

        scorer_name = config.buy_scorer_name
        if not scorer_name and config.sell_strategy_name == "brick_chart":
            scorer_name = "brick"
        return create_buy_scorer(scorer_name, config.buy_scorer_params or None)

    def _score_and_filter(
        self,
        matched_stocks: list[dict[str, str]],
        scorer: BrickBuyScorer | None,
        trade_date: str,
        holdings: dict[str, BacktestHolding],
        daily_data_cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]],
    ) -> list[dict]:
        """对候选股票执行禁止过滤 + 评分排序"""
        if scorer is None or not matched_stocks:
            return matched_stocks

        # 过滤掉已持仓的
        candidates = [m for m in matched_stocks if m["symbol"] not in holdings]
        if not candidates:
            return candidates

        scored: list[tuple[float, dict]] = []
        for match in candidates:
            symbol = match["symbol"]
            daily_df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
            if daily_df is None:
                continue

            # 定位当前日期的索引
            di = date_index_cache.get(symbol)
            if di is not None:
                time_result = locate_time_index_fast(di, trade_date)
            else:
                time_result = locate_time_index(daily_df, trade_date)

            if not time_result.matched or time_result.index is None:
                scored.append((0.0, {**match, "score_reason": "选股信号买入"}))
                continue

            result = scorer.score(symbol, match.get("name", ""), daily_df, time_result.index)

            if result.vetoed:
                continue  # 被禁止，直接跳过

            scored.append((
                result.total_score,
                {**match, "score_reason": f"选股信号买入({result.format_reason()})"},
            ))

        # 按分数降序排列
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored]

    def _get_daily_data(
        self,
        symbol: str,
        cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]] | None = None,
    ) -> pd.DataFrame | None:
        """获取日线数据（带缓存），同时懒构建日期索引。

        首次加载某只股票数据时，会同时构建其日期索引并存入
        date_index_cache，后续对该股票的日期查找即可使用 O(1) 的
        locate_time_index_fast。
        """
        if symbol in cache:
            return cache[symbol]
        try:
            df = self.repository.get_daily_frame(symbol)
            if df is not None and not df.empty:
                cache[symbol] = df
                # 懒构建日期索引
                if date_index_cache is not None and symbol not in date_index_cache:
                    date_index_cache[symbol] = build_date_index(df)
                return df
        except Exception:
            pass
        return None

    def _execute_buy_fast(
        self,
        symbol: str,
        name: str,
        trade_date: str,
        cash: float,
        holdings: dict[str, BacktestHolding],
        config: BacktestConfig,
        daily_data_cache: dict[str, pd.DataFrame],
        date_index_cache: dict[str, dict[str, int]],
        use_open_price: bool = False,
        buy_reason: str = "选股信号买入",
    ) -> BacktestTradeRecord | None:
        """执行买入操作（使用预构建日期索引加速）"""
        # 已持仓不重复买入
        if symbol in holdings:
            return None

        # 持仓数量上限
        if len(holdings) >= config.max_positions:
            return None

        daily_df = self._get_daily_data(symbol, daily_data_cache, date_index_cache)
        if daily_df is None:
            return None

        # 优先使用预构建的日期索引
        date_index = date_index_cache.get(symbol)
        if date_index is not None:
            time_result = locate_time_index_fast(date_index, trade_date)
        else:
            time_result = locate_time_index(daily_df, trade_date)

        if not time_result.matched or time_result.index is None:
            return None

        row = daily_df.iloc[time_result.index]
        buy_price = float(row["open"]) if use_open_price else float(row["close"])

        if buy_price <= 0:
            return None

        # 计算买入数量（向下取整到 100 股）
        available_amount = cash * config.position_size
        shares = int(available_amount / buy_price / 100) * 100

        if shares < 100:
            return None

        # 计算交易成本
        amount = shares * buy_price
        commission = _calc_buy_commission(amount, config)
        total_cost = amount + commission

        # 资金不足
        if total_cost > cash:
            shares = int((cash - config.min_commission) * config.position_size / buy_price / 100) * 100
            if shares < 100:
                return None
            amount = shares * buy_price
            commission = _calc_buy_commission(amount, config)
            total_cost = amount + commission
            if total_cost > cash:
                return None

        # 创建持仓
        holdings[symbol] = BacktestHolding(
            symbol=symbol,
            name=name,
            quantity=shares,
            cost_price=buy_price,
            total_cost=total_cost,
            buy_date=trade_date,
            current_price=buy_price,
            current_value=amount,
            buy_day_low=float(row["low"]),
            buy_data_index=time_result.index,
        )

        return BacktestTradeRecord(
            symbol=symbol,
            name=name,
            action="BUY",
            price=buy_price,
            quantity=shares,
            amount=amount,
            commission=commission,
            stamp_tax=0.0,
            total_cost=total_cost,
            trade_date=trade_date,
            reason=buy_reason,
        )

    def _execute_buy(
        self,
        symbol: str,
        name: str,
        trade_date: str,
        cash: float,
        holdings: dict[str, BacktestHolding],
        config: BacktestConfig,
        daily_data_cache: dict[str, pd.DataFrame],
        use_open_price: bool = False,
    ) -> BacktestTradeRecord | None:
        """执行买入操作"""
        # 已持仓不重复买入
        if symbol in holdings:
            return None

        # 持仓数量上限
        if len(holdings) >= config.max_positions:
            return None

        daily_df = self._get_daily_data(symbol, daily_data_cache)
        if daily_df is None:
            return None

        time_result = locate_time_index(daily_df, trade_date)
        if not time_result.matched or time_result.index is None:
            return None

        row = daily_df.iloc[time_result.index]
        buy_price = float(row["open"]) if use_open_price else float(row["close"])

        if buy_price <= 0:
            return None

        # 计算买入数量（向下取整到 100 股）
        available_amount = cash * config.position_size
        shares = int(available_amount / buy_price / 100) * 100

        if shares < 100:
            return None

        # 计算交易成本
        amount = shares * buy_price
        commission = _calc_buy_commission(amount, config)
        total_cost = amount + commission

        # 资金不足
        if total_cost > cash:
            shares = int((cash - config.min_commission) * config.position_size / buy_price / 100) * 100
            if shares < 100:
                return None
            amount = shares * buy_price
            commission = _calc_buy_commission(amount, config)
            total_cost = amount + commission
            if total_cost > cash:
                return None

        # 创建持仓
        holdings[symbol] = BacktestHolding(
            symbol=symbol,
            name=name,
            quantity=shares,
            cost_price=buy_price,
            total_cost=total_cost,
            buy_date=trade_date,
            current_price=buy_price,
            current_value=amount,
            buy_day_low=float(row["low"]),
            buy_data_index=time_result.index,
        )

        return BacktestTradeRecord(
            symbol=symbol,
            name=name,
            action="BUY",
            price=buy_price,
            quantity=shares,
            amount=amount,
            commission=commission,
            stamp_tax=0.0,
            total_cost=total_cost,
            trade_date=trade_date,
            reason="选股信号买入",
        )

    def _execute_sell(
        self,
        holding: BacktestHolding,
        sell_price: float,
        trade_date: str,
        sell_quantity: int,
        config: BacktestConfig,
        reason: str,
    ) -> BacktestTradeRecord:
        """执行卖出操作"""
        amount = sell_quantity * sell_price
        commission = _calc_sell_commission(amount, config)
        stamp_tax = _calc_stamp_tax(amount, config)
        net_amount = amount - commission - stamp_tax

        return BacktestTradeRecord(
            symbol=holding.symbol,
            name=holding.name,
            action="SELL",
            price=sell_price,
            quantity=sell_quantity,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            total_cost=net_amount,
            trade_date=trade_date,
            reason=reason,
        )
