"""T+1 / T+2 / T+3 三窗口实盘回填（P0-5）。

每天调用一次 `OutcomesFiller.fill_for_today(today)`：扫 today-1/today-2/today-3
的 scoring_picks，把 today 的实际收益与是否绿砖写进对应 scoring_outcomes 文件。
属于增量更新 — 第 1/2/3 天分别写 t1/t2/t3 列。

存储格式：CSV（扁平时序结构，便于 P2 阶段 IC 计算多日 pd.concat 聚合）。
交易日历参考：用 000001（平安银行）的日线序列，主板长期未停牌。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

OUTCOMES_COLUMNS = (
    "symbol",
    "score_date",
    "t1_return", "t1_is_green",
    "t2_return", "t2_is_green",
    "t3_return", "t3_is_green",
)

from core.data.repository import StockRepository
from core.screening.brick_pattern.helpers import (
    _calc_indicators,
    _is_green_brick,
)


CALENDAR_REFERENCE_SYMBOL = "000001"
WINDOW_DAYS = (1, 2, 3)


@dataclass
class OutcomeRecord:
    """单只票在某 score_date 的三窗口回填结果。None 表示尚未回填。"""
    symbol: str
    score_date: str
    t1_return: Optional[float] = None
    t1_is_green: Optional[bool] = None
    t2_return: Optional[float] = None
    t2_is_green: Optional[bool] = None
    t3_return: Optional[float] = None
    t3_is_green: Optional[bool] = None


@dataclass
class OutcomesFiller:
    repository: StockRepository
    _calendar: list[pd.Timestamp] = field(default_factory=list, init=False)

    @classmethod
    def from_root(cls, root: Path) -> "OutcomesFiller":
        return cls(repository=StockRepository(root=root))

    @property
    def root(self) -> Path:
        return self.repository.root

    def _get_calendar(self) -> list[pd.Timestamp]:
        if not self._calendar:
            df = self.repository.get_daily_frame(CALENDAR_REFERENCE_SYMBOL)
            self._calendar = pd.to_datetime(df["date"]).dt.normalize().sort_values().tolist()
        return self._calendar

    def fill_for_today(self, today: str) -> dict[str, Path]:
        """以 today 为参考点回填过去 3 个交易日的 outcomes。

        返回 {score_date: 写入路径}。
        """
        calendar = self._get_calendar()
        today_ts = pd.Timestamp(today).normalize()
        try:
            today_idx = calendar.index(today_ts)
        except ValueError:
            return {}

        results: dict[str, Path] = {}
        for n in WINDOW_DAYS:
            score_idx = today_idx - n
            if score_idx < 0:
                continue
            score_date = calendar[score_idx].strftime("%Y-%m-%d")
            path = self._fill_window(score_date=score_date, fill_date=today, window=n)
            if path is not None:
                results[score_date] = path
        return results

    def _fill_window(self, score_date: str, fill_date: str, window: int) -> Optional[Path]:
        picks = _load_picks(self.root, score_date)
        if not picks:
            return None
        existing = _load_outcomes(self.root, score_date)

        for pick in picks:
            sym = pick["symbol"]
            rec = existing.get(sym) or OutcomeRecord(symbol=sym, score_date=score_date)
            ret, is_green = self._compute_window(sym, score_date, fill_date)
            if window == 1:
                rec.t1_return = ret
                rec.t1_is_green = is_green
            elif window == 2:
                rec.t2_return = ret
                rec.t2_is_green = is_green
            elif window == 3:
                rec.t3_return = ret
                rec.t3_is_green = is_green
            existing[sym] = rec

        return _save_outcomes(self.root, score_date, existing)

    def _compute_window(
        self,
        symbol: str,
        score_date: str,
        fill_date: str,
    ) -> tuple[Optional[float], Optional[bool]]:
        """计算 fill_date 当日涨跌幅（相对前一交易日收盘）+ 是否绿砖。

        注：从 v2 开始改为"当日涨跌"语义（不再是 score_date 到 fill_date 的累计）。
        score_date 参数保留是为签名兼容（外部按 score_date 索引 picks）。
        """
        df = self.repository.get_daily_frame(symbol)
        if df.empty:
            return None, None
        df = df.sort_values("date").reset_index(drop=True)
        dates = pd.to_datetime(df["date"]).dt.normalize()

        fill_ts = pd.Timestamp(fill_date).normalize()
        fill_mask = dates == fill_ts
        if not fill_mask.any():
            return None, None

        fill_idx = int(df.index[fill_mask][0])
        if fill_idx < 1:
            return None, None

        c_prev = float(df.loc[fill_idx - 1, "close"])
        c_fill = float(df.loc[fill_idx, "close"])
        ret = (c_fill - c_prev) / c_prev if c_prev > 0 else None

        indicators = _calc_indicators(df)
        is_green = bool(_is_green_brick(indicators["brick"], fill_idx))

        return ret, is_green


# ── 文件 IO ───────────────────────────────────────────────


def _load_picks(root: Path, date: str) -> list[dict]:
    path = root / "output" / "scoring_picks" / f"{date}.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f).get("picks", [])


def _outcomes_path(root: Path, date: str) -> Path:
    out_dir = root / "output" / "scoring_outcomes"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{date}.csv"


def _parse_optional_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    return float(v)


def _parse_optional_bool(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def _load_outcomes(root: Path, date: str) -> dict[str, OutcomeRecord]:
    path = _outcomes_path(root, date)
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"symbol": str, "score_date": str})
    out: dict[str, OutcomeRecord] = {}
    for _, row in df.iterrows():
        sym = str(row["symbol"]).zfill(6)
        out[sym] = OutcomeRecord(
            symbol=sym,
            score_date=str(row["score_date"]),
            t1_return=_parse_optional_float(row.get("t1_return")),
            t1_is_green=_parse_optional_bool(row.get("t1_is_green")),
            t2_return=_parse_optional_float(row.get("t2_return")),
            t2_is_green=_parse_optional_bool(row.get("t2_is_green")),
            t3_return=_parse_optional_float(row.get("t3_return")),
            t3_is_green=_parse_optional_bool(row.get("t3_is_green")),
        )
    return out


def _save_outcomes(root: Path, date: str, records: dict[str, OutcomeRecord]) -> Path:
    path = _outcomes_path(root, date)
    rows = [asdict(r) for r in records.values()]
    df = pd.DataFrame(rows, columns=list(OUTCOMES_COLUMNS))
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def load_outcomes(root: Path, date: str) -> list[OutcomeRecord]:
    """读取已落盘的 outcomes（供 UI / IC 计算使用）。"""
    return list(_load_outcomes(root, date).values())
