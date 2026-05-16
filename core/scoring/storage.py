"""主板评分系统的评分明细 + TopK 存档（P0-3 + P0-4）。

存档结构：
    output/scoring_daily/{YYYY-MM-DD}.json   全部命中票评分明细（机器读）
    output/scoring_picks/{YYYY-MM-DD}.json   当日 Top K 候选（人类读）

格式选型：用 JSON 而非 parquet — 数据量小（每天 < 200 条），零新依赖，调试方便。
未来 IC 计算如成为瓶颈再迁移。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from core.models.brick_pattern import BrickPatternMatch


SCORING_OUTPUT_SUBDIR = "output"


@dataclass(frozen=True)
class ScoringRecord:
    """单条评分记录（落盘单位）。"""
    symbol: str
    name: str
    date: str
    pattern: str
    total_score: float
    grade: str
    specific_score: float
    common_score: float
    macd_score: float
    signal_score: float
    risk_penalty: float
    items: dict = field(default_factory=dict)
    regime: str = ""
    bonus_score: float = 0.0
    bonus_items: dict = field(default_factory=dict)


def _output_dir(root: Path, sub: str) -> Path:
    d = root / SCORING_OUTPUT_SUBDIR / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def match_to_record(match: BrickPatternMatch) -> ScoringRecord:
    bd = match.score_breakdown
    items: dict = {}
    bonus_items: dict = {}
    if bd is not None:
        items.update(bd.specific_items)
        items.update(bd.common_items)
        items.update(bd.macd_items)
        items.update(bd.signal_items)
        items.update(bd.risk_items)
        bonus_items = dict(bd.bonus_items)
    return ScoringRecord(
        symbol=match.symbol,
        name=match.name,
        date=match.actual_date or match.target_date,
        pattern=match.matched_pattern,
        total_score=float(match.final_score),
        grade=match.grade,
        specific_score=float(bd.specific_score) if bd else 0.0,
        common_score=float(bd.common_score) if bd else 0.0,
        macd_score=float(bd.macd_score) if bd else 0.0,
        signal_score=float(bd.signal_score) if bd else 0.0,
        risk_penalty=float(bd.risk_penalty) if bd else 0.0,
        items=items,
        bonus_score=float(bd.bonus_score) if bd else 0.0,
        bonus_items=bonus_items,
    )


def save_scoring_daily(
    root: Path,
    date: str,
    matches: Iterable[BrickPatternMatch],
) -> Path:
    """P0-3：写入 scoring_daily/{date}.json。仅存命中票。"""
    records = [match_to_record(m) for m in matches if m.final_matched]
    records.sort(key=lambda r: r.total_score, reverse=True)
    path = _output_dir(root, "scoring_daily") / f"{date}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in records], f, ensure_ascii=False, indent=2)
    return path


def load_scoring_daily(root: Path, date: str) -> list[ScoringRecord]:
    path = root / SCORING_OUTPUT_SUBDIR / "scoring_daily" / f"{date}.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [ScoringRecord(**item) for item in data]


def save_scoring_picks(
    root: Path,
    date: str,
    matches: Iterable[BrickPatternMatch],
    k: int = 20,
    regime: str = "",
) -> Path:
    """P0-4：写入 scoring_picks/{date}.json（Top K 人类可读 JSON）。"""
    matched_list = sorted(
        [m for m in matches if m.final_matched],
        key=lambda m: m.final_score,
        reverse=True,
    )[:k]

    picks = []
    for rank, m in enumerate(matched_list, start=1):
        bd = m.score_breakdown
        breakdown = {}
        if bd is not None:
            sub_items = {}
            sub_items.update(bd.specific_items)
            sub_items.update(bd.common_items)
            sub_items.update(bd.macd_items)
            sub_items.update(bd.signal_items)
            sub_items.update(bd.risk_items)
            sub_items.update(bd.bonus_items)
            breakdown = {
                "定式专属": float(bd.specific_score),
                "通用质量": float(bd.common_score),
                "MACD辅助": float(bd.macd_score),
                "信号强度": float(bd.signal_score),
                "战法加分": float(bd.bonus_score),
                "风险扣分": float(bd.risk_penalty),
                "子项明细": sub_items,
            }
        picks.append({
            "rank": rank,
            "symbol": m.symbol,
            "name": m.name,
            "total": float(m.final_score),
            "grade": m.grade,
            "pattern": m.matched_pattern,
            "breakdown": breakdown,
        })

    output = {
        "date": date,
        "regime": regime,
        "k": k,
        "picks": picks,
    }
    path = _output_dir(root, "scoring_picks") / f"{date}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return path


def load_scoring_picks(root: Path, date: str) -> dict:
    """读 picks（返回原始字典，含 date/regime/k/picks 字段）。"""
    path = root / SCORING_OUTPUT_SUBDIR / "scoring_picks" / f"{date}.json"
    if not path.exists():
        return {"date": date, "regime": "", "k": 0, "picks": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
