"""砖形图定式验证页：相似定式后台 Worker。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6 import QtCore

from app.data_loader import load_daily_csv, load_stock_list
from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
)

from .helpers import (
    DATE_RANGE_END,
    DATE_RANGE_START,
    _GRADE_ORDER,
    _PATTERN_DETECTORS,
    _build_score_tooltip,
    _is_feature_similar,
)


class SimilarPatternWorker(QtCore.QObject):
    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(
        self,
        stock_daily_data_dir: Path,
        stocklist_csv: Path,
        pattern_type: PatternType,
        exclude_symbol: str,
        exclude_date: str,
        ref_extra: dict | None = None,
        ref_grade: str = "",
    ):
        super().__init__()
        self._stock_daily_data_dir = stock_daily_data_dir
        self._stocklist_csv = stocklist_csv
        self._pattern_type = pattern_type
        self._exclude_symbol = exclude_symbol
        self._exclude_date = exclude_date
        self._ref_extra = ref_extra or {}
        self._ref_grade = ref_grade
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @QtCore.Slot()
    def run(self):
        try:
            stock_df = load_stock_list(self._stocklist_csv)
            symbols = stock_df["symbol"].tolist()
            names = dict(zip(stock_df["symbol"], stock_df["name"]))
            total = len(symbols)
            detector = _PATTERN_DETECTORS[self._pattern_type]
            results: list[dict] = []
            progress_interval = 20

            # 允许的最大评分等级差距：参考等级 ±1 级
            ref_grade_idx = _GRADE_ORDER.get(self._ref_grade, 2)
            min_grade_idx = max(0, ref_grade_idx - 1)
            max_grade_idx = min(4, ref_grade_idx + 1)

            for idx, symbol in enumerate(symbols):
                if self._cancelled:
                    break

                try:
                    df = load_daily_csv(self._stock_daily_data_dir, symbol)
                except Exception:
                    if (idx + 1) % progress_interval == 0 or idx + 1 == total:
                        self.progressChanged.emit({
                            "current": idx + 1, "total": total,
                            "symbol": symbol, "found": len(results),
                        })
                    continue

                if df.empty or len(df) < 10:
                    if (idx + 1) % progress_interval == 0 or idx + 1 == total:
                        self.progressChanged.emit({
                            "current": idx + 1, "total": total,
                            "symbol": symbol, "found": len(results),
                        })
                    continue

                dates = pd.to_datetime(df["date"], errors="coerce")
                mask = (dates >= DATE_RANGE_START) & (dates <= DATE_RANGE_END)
                scan_indices = df.index[mask].tolist()

                if not scan_indices:
                    if (idx + 1) % progress_interval == 0 or idx + 1 == total:
                        self.progressChanged.emit({
                            "current": idx + 1, "total": total,
                            "symbol": symbol, "found": len(results),
                        })
                    continue

                indicators = _calc_indicators(df)

                for i in scan_indices:
                    if self._cancelled:
                        break

                    date_val = dates.iloc[i]
                    if pd.isna(date_val):
                        continue
                    date_str = date_val.strftime("%Y-%m-%d")

                    if symbol == self._exclude_symbol and date_str == self._exclude_date:
                        continue

                    prereq_ok, _ = check_prerequisites(indicators, i)
                    if not prereq_ok:
                        continue

                    result = detector(indicators, i)
                    if not result.matched:
                        continue

                    if not _is_feature_similar(
                        self._pattern_type, self._ref_extra, result.extra,
                    ):
                        continue

                    common_score, common_items = compute_common_quality_score(
                        indicators, i, self._pattern_type,
                    )
                    macd_score, macd_items = compute_macd_auxiliary_score(
                        indicators, i, self._pattern_type,
                    )
                    risk_penalty, risk_items, risk_details_list = compute_risk_penalty(
                        indicators, i, self._pattern_type,
                    )
                    signal_score, signal_items = compute_signal_strength_score(
                        indicators, i,
                    )
                    bd = ScoreBreakdown(
                        specific_score=result.score,
                        specific_items=result.extra.get("specific_items", {}),
                        common_score=common_score,
                        common_items=common_items,
                        macd_score=macd_score,
                        macd_items=macd_items,
                        signal_score=signal_score,
                        signal_items=signal_items,
                        risk_penalty=risk_penalty,
                        risk_items=risk_items,
                    )

                    cand_grade_idx = _GRADE_ORDER.get(bd.grade, 4)
                    if cand_grade_idx < min_grade_idx or cand_grade_idx > max_grade_idx:
                        continue

                    if bd.risk_penalty == 0:
                        risk_text = "无风险"
                    else:
                        risk_text = f"{bd.risk_level}({bd.risk_penalty:.0f})"

                    detail_parts = [result.description]
                    triggered = [r for r in risk_details_list if r.triggered]
                    if triggered:
                        risk_descs = "; ".join(r.description for r in triggered)
                        detail_parts.append(f"风险: {risk_descs}")

                    results.append({
                        "symbol": symbol,
                        "name": names.get(symbol, ""),
                        "date": date_str,
                        "score": bd.final_score,
                        "grade": bd.grade,
                        "risk": risk_text,
                        "detail": " | ".join(detail_parts),
                        "tooltip": _build_score_tooltip(bd),
                    })

                if (idx + 1) % progress_interval == 0 or idx + 1 == total:
                    self.progressChanged.emit({
                        "current": idx + 1, "total": total,
                        "symbol": symbol, "found": len(results),
                    })

            results.sort(key=lambda r: r["score"], reverse=True)
            self.finished.emit({
                "results": results,
                "pattern_type": self._pattern_type.value,
            })
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

