"""回测相关数据模型：配置、交易记录、每日快照、回测结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BuyTiming(Enum):
    """买入时机"""

    CLOSE = "close"          # 信号日收盘价买入
    NEXT_OPEN = "next_open"  # 次日开盘价买入


class SignalMode(Enum):
    """选股信号模式"""

    TDX_EXPRESSION = "tdx_expression"      # TDX 表达式选股（默认）
    PATTERN_VERIFY = "pattern_verify"      # 砖形图定式验证选股


class SellAction(Enum):
    """卖出动作类型"""

    HOLD = "hold"        # 继续持有
    PARTIAL = "partial"  # 部分卖出
    CLEAR = "clear"      # 全部清仓


@dataclass(frozen=True)
class SellSignal:
    """卖出信号"""

    action: SellAction = SellAction.HOLD
    ratio: float = 1.0   # 卖出比例（PARTIAL 时使用）
    reason: str = ""
    price: float | None = None  # 建议卖出价（None = 使用默认收盘价）


@dataclass
class BacktestConfig:
    """回测配置参数"""

    # 策略参数
    template_id: str = ""
    template_name: str = ""
    tdx_source: str = ""
    stock_pool_name: str = "default"

    # 时间范围
    start_date: str = ""
    end_date: str = ""

    # 资金参数
    initial_capital: float = 100_000.0
    position_size: float = 0.33   # 单只股票仓位比例（33%）
    max_positions: int = 3        # 最大同时持仓数

    # 交易成本
    commission_rate: float = 0.0001   # 佣金费率（万1）
    min_commission: float = 5.0       # 最低佣金
    stamp_tax_rate: float = 0.001     # 印花税（千1，仅卖出）

    # 买入时机
    buy_timing: BuyTiming = BuyTiming.NEXT_OPEN

    # 卖出策略名称（用于动态加载对应的 SellStrategy）
    sell_strategy_name: str = "default"
    sell_strategy_params: dict = field(default_factory=dict)

    # 买入评分器（空字符串 = 不使用评分）
    buy_scorer_name: str = ""
    buy_scorer_params: dict = field(default_factory=dict)

    # 选股信号模式
    signal_mode: SignalMode = SignalMode.TDX_EXPRESSION

    # 定式验证选股参数（signal_mode == PATTERN_VERIFY 时生效）
    pattern_min_score: float = 80.0       # 定式评分 ≥ 此值视为买入信号
    pattern_price_limit: float = 0.0      # 价格上限过滤（0 = 不限制）


@dataclass
class BacktestHolding:
    """回测持仓信息"""

    symbol: str
    name: str
    quantity: int
    cost_price: float       # 买入均价
    total_cost: float       # 总成本（含佣金）
    buy_date: str           # 买入日期
    current_price: float = 0.0
    current_value: float = 0.0
    pnl_amount: float = 0.0
    pnl_percent: float = 0.0
    partial_sold: bool = False  # 是否已做过分批止盈（兼容旧逻辑）
    partial_sell_count: int = 0  # 已执行分批止盈的次数
    buy_day_low: float = 0.0    # 买入当天K线最低价（止损位）
    buy_data_index: int = -1    # 买入日在 daily_data 中的索引（用于计算持有天数）

    def update_price(self, price: float) -> None:
        """更新当前价格并重新计算盈亏"""
        self.current_price = price
        self.current_value = price * self.quantity
        self.pnl_amount = self.current_value - self.total_cost
        self.pnl_percent = (
            (self.pnl_amount / self.total_cost * 100) if self.total_cost > 0 else 0.0
        )

    @property
    def hold_days(self) -> int:
        """占位属性，实际持有天数在结算时由外部计算"""
        return 0


@dataclass(frozen=True)
class BacktestTradeRecord:
    """回测交易记录"""

    symbol: str
    name: str
    action: str              # "BUY" / "SELL"
    price: float
    quantity: int
    amount: float            # 成交金额
    commission: float        # 佣金
    stamp_tax: float         # 印花税
    total_cost: float        # 实际扣款/到账金额（含手续费）
    trade_date: str
    reason: str = ""         # 卖出原因


@dataclass
class DailySnapshot:
    """每日状态快照"""

    date: str
    total_assets: float          # 总资产 = 可用资金 + 持仓市值
    cash: float                  # 可用资金
    holdings_value: float        # 持仓市值
    holdings_count: int          # 持仓数量
    position_ratio: float = 0.0  # 仓位占比（持仓市值 / 总资产）
    daily_return: float = 0.0    # 当日收益率
    cumulative_return: float = 0.0  # 累计收益率
    trades_today: list[BacktestTradeRecord] = field(default_factory=list)
    holdings_detail: list[dict] = field(default_factory=list)


@dataclass
class BacktestMetrics:
    """回测绩效指标"""

    total_return: float = 0.0           # 总收益率
    annual_return: float = 0.0          # 年化收益率
    max_drawdown: float = 0.0           # 最大回撤
    sharpe_ratio: float = 0.0           # 夏普比率
    win_rate: float = 0.0               # 胜率
    profit_loss_ratio: float = 0.0      # 盈亏比
    total_trades: int = 0               # 总交易次数（完整买卖算一次）
    average_hold_days: float = 0.0      # 平均持仓天数
    max_consecutive_losses: int = 0     # 最大连续亏损
    annual_volatility: float = 0.0      # 年化波动率
    calmar_ratio: float = 0.0           # Calmar 比率
    monthly_returns: list[dict] = field(default_factory=list)  # 月度收益分布

    # 基准对比指标
    benchmark_return: float = 0.0       # 基准总收益率
    benchmark_annual_return: float = 0.0  # 基准年化收益率
    excess_return: float = 0.0          # 超额收益率（策略 - 基准）


@dataclass
class BenchmarkSnapshot:
    """基准指数每日快照"""

    date: str
    close: float
    daily_return: float = 0.0
    cumulative_return: float = 0.0

@dataclass
class BacktestResult:
    """回测结果"""

    config: BacktestConfig
    trades: list[BacktestTradeRecord] = field(default_factory=list)
    snapshots: list[DailySnapshot] = field(default_factory=list)
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    final_cash: float = 0.0
    final_holdings: list[BacktestHolding] = field(default_factory=list)
    trading_days: int = 0
    benchmark_snapshots: list[BenchmarkSnapshot] = field(default_factory=list)
