"""截面分位计算（P1-1）。

对全主板每只票计算 3 个待归一化因子的当日原始值，再用 `pd.rank(pct=True)`
得到截面分位（0~1），缓存到 scoring.db 的 cross_section 表。

3 个待归一化因子：
1. 信号日涨幅 day_change      = (close[i] - close[i-1]) / close[i-1] * 100
2. 翻红力度比 force_ratio     = (brick[i] - brick[i-1]) / max(|brick[i-1]-brick[i-2]|, 2.0)
3. 短趋斜率 short_trend_slope = polyfit(short_trend[i-9..i], 1) / close[i] * 100

下游（P1-2 scoring.py）读分位数据把绝对阈值改成"分位 → 分数"查表。
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.screening.brick_pattern.helpers import _calc_indicators
from core.scoring.main_board_pool import MainBoardPool


CS_COLUMNS = (
    "symbol",
    "day_change", "day_change_pct",
    "force_ratio", "force_ratio_pct",
    "short_trend_slope", "short_trend_slope_pct",
)


# ── Worker（在子进程跑）────────────────────────────────────


def _cs_worker(args: tuple) -> dict:
    root_str, symbol, target_date = args
    try:
        repo = StockRepository(Path(root_str))
        df = repo.get_daily_frame(symbol)
        if df.empty or len(df) < 12:
            return {"symbol": symbol, "error": "数据不足"}

        time_result = locate_time_index(df, target_date)
        if not time_result.matched or time_result.index is None:
            return {"symbol": symbol, "error": "日期未匹配"}
        i = time_result.index
        if i < 2:
            return {"symbol": symbol, "error": "index<2"}

        indicators = _calc_indicators(df)
        close = indicators["close"]
        brick = indicators["brick"]
        short_trend = indicators["short_trend"]

        # 信号日涨幅
        prev_close = close[i - 1]
        day_change = (close[i] - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

        # 翻红力度比
        delta_today = brick[i] - brick[i - 1]
        delta_yesterday = abs(brick[i - 1] - brick[i - 2])
        divisor = max(abs(delta_yesterday), 2.0)
        force_ratio = delta_today / divisor

        # 短趋斜率
        trend_window = 10
        start = max(0, i - trend_window + 1)
        slice_ = short_trend[start: i + 1]
        valid_mask = np.isfinite(slice_)
        if np.sum(valid_mask) >= 3:
            valid_trend = slice_[valid_mask]
            x_vals = np.arange(len(valid_trend), dtype=float)
            slope = np.polyfit(x_vals, valid_trend, 1)[0]
            price_ref = close[i] if close[i] > 0 else 1
            short_trend_slope = slope / price_ref * 100
        else:
            short_trend_slope = 0.0

        return {
            "symbol": symbol,
            "day_change": float(day_change),
            "force_ratio": float(force_ratio),
            "short_trend_slope": float(short_trend_slope),
        }
    except Exception as exc:
        return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}


# ── 主类 ────────────────────────────────────────────────


@dataclass
class CrossSectionStats:
    repository: StockRepository
    main_board_pool: MainBoardPool = field(init=False)
    max_workers: int = 8

    def __post_init__(self):
        self.main_board_pool = MainBoardPool(repository=self.repository)

    @classmethod
    def from_root(cls, root: Path) -> "CrossSectionStats":
        return cls(repository=StockRepository(root=root))

    def compute(self, target_date: str) -> pd.DataFrame:
        """计算 target_date 的全主板 3 个因子的原始值 + 分位。"""
        candidates = self.main_board_pool.list_active()
        root_str = str(self.repository.root)
        task_args = [(root_str, s.symbol, target_date) for s in candidates]

        rows: list[dict] = []
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_cs_worker, a): a[1] for a in task_args}
            for future in as_completed(futures):
                rows.append(future.result())

        # 过滤掉 error 行
        valid_rows = [r for r in rows if "error" not in r]
        if not valid_rows:
            return pd.DataFrame(columns=list(CS_COLUMNS))

        df = pd.DataFrame(valid_rows)
        # pct rank
        df["day_change_pct"] = df["day_change"].rank(pct=True)
        df["force_ratio_pct"] = df["force_ratio"].rank(pct=True)
        df["short_trend_slope_pct"] = df["short_trend_slope"].rank(pct=True)

        df = df[list(CS_COLUMNS)].sort_values("symbol").reset_index(drop=True)
        return df

    def save(self, target_date: str, df: pd.DataFrame):
        from core.data.database import get_scoring_db
        scoring_db = get_scoring_db()
        scoring_db.save_cross_section(target_date, df)

    def compute_and_save(self, target_date: str) -> pd.DataFrame:
        df = self.compute(target_date)
        self.save(target_date, df)
        return df


# ── IO ─────────────────────────────────────────────────


def load_cross_section(root: Path, date: str) -> pd.DataFrame:
    """从数据库读取已缓存的截面分位。symbol 列保持字符串（6 位补零）。"""
    from core.data.database import get_scoring_db
    scoring_db = get_scoring_db()
    df = scoring_db.load_cross_section(date)
    if df.empty:
        return pd.DataFrame(columns=list(CS_COLUMNS))
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def get_symbol_pcts(df: pd.DataFrame, symbol: str) -> dict[str, float] | None:
    """从截面 DataFrame 取某只票的 3 个分位值。找不到返回 None。"""
    if df.empty:
        return None
    sym = symbol.zfill(6)
    match = df[df["symbol"] == sym]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "day_change_pct": float(row["day_change_pct"]),
        "force_ratio_pct": float(row["force_ratio_pct"]),
        "short_trend_slope_pct": float(row["short_trend_slope_pct"]),
    }
