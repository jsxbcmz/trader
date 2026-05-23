"""OAMV 阶段标签生成器（P3-1）。

读 `stock_daily_data/oamv_930903_CSI.csv`，对每个交易日打 "bull"/"bear" 阶段标签。

算法：
1. 原始方向 raw_phase = "bull" if close >= MA20(close) else "bear"
2. 平滑过滤：smoothed_phase 只在"过去连续 3 日 raw 同向"时切换，否则沿用前日 smoothed
   避免每周抖动（数据探查显示连续段中位仅 4~5 天，需平滑窗）

阶段二分（多头/空头）是 MVP；进阶四象限（快/慢 × 涨/跌）留 P3-9。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.data.io import load_oamv_csv


SMOOTH_WINDOW = 3
MA_WINDOW = 20
SLOPE_SHORT = 5
SLOPE_LONG = 20


@dataclass
class RegimeRecord:
    date: str
    raw_phase: str         # "bull" / "bear"（无平滑）
    smoothed_phase: str    # "bull" / "bear"（平滑后；P3 实际生效的标签）
    close: float
    ma20: float
    slope5: float          # 相对斜率（% / 日）
    slope20: float
    # 四象限预备字段（P3-9 启用，MVP 不读）
    tempo: str = ""        # "fast" / "slow"


def _rolling_slope(values: np.ndarray, window: int) -> np.ndarray:
    """每个 i 算 [i-window+1..i] 的线性回归斜率，归一化到 % / 日。"""
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        y = values[i - window + 1: i + 1]
        x = np.arange(window, dtype=float)
        m = float(y.mean())
        if m > 0:
            out[i] = float(np.polyfit(x, y, 1)[0] / m * 100)
    return out


def compute_regime_series(root: Path) -> list[RegimeRecord]:
    """从 OAMV CSV 算出全历史阶段序列。"""
    df = load_oamv_csv(root / "stock_daily_data")
    if df.empty:
        return []
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"].values.astype(float)
    ma20 = pd.Series(close).rolling(MA_WINDOW).mean().values
    slope5 = _rolling_slope(close, SLOPE_SHORT)
    slope20 = _rolling_slope(close, SLOPE_LONG)

    # 1. 原始阶段
    raw_phases: list[str] = []
    for i in range(len(df)):
        if np.isnan(ma20[i]):
            raw_phases.append("")
            continue
        raw_phases.append("bull" if close[i] >= ma20[i] else "bear")

    # 2. 平滑过滤：连续 SMOOTH_WINDOW 日 raw 同向才切换
    smoothed: list[str] = [""] * len(df)
    for i in range(len(df)):
        if not raw_phases[i]:
            smoothed[i] = ""
            continue
        prev_smoothed = smoothed[i - 1] if i > 0 else ""
        if not prev_smoothed:
            # 第一个有效日：直接用 raw
            smoothed[i] = raw_phases[i]
            continue
        if raw_phases[i] == prev_smoothed:
            smoothed[i] = prev_smoothed
            continue
        # raw 与 smoothed 不一致 → 检查是否连续 SMOOTH_WINDOW 日 raw 都同向
        start = max(0, i - SMOOTH_WINDOW + 1)
        recent = raw_phases[start: i + 1]
        if len(recent) >= SMOOTH_WINDOW and all(r == raw_phases[i] for r in recent):
            smoothed[i] = raw_phases[i]  # 确认切换
        else:
            smoothed[i] = prev_smoothed  # 沿用

    # 3. 节奏（四象限预备，MVP 不参与）
    tempos: list[str] = []
    for i in range(len(df)):
        if np.isnan(slope5[i]) or np.isnan(slope20[i]) or not smoothed[i]:
            tempos.append("")
            continue
        if smoothed[i] == "bull":
            tempos.append("fast" if slope5[i] > slope20[i] else "slow")
        else:
            tempos.append("fast" if slope5[i] < slope20[i] else "slow")

    records: list[RegimeRecord] = []
    for i in range(len(df)):
        if not smoothed[i]:
            continue
        records.append(RegimeRecord(
            date=df["date"].iloc[i].strftime("%Y-%m-%d"),
            raw_phase=raw_phases[i],
            smoothed_phase=smoothed[i],
            close=float(close[i]),
            ma20=float(ma20[i]),
            slope5=float(slope5[i]) if not np.isnan(slope5[i]) else 0.0,
            slope20=float(slope20[i]) if not np.isnan(slope20[i]) else 0.0,
            tempo=tempos[i],
        ))
    return records


@dataclass
class RegimeAnalyzer:
    root: Path
    _series: list[RegimeRecord] | None = None

    def _ensure(self) -> list[RegimeRecord]:
        if self._series is None:
            self._series = compute_regime_series(self.root)
        return self._series

    @classmethod
    def from_root(cls, root: Path) -> "RegimeAnalyzer":
        return cls(root=root)

    def get_regime(self, date: str) -> Optional[RegimeRecord]:
        for rec in self._ensure():
            if rec.date == date:
                return rec
        return None

    def get_series(self, start: str, end: str) -> list[RegimeRecord]:
        return [r for r in self._ensure() if start <= r.date <= end]

    def save_for_date(self, date: str) -> Optional[Path]:
        """单日落盘 JSON。找不到日期返回 None。"""
        rec = self.get_regime(date)
        if rec is None:
            return None
        out_dir = self.root / "output" / "scoring_regime"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{date}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(rec), f, ensure_ascii=False, indent=2)
        return path


def load_regime(root: Path, date: str) -> Optional[RegimeRecord]:
    """读取已落盘的单日 regime。"""
    path = root / "output" / "scoring_regime" / f"{date}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return RegimeRecord(**data)
