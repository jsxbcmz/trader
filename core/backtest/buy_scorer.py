"""砖形图买入评分系统：优先级排序 + 禁止规则。

当同一交易日有多只股票触发选股信号时，评分系统决定买入优先级；
禁止规则对存在明确风险特征的股票实施硬性否决。

评分权重完全可调，通过 buy_scorer_params 传入即可覆盖默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class BuyScoreResult:
    """单只股票的评分结果"""

    symbol: str
    name: str
    total_score: float = 0.0
    vetoed: bool = False
    veto_reason: str = ""
    score_details: dict[str, float] = field(default_factory=dict)

    def format_reason(self) -> str:
        """格式化为买入记录的 reason 字符串"""
        if self.vetoed:
            return f"被禁止({self.veto_reason})"
        parts = [f"{k}:{v:.1f}" for k, v in self.score_details.items()]
        return f"评分:{self.total_score:.1f} {'/'.join(parts)}"


@dataclass
class BrickBuyScorer:
    """砖形图买入评分器

    禁止规则（硬性否决，命中任一即排除）：
    1. 当日绿柱：信号日K线收绿（高开低走造成砖形假象）
    2. 巨量绿柱出货：近N日内出现 ≥ 均量M倍的绿色K线
    3. 砖小柱长：砖值低 + K线实体长
    4. 红绿交替无趋势：砖形方向频繁变化
    5. 箱体震荡：近期价格在窄幅区间内横盘整理
    6. 趋势线横盘：知行短期趋势线振幅极小且无方向
    7. 翻红力度不足：砖形翻红增量过小，属于噪音级别

    评分项（加权求和）：
    1. 砖大柱短：转折动能大 + 上涨空间
    2. 价格贴近趋势线：盈亏比优
    3. 连续绿砖后首根翻红：反转时机早
    4. 空头衰竭反转：形态完整
    """

    # ── 禁止规则参数 ──
    veto_lookback: int = 10
    veto_volume_ratio: float = 1.8
    veto_avg_window: int = 30
    veto_brick_low_ratio: float = 0.5
    veto_body_high_ratio: float = 1.5
    veto_choppy_window: int = 8
    veto_choppy_threshold: int = 4
    veto_box_window: int = 20
    veto_box_range_pct: float = 0.08
    veto_close_consol_window: int = 10
    veto_close_consol_range_pct: float = 0.05
    veto_close_consol_slope: float = 0.002
    veto_brick_min_increase_ratio: float = 0.05

    # ── 评分权重（运行时可调）──
    weight_big_brick_small_body: float = 30.0
    weight_near_trend: float = 25.0
    weight_first_red: float = 25.0
    weight_bear_exhaustion: float = 20.0

    def __post_init__(self):
        self._brick_cache: dict[str, np.ndarray | None] = {}
        self._trend_cache: dict[str, np.ndarray | None] = {}

    # ── 公开接口 ──────────────────────────────────────────

    def score(
        self,
        symbol: str,
        name: str,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> BuyScoreResult:
        """计算买入评分（含禁止规则前置检查）"""
        if current_index < 2:
            return BuyScoreResult(symbol=symbol, name=name)

        # 计算砖形图（带缓存）
        brick = self._get_brick(symbol, daily_data)

        # ── 禁止规则（命中任一即返回）──
        veto = self._check_veto_green_bar_today(daily_data, current_index)
        if veto is None:
            veto = self._check_veto_huge_green_volume(daily_data, current_index)
        if veto is None:
            veto = self._check_veto_small_brick_long_body(
                daily_data, current_index, brick,
            )
        if veto is None:
            veto = self._check_veto_choppy_alternation(brick, current_index)
        if veto is None:
            veto = self._check_veto_box_consolidation(daily_data, current_index)
        if veto is None:
            veto = self._check_veto_trend_consolidation(symbol, daily_data, current_index, brick)
        if veto is None:
            veto = self._check_veto_weak_brick_reversal(brick, current_index)
        if veto is not None:
            return BuyScoreResult(
                symbol=symbol, name=name, vetoed=True, veto_reason=veto,
            )

        # ── 评分 ──
        details: dict[str, float] = {}
        s1 = self._score_big_brick_small_body(daily_data, current_index, brick)
        details["砖大柱短"] = s1
        s2 = self._score_near_trend_line(symbol, daily_data, current_index)
        details["趋势线"] = s2
        s3 = self._score_first_red_after_greens(brick, current_index)
        details["首根翻红"] = s3
        s4 = self._score_bear_exhaustion_reversal(brick, current_index)
        details["衰竭反转"] = s4

        total = s1 + s2 + s3 + s4
        return BuyScoreResult(
            symbol=symbol,
            name=name,
            total_score=total,
            score_details=details,
        )

    def clear_cache(self) -> None:
        """清除缓存（切换股票池时调用）"""
        self._brick_cache.clear()
        self._trend_cache.clear()

    # ── 禁止规则 ──────────────────────────────────────────

    @staticmethod
    def _check_veto_green_bar_today(
        daily_data: pd.DataFrame,
        index: int,
    ) -> str | None:
        """禁止规则1：信号日K线收绿（高开低走造成砖形假象）"""
        close = float(daily_data.iloc[index]["close"])
        open_ = float(daily_data.iloc[index]["open"])
        if close < open_:
            return "当日K线收绿"
        return None

    def _check_veto_huge_green_volume(
        self,
        daily_data: pd.DataFrame,
        index: int,
    ) -> str | None:
        """禁止规则1：近N日内出现巨量绿柱（大资金出货）"""
        if "volume" not in daily_data.columns:
            return None

        volume = daily_data["volume"].values.astype(float)
        close = daily_data["close"].values.astype(float)
        open_ = daily_data["open"].values.astype(float)

        # 计算平均成交额（前30日，排除当日，去掉最高10%异常值后求均）
        avg_start = max(0, index - self.veto_avg_window)
        avg_end = index
        if avg_end <= avg_start:
            return None
        window_vol = volume[avg_start:avg_end]
        trim_count = max(1, len(window_vol) // 10)
        sorted_vol = np.sort(window_vol)
        trimmed_vol = sorted_vol[:-trim_count] if trim_count < len(sorted_vol) else sorted_vol
        avg_volume = np.mean(trimmed_vol)
        if avg_volume <= 0:
            return None

        threshold = avg_volume * self.veto_volume_ratio

        # 回看最近N个交易日
        lookback_start = max(0, index - self.veto_lookback)
        for j in range(lookback_start, index + 1):
            if volume[j] >= threshold and close[j] < open_[j]:
                return "近期巨量绿柱出货"

        return None

    def _check_veto_small_brick_long_body(
        self,
        daily_data: pd.DataFrame,
        index: int,
        brick: np.ndarray | None,
    ) -> str | None:
        """禁止规则2：砖小柱长（反转力度弱）"""
        if brick is None or index >= len(brick):
            return None

        close = daily_data["close"].values.astype(float)
        open_ = daily_data["open"].values.astype(float)

        brick_value = brick[index]
        body_length = abs(close[index] - open_[index])

        # 近20日有效砖均值
        window_start = max(0, index - 19)
        brick_window = brick[window_start:index + 1]
        positive_bricks = brick_window[brick_window > 0]
        if len(positive_bricks) == 0:
            return None
        avg_brick = np.mean(positive_bricks)

        # 近20日实体均值
        body_window = np.abs(close[window_start:index + 1] - open_[window_start:index + 1])
        avg_body = np.mean(body_window)
        if avg_body <= 0:
            return None

        if brick_value < avg_brick * self.veto_brick_low_ratio and body_length > avg_body * self.veto_body_high_ratio:
            return "砖小柱长"

        return None

    def _check_veto_choppy_alternation(
        self,
        brick: np.ndarray | None,
        index: int,
    ) -> str | None:
        """禁止规则3：红绿交替无趋势（横盘震荡）"""
        if brick is None:
            return None

        window = self.veto_choppy_window
        if index < window:
            return None

        direction_changes = 0
        for j in range(index - window + 1, index + 1):
            if j < 2:
                continue
            curr_rising = brick[j] > brick[j - 1]
            prev_rising = brick[j - 1] > brick[j - 2]
            if curr_rising != prev_rising:
                direction_changes += 1

        if direction_changes >= self.veto_choppy_threshold:
            return "红绿交替无趋势"

        return None

    def _check_veto_box_consolidation(
        self,
        daily_data: pd.DataFrame,
        index: int,
    ) -> str | None:
        """禁止规则5：箱体震荡（价格在窄幅区间横盘整理）

        近N日最高价与最低价的振幅占均价比例低于阈值，
        且收盘价无明显趋势方向（线性回归斜率接近零）。
        """
        window = self.veto_box_window
        if index < window:
            return None

        high = daily_data["high"].values.astype(float)
        low = daily_data["low"].values.astype(float)
        close = daily_data["close"].values.astype(float)

        w_start = index - window + 1
        w_high = np.max(high[w_start:index + 1])
        w_low = np.min(low[w_start:index + 1])
        w_mid = (w_high + w_low) / 2

        if w_mid <= 0:
            return None

        # 振幅比例：区间最高-最低 / 中位价
        range_pct = (w_high - w_low) / w_mid
        if range_pct >= self.veto_box_range_pct:
            return None

        # 收盘价线性回归斜率，判断是否无方向
        w_close = close[w_start:index + 1]
        x = np.arange(len(w_close), dtype=float)
        slope = np.polyfit(x, w_close, 1)[0]
        # 斜率占均价比例极小 → 横盘
        slope_pct = abs(slope) / w_mid
        if slope_pct < 0.002:
            return "箱体震荡横盘"

        return None

    def _check_veto_trend_consolidation(
        self,
        symbol: str,
        daily_data: pd.DataFrame,
        index: int,
        brick: np.ndarray | None = None,
    ) -> str | None:
        """禁止规则6：趋势线横盘（知行短期趋势线振幅极小）

        用 EMA(EMA(C,10),10) 趋势线判断横盘，平滑日间噪音。
        横盘 + 强翻红 = 突破信号，予以豁免；
        横盘 + 弱翻红 = 无意义波动，禁止买入。
        """
        window = self.veto_close_consol_window
        if index < window:
            return None

        trend = self._get_trend(symbol, daily_data)
        if trend is None or index >= len(trend):
            return None

        w_start = index - window + 1
        w_trend = trend[w_start:index + 1]

        # 过滤掉 NaN/Inf（EMA 前期可能不稳定）
        valid = w_trend[np.isfinite(w_trend)]
        if len(valid) < window // 2:
            return None

        t_max = np.max(valid)
        t_min = np.min(valid)
        t_mid = (t_max + t_min) / 2

        if t_mid <= 0:
            return None

        range_pct = (t_max - t_min) / t_mid
        if range_pct >= self.veto_close_consol_range_pct:
            return None

        # 斜率接近零 → 确认横盘
        x = np.arange(len(valid), dtype=float)
        slope = np.polyfit(x, valid, 1)[0]
        slope_pct = abs(slope) / t_mid
        if slope_pct >= self.veto_close_consol_slope:
            return None

        # 横盘已确认 → 检查砖形翻红力度，强翻红视为突破豁免
        if brick is not None and index >= 2 and index < len(brick):
            if brick[index] > brick[index - 1]:
                brick_increase = brick[index] - brick[index - 1]
                w20_start = max(0, index - 19)
                positive_bricks = brick[w20_start:index + 1]
                positive_bricks = positive_bricks[positive_bricks > 0]
                if len(positive_bricks) > 0:
                    avg_brick = np.mean(positive_bricks)
                    if avg_brick > 0 and brick_increase >= avg_brick * self.veto_brick_min_increase_ratio:
                        return None  # 强翻红突破横盘，豁免

        return "趋势线横盘整理"

    def _check_veto_weak_brick_reversal(
        self,
        brick: np.ndarray | None,
        index: int,
    ) -> str | None:
        """禁止规则7：翻红力度不足（砖增量过小，噪音级别）

        当前为翻红（砖值上升），但增量占近20日正砖均值的比例
        低于阈值时，判定为无意义的微弱反转。
        """
        if brick is None or index < 2 or index >= len(brick):
            return None

        # 必须是翻红
        if brick[index] <= brick[index - 1]:
            return None

        brick_increase = brick[index] - brick[index - 1]

        # 近20日正砖均值
        window_start = max(0, index - 19)
        brick_window = brick[window_start:index + 1]
        positive_bricks = brick_window[brick_window > 0]
        if len(positive_bricks) == 0:
            return None
        avg_brick = np.mean(positive_bricks)

        if avg_brick <= 0:
            return None

        if brick_increase < avg_brick * self.veto_brick_min_increase_ratio:
            return "翻红力度不足"

        return None

    # ── 评分项 ──────────────────────────────────────────

    def _score_big_brick_small_body(
        self,
        daily_data: pd.DataFrame,
        index: int,
        brick: np.ndarray | None,
    ) -> float:
        """评分1：砖大柱短 — 转折动能大 + 上涨空间"""
        if brick is None or index >= len(brick):
            return 0.0

        close = daily_data["close"].values.astype(float)
        open_ = daily_data["open"].values.astype(float)

        brick_value = brick[index]
        body_length = abs(close[index] - open_[index])

        # 近20日基准
        window_start = max(0, index - 19)
        positive_bricks = brick[window_start:index + 1]
        positive_bricks = positive_bricks[positive_bricks > 0]
        if len(positive_bricks) == 0:
            return 0.0
        avg_brick = np.mean(positive_bricks)

        body_window = np.abs(close[window_start:index + 1] - open_[window_start:index + 1])
        avg_body = np.mean(body_window)
        if avg_body <= 0 or avg_brick <= 0:
            return 0.0

        # 砖值相对强度（越大越好，上限2.0）
        brick_score = np.clip(brick_value / avg_brick - 1.0, 0.0, 2.0)
        # 实体相对短度（越短越好）
        body_score = np.clip(1.0 - body_length / avg_body, 0.0, 1.0)

        return float(brick_score * body_score * self.weight_big_brick_small_body)

    def _score_near_trend_line(
        self,
        symbol: str,
        daily_data: pd.DataFrame,
        index: int,
    ) -> float:
        """评分2：价格贴近短期趋势线 — 盈亏比优"""
        trend = self._get_trend(symbol, daily_data)
        if trend is None or index >= len(trend):
            return 0.0

        trend_value = trend[index]
        if not np.isfinite(trend_value) or trend_value <= 0:
            return 0.0

        close = float(daily_data.iloc[index]["close"])
        deviation = abs(close - trend_value) / trend_value

        if deviation <= 0.02:
            score = 1.0
        elif deviation <= 0.05:
            score = (0.05 - deviation) / 0.03
        else:
            score = 0.0

        return float(score * self.weight_near_trend)

    def _score_first_red_after_greens(
        self,
        brick: np.ndarray | None,
        index: int,
    ) -> float:
        """评分3：连续绿砖后首根翻红 — 反转时机早"""
        if brick is None or index < 2 or index >= len(brick):
            return 0.0

        # 当前必须翻红（砖值上升）
        if brick[index] <= brick[index - 1]:
            return 0.0

        # 向前数连续绿砖数量
        green_count = 0
        j = index - 1
        while j >= 1 and brick[j] <= brick[j - 1]:
            green_count += 1
            j -= 1

        if green_count == 0:
            return 0.0

        # 连续绿砖越多，反转信号越强
        if green_count >= 5:
            score = 1.0
        elif green_count >= 3:
            score = 0.7
        elif green_count >= 2:
            score = 0.4
        else:
            score = 0.2

        return float(score * self.weight_first_red)

    def _score_bear_exhaustion_reversal(
        self,
        brick: np.ndarray | None,
        index: int,
    ) -> float:
        """评分4：空头衰竭 + 巨量红砖反转 — 形态完整"""
        if brick is None or index < 4 or index >= len(brick):
            return 0.0

        # 当前必须是红砖（砖值上升）
        if brick[index] <= brick[index - 1]:
            return 0.0

        # 红砖增量足够大
        brick_increase = brick[index] - brick[index - 1]
        window_start = max(0, index - 19)
        positive_bricks = brick[window_start:index + 1]
        positive_bricks = positive_bricks[positive_bricks > 0]
        if len(positive_bricks) == 0:
            return 0.0
        avg_brick = np.mean(positive_bricks)

        if brick_increase < avg_brick * 0.5:
            return 0.0

        # 向前扫描绿砖阶段
        # 阶段1：绿砖缩小（做空动能衰竭）
        shrink_count = 0
        j = index - 1
        while j >= 2 and brick[j] < brick[j - 1]:
            green_delta = brick[j - 1] - brick[j]
            prev_delta = brick[j - 2] - brick[j - 1]
            if prev_delta > 0 and green_delta < prev_delta:
                shrink_count += 1
            j -= 1

        # 阶段2：继续向前，找绿砖加大（空头释放）
        expand_count = 0
        while j >= 2 and brick[j] < brick[j - 1]:
            green_delta = brick[j - 1] - brick[j]
            prev_delta = brick[j - 2] - brick[j - 1]
            if prev_delta > 0 and green_delta > prev_delta:
                expand_count += 1
            j -= 1

        if expand_count >= 1 and shrink_count >= 1:
            score = min(1.0, (expand_count + shrink_count) / 4.0)
        else:
            score = 0.0

        return float(score * self.weight_bear_exhaustion)

    # ── 缓存辅助 ──────────────────────────────────────────

    def _get_brick(self, symbol: str, daily_data: pd.DataFrame) -> np.ndarray | None:
        """获取砖形图序列（带缓存）"""
        if symbol in self._brick_cache:
            return self._brick_cache[symbol]

        result = self._calc_brick(daily_data)
        self._brick_cache[symbol] = result
        return result

    def _get_trend(self, symbol: str, daily_data: pd.DataFrame) -> np.ndarray | None:
        """获取短期趋势线序列（带缓存）"""
        if symbol in self._trend_cache:
            return self._trend_cache[symbol]

        result = self._calc_trend(daily_data)
        self._trend_cache[symbol] = result
        return result

    @staticmethod
    def _calc_brick(daily_data: pd.DataFrame) -> np.ndarray | None:
        """计算砖形图指标序列"""
        from app.chart_indicators import compute_brick_indicator

        if not all(col in daily_data.columns for col in ("high", "low", "close")):
            return None

        high = daily_data["high"].values.astype(float)
        low = daily_data["low"].values.astype(float)
        close = daily_data["close"].values.astype(float)

        if len(close) < 4:
            return None

        result = compute_brick_indicator(high, low, close)
        return result["brick"]

    @staticmethod
    def _calc_trend(daily_data: pd.DataFrame) -> np.ndarray | None:
        """计算短期趋势线 EMA(EMA(C,10),10)"""
        from app.chart_indicators import compute_zx_short_trend

        if "close" not in daily_data.columns:
            return None

        close = daily_data["close"].values.astype(float)
        if len(close) < 10:
            return None

        return compute_zx_short_trend(close)


# ── 评分器注册表 ──────────────────────────────────────────

BUY_SCORER_REGISTRY: dict[str, type[BrickBuyScorer]] = {
    "brick": BrickBuyScorer,
}


def create_buy_scorer(name: str, params: dict | None = None) -> BrickBuyScorer | None:
    """根据名称和参数创建评分器实例，返回 None 表示不使用评分"""
    if not name:
        return None

    scorer_class = BUY_SCORER_REGISTRY.get(name)
    if scorer_class is None:
        return None

    if params:
        return scorer_class(**params)
    return scorer_class()
