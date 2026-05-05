"""砖形图交易定式批量验证页面 V2。

独立页面：预填充文档中的案例数据，支持批量验证和手动添加，
展示每条数据的验证结论（定式匹配、V2评分等级、评分分解）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv, load_stock_list
from app.utils.thread_manager import start_worker
from app.widgets import StockChartWidget
from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)

# ── 文档中的默认案例数据 ──────────────────────────────────────
DEFAULT_CASES: list[tuple[str, str, str]] = [
    # N型起跳
    ("002444", "20251231", "N型起跳"),
    ("600693", "20241225", "N型起跳"),
    ("000833", "20241105", "N型起跳"),
    ("002792", "20251124", "N型起跳"),
    ("600366", "20250806", "N型起跳"),
    ("601778", "20260403", "N型起跳"),
    # 横盘起跳
    ("600893", "20260212", "横盘起跳"),
    ("600744", "20260224", "横盘起跳"),
    ("600389", "20250815", "横盘起跳"),
    ("002846", "20250620", "横盘起跳"),
    # 上升波段延续
    ("600410", "20250811", "上升波段延续"),
    ("600363", "20241029", "上升波段延续"),
    ("002402", "20250916", "上升波段延续"),
    ("002536", "20260417", "上升波段延续"),
]

GRADE_COLORS = {
    "S": "#D5F5D5",
    "A": "#F6FFED",
    "B": "#FFFBE6",
    "C": "#FFF1E6",
    "D": "#FFF1F0",
}


def _format_date(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _build_score_tooltip(breakdown: ScoreBreakdown) -> str:
    """构建评分分解的tooltip文本"""
    lines = [f"最终得分: {breakdown.final_score:.0f} ({breakdown.grade}级)"]
    base_parts = f"专属{breakdown.specific_score:.0f} + 通用{breakdown.common_score:.0f} + MACD{breakdown.macd_score:.0f} + 信号{breakdown.signal_score:.0f}"
    lines.append(f"基础分: {breakdown.base_score:.0f} = {base_parts}")
    lines.append("")

    lines.append(f"── 定式专属 ({breakdown.specific_score:.0f}/30) ──")
    for k, v in breakdown.specific_items.items():
        lines.append(f"  {k}: {v:+.0f}" if v < 0 else f"  {k}: {v:.0f}")

    lines.append(f"── 通用质量 ({breakdown.common_score:.0f}/30) ──")
    for k, v in breakdown.common_items.items():
        lines.append(f"  {k}: {v:.0f}")

    lines.append(f"── MACD环境 ({breakdown.macd_score:.0f}/25) ──")
    for k, v in breakdown.macd_items.items():
        lines.append(f"  {k}: {v:+.0f}" if v < 0 else f"  {k}: {v:.0f}")

    if breakdown.signal_items:
        lines.append(f"── 信号强度 ({breakdown.signal_score:.0f}/15) ──")
        for k, v in breakdown.signal_items.items():
            lines.append(f"  {k}: {v:.0f}")

    if breakdown.risk_penalty != 0:
        lines.append(f"── 风险扣分 ({breakdown.risk_penalty:.0f}) ──")
        for k, v in breakdown.risk_items.items():
            lines.append(f"  {k}: {v:.0f}")

    return "\n".join(lines)


_PATTERN_DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}

DATE_RANGE_START = "2024-01-01"
DATE_RANGE_END = "2026-03-31"


_GRADE_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}


def _is_feature_similar(
    pattern_type: PatternType,
    ref_extra: dict,
    candidate_extra: dict,
) -> bool:
    """判断候选结果的特征是否与参考特征相似。

    按定式类型使用不同的关键特征进行范围匹配。
    """
    if pattern_type == PatternType.N_SHAPE_JUMP:
        ref_j = ref_extra.get("kdj_j", 50)
        cand_j = candidate_extra.get("kdj_j", 50)
        # J值同区间: <0 / 0~20 / 20~40
        ref_band = 0 if ref_j < 0 else (1 if ref_j < 20 else 2)
        cand_band = 0 if cand_j < 0 else (1 if cand_j < 20 else 2)
        if abs(ref_band - cand_band) > 1:
            return False

        ref_green = ref_extra.get("max_green_segment", 0)
        cand_green = candidate_extra.get("max_green_segment", 0)
        if ref_green != cand_green:
            return False

        ref_vs = ref_extra.get("vs_short_trend", 0)
        cand_vs = candidate_extra.get("vs_short_trend", 0)
        # 价格vs短趋势偏离差距不超过5%
        if abs(ref_vs - cand_vs) > 5:
            return False

    elif pattern_type == PatternType.SIDEWAYS_JUMP:
        ref_sw = ref_extra.get("switches", 0)
        cand_sw = candidate_extra.get("switches", 0)
        # 切换次数差距不超过2
        if abs(ref_sw - cand_sw) > 2:
            return False

        ref_amp = ref_extra.get("amplitude", 0)
        cand_amp = candidate_extra.get("amplitude", 0)
        # 振幅差距不超过5%
        if abs(ref_amp - cand_amp) > 5:
            return False

        ref_jump = ref_extra.get("brick_jump", 0)
        cand_jump = candidate_extra.get("brick_jump", 0)
        # 跳升幅度比值在 0.5~2.0 之间
        if ref_jump > 0 and cand_jump > 0:
            ratio = cand_jump / ref_jump
            if ratio < 0.5 or ratio > 2.0:
                return False

    elif pattern_type == PatternType.UPTREND_CONTINUE:
        ref_red = ref_extra.get("red_count", 0)
        cand_red = candidate_extra.get("red_count", 0)
        # 红砖数差距不超过3
        if abs(ref_red - cand_red) > 3:
            return False

        ref_green = ref_extra.get("green_count", 0)
        cand_green = candidate_extra.get("green_count", 0)
        # 绿砖数差距不超过1
        if abs(ref_green - cand_green) > 1:
            return False

        ref_bv = ref_extra.get("brick_val", 0)
        cand_bv = candidate_extra.get("brick_val", 0)
        # 砖值差距不超过30
        if abs(ref_bv - cand_bv) > 30:
            return False

    return True


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


class SimilarSearchProgressDialog(QtWidgets.QDialog):
    stopRequested = QtCore.Signal()

    def __init__(self, pattern_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"查找相似例子 - {pattern_name}")
        self.resize(420, 160)
        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始搜索...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.statsLabel = QtWidgets.QLabel("已处理: 0  已找到: 0")

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        self.stopButton = QtWidgets.QPushButton("停止")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)
        btn_layout.addWidget(self.stopButton)
        btn_layout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.statsLabel)
        layout.addStretch()
        layout.addLayout(btn_layout)

        self.stopButton.clicked.connect(self._on_stop)
        self.closeButton.clicked.connect(self.accept)

    def _on_stop(self):
        self.stopButton.setEnabled(False)
        self.progressLabel.setText("正在停止...")
        self.stopRequested.emit()

    def update_progress(self, payload: dict):
        current = payload.get("current", 0)
        total = max(payload.get("total", 1), 1)
        found = payload.get("found", 0)
        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))
        self.progressLabel.setText(f"搜索进度: {current} / {total}")
        self.statsLabel.setText(f"已处理: {current}  已找到: {found}")

    def mark_finished(self, summary: str = ""):
        self.stopButton.setEnabled(False)
        self.closeButton.setEnabled(True)
        if summary:
            self.progressLabel.setText(summary)


class SimilarPatternResultDialog(QtWidgets.QDialog):
    def __init__(self, results: list[dict], pattern_name: str,
                 stock_daily_data_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"相似例子 - {pattern_name} (共 {len(results)} 条)")
        self.resize(1300, 800)
        self._stock_daily_data_dir = stock_daily_data_dir
        self._results = results

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self._table = QtWidgets.QTableWidget()
        headers = ["代码", "名称", "日期", "评分", "等级", "风险", "详情"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setColumnWidth(0, 65)
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 60)
        self._table.setColumnWidth(4, 45)
        self._table.setColumnWidth(5, 100)

        self._table.setRowCount(len(results))
        for row, r in enumerate(results):
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(r["symbol"]))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(r["name"]))
            self._table.setItem(row, 2, QtWidgets.QTableWidgetItem(r["date"]))
            score_item = QtWidgets.QTableWidgetItem(f"{r['score']:.0f}")
            score_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self._table.setItem(row, 3, score_item)
            grade_item = QtWidgets.QTableWidgetItem(r["grade"])
            grade_item.setTextAlignment(QtCore.Qt.AlignCenter)
            bg_color = GRADE_COLORS.get(r["grade"], "#FFF")
            grade_item.setBackground(QtGui.QColor(bg_color))
            self._table.setItem(row, 4, grade_item)
            self._table.setItem(row, 5, QtWidgets.QTableWidgetItem(r.get("risk", "")))
            self._table.setItem(row, 6, QtWidgets.QTableWidgetItem(r.get("detail", "")))
            tooltip = r.get("tooltip", "")
            if tooltip:
                for col in range(len(headers)):
                    item = self._table.item(row, col)
                    if item:
                        item.setToolTip(tooltip)

        self._chart = StockChartWidget()

        splitter.addWidget(self._table)
        splitter.addWidget(self._chart)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)

        if results:
            self._table.selectRow(0)

    def _on_row_changed(self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex):
        row = current.row()
        if row < 0 or row >= len(self._results):
            return
        r = self._results[row]
        self._load_chart(r["symbol"], r["date"])

    def _load_chart(self, symbol: str, target_date: str):
        try:
            df = load_daily_csv(self._stock_daily_data_dir, symbol)
        except FileNotFoundError:
            return
        if df.empty:
            return

        df_full = df.copy().reset_index(drop=True)
        target_index = None
        for i, row_data in df_full.iterrows():
            date_val = row_data["date"]
            date_str = (
                date_val.strftime("%Y-%m-%d")
                if hasattr(date_val, "strftime")
                else str(date_val)[:10]
            )
            if date_str <= target_date:
                target_index = i
            else:
                break

        if target_index is None:
            return

        self._chart.set_daily(df_full)

        half_width = self._chart._item_half_width
        right_padding = self._chart._right_view_padding
        x_right = target_index + half_width + right_padding
        visible_days = min(target_index + 1, 120)
        x_left = target_index - visible_days + 1 - half_width
        self._chart.pricePlot.setXRange(x_left, x_right, padding=0)


class BrickPatternPage(QtWidgets.QWidget):
    """砖形图交易定式批量验证页面"""

    statusMessageRequested = QtCore.Signal(str, int)

    COL_CODE = 0
    COL_DATE = 1
    COL_EXPECTED = 2
    COL_PREREQ = 3
    COL_MATCHED = 4
    COL_SCORE = 5
    COL_RISK = 6
    COL_DETAIL = 7
    COLUMN_HEADERS = ["代码", "日期", "期望定式", "前提", "匹配定式", "评分", "风险", "详情"]

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.repository = StockRepository(root)
        self._setup_ui()
        self._connect_signals()
        self._load_default_cases()

    # ── UI 构建 ──────────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # ── 顶部标题栏 ──
        header_row = QtWidgets.QHBoxLayout()

        title = QtWidgets.QLabel("砖形图定式批量验证")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        header_row.addWidget(title)

        header_row.addStretch()

        header_row.addWidget(QtWidgets.QLabel("代码:"))
        self.add_code_input = QtWidgets.QLineEdit()
        self.add_code_input.setPlaceholderText("如 002444")
        self.add_code_input.setFixedWidth(80)
        self.add_code_input.setMaxLength(6)
        header_row.addWidget(self.add_code_input)

        header_row.addWidget(QtWidgets.QLabel("日期:"))
        self.add_date_input = QtWidgets.QLineEdit()
        self.add_date_input.setPlaceholderText("如 20251231")
        self.add_date_input.setFixedWidth(100)
        self.add_date_input.setMaxLength(8)
        self.add_date_input.setText(QtCore.QDate.currentDate().toString("yyyyMMdd"))
        header_row.addWidget(self.add_date_input)

        header_row.addWidget(QtWidgets.QLabel("期望:"))
        self.add_expected_combo = QtWidgets.QComboBox()
        self.add_expected_combo.addItems(["", "N型起跳", "横盘起跳", "上升波段延续"])
        self.add_expected_combo.setFixedWidth(100)
        header_row.addWidget(self.add_expected_combo)

        self.add_btn = QtWidgets.QPushButton("+ 添加")
        self.add_btn.setFixedWidth(70)
        header_row.addWidget(self.add_btn)

        main_layout.addLayout(header_row)

        # ── 操作按钮栏 ──
        btn_row = QtWidgets.QHBoxLayout()

        self.verify_all_btn = QtWidgets.QPushButton("批量验证全部")
        self.verify_all_btn.setMinimumHeight(36)
        self.verify_all_btn.setStyleSheet(
            "background-color: #1890FF; color: white; font-weight: bold; font-size: 13px;"
            "border-radius: 4px; padding: 4px 16px;"
        )
        btn_row.addWidget(self.verify_all_btn)

        self.reset_btn = QtWidgets.QPushButton("重置为默认")
        self.reset_btn.setMinimumHeight(36)
        btn_row.addWidget(self.reset_btn)

        self.clear_results_btn = QtWidgets.QPushButton("清空结果")
        self.clear_results_btn.setMinimumHeight(36)
        btn_row.addWidget(self.clear_results_btn)

        self.delete_selected_btn = QtWidgets.QPushButton("删除选中行")
        self.delete_selected_btn.setMinimumHeight(36)
        btn_row.addWidget(self.delete_selected_btn)

        btn_row.addStretch()

        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setStyleSheet("font-size: 13px; color: #666;")
        btn_row.addWidget(self.stats_label)

        main_layout.addLayout(btn_row)

        # ── 结果表格 ──
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(len(self.COLUMN_HEADERS))
        self.table.setHorizontalHeaderLabels(self.COLUMN_HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)

        self.table.setColumnWidth(self.COL_CODE, 70)
        self.table.setColumnWidth(self.COL_DATE, 90)
        self.table.setColumnWidth(self.COL_EXPECTED, 90)
        self.table.setColumnWidth(self.COL_PREREQ, 60)
        self.table.setColumnWidth(self.COL_MATCHED, 90)
        self.table.setColumnWidth(self.COL_SCORE, 80)
        self.table.setColumnWidth(self.COL_RISK, 100)

        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        main_layout.addWidget(self.table, 1)

        self._similar_thread: QtCore.QThread | None = None
        self._similar_worker: SimilarPatternWorker | None = None
        self._similar_progress_dialog: SimilarSearchProgressDialog | None = None

    def _connect_signals(self):
        self.verify_all_btn.clicked.connect(self._on_verify_all)
        self.reset_btn.clicked.connect(self._load_default_cases)
        self.clear_results_btn.clicked.connect(self._clear_results)
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        self.add_btn.clicked.connect(self._on_add_case)
        self.add_date_input.returnPressed.connect(self._on_add_case)
        self.table.doubleClicked.connect(self._on_row_double_clicked)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

    # ── 数据管理 ─────────────────────────────────────────────

    def _load_default_cases(self):
        self.table.setRowCount(0)
        for code, date_raw, expected in DEFAULT_CASES:
            self._append_row(code, _format_date(date_raw), expected)
        self._update_stats()

    def _append_row(self, code: str, date_str: str, expected: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, self.COL_CODE, QtWidgets.QTableWidgetItem(code.zfill(6)))
        self.table.setItem(row, self.COL_DATE, QtWidgets.QTableWidgetItem(date_str))
        self.table.setItem(row, self.COL_EXPECTED, QtWidgets.QTableWidgetItem(expected))
        for col in (self.COL_PREREQ, self.COL_MATCHED, self.COL_SCORE, self.COL_RISK, self.COL_DETAIL):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(""))

    def _on_add_case(self):
        code = self.add_code_input.text().strip()
        date_raw = self.add_date_input.text().strip()

        if not code:
            QtWidgets.QMessageBox.warning(self, "提示", "请输入股票代码")
            return
        if not date_raw or len(date_raw) != 8 or not date_raw.isdigit():
            QtWidgets.QMessageBox.warning(self, "提示", "请输入8位日期，如 20251231")
            return

        expected = self.add_expected_combo.currentText()
        self._append_row(code.zfill(6), _format_date(date_raw), expected)
        self.add_code_input.clear()
        self._update_stats()

    def _delete_selected(self):
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self._update_stats()

    def _clear_results(self):
        for row in range(self.table.rowCount()):
            for col in (self.COL_PREREQ, self.COL_MATCHED, self.COL_SCORE, self.COL_RISK, self.COL_DETAIL):
                item = self.table.item(row, col)
                if item:
                    item.setText("")
                    item.setToolTip("")
                    item.setForeground(QtGui.QColor("#000"))
                    item.setData(QtCore.Qt.UserRole, None)
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(QtGui.QColor("#FFF"))
        self._update_stats()

    # ── 批量验证 ─────────────────────────────────────────────

    def _on_verify_all(self):
        total = self.table.rowCount()
        if total == 0:
            QtWidgets.QMessageBox.information(self, "提示", "没有待验证的数据")
            return

        self.verify_all_btn.setEnabled(False)
        self.verify_all_btn.setText("验证中...")

        self._verify_index = 0
        self._verify_stats = {"pass": 0, "fail": 0, "risk": 0, "error": 0}
        QtCore.QTimer.singleShot(10, self._verify_next_row)

    def _verify_next_row(self):
        row = self._verify_index
        total = self.table.rowCount()

        if row >= total:
            self._on_verify_complete()
            return

        code = self.table.item(row, self.COL_CODE).text().strip()
        date_str = self.table.item(row, self.COL_DATE).text().strip()

        self._execute_single_verify(row, code, date_str)

        self._verify_index += 1
        self.verify_all_btn.setText(f"验证中... ({self._verify_index}/{total})")

        QtCore.QTimer.singleShot(5, self._verify_next_row)

    def _on_verify_complete(self):
        self.verify_all_btn.setEnabled(True)
        self.verify_all_btn.setText("批量验证全部")
        self._update_stats()

        stats = self._verify_stats
        self.statusMessageRequested.emit(
            f"批量验证完成：通过{stats['pass']} 风险{stats['risk']} "
            f"不符{stats['fail']} 错误{stats['error']}",
            5000,
        )

    def _execute_single_verify(self, row: int, code: str, target_date: str):
        """对单行执行V2验证并填充结果"""
        code = code.zfill(6)

        try:
            df = self.repository.get_daily_frame(code)
        except FileNotFoundError:
            self._set_row_error(row, f"未找到{code}数据")
            return

        if df.empty:
            self._set_row_error(row, "数据为空")
            return

        time_result = locate_time_index(df, target_date)
        if not time_result.matched or time_result.index is None:
            self._set_row_error(row, f"日期未匹配: {time_result.reason}")
            return

        index = time_result.index
        if len(df) < 10:
            self._set_row_error(row, "数据不足")
            return

        indicators = _calc_indicators(df)

        # ── 前提检测 ──
        prereq_passed, prereq_detail = check_prerequisites(indicators, index)

        if not prereq_passed:
            self._set_row_result(
                row,
                prereq="X",
                matched="--",
                score="--",
                risk="--",
                detail=f"前提不满足: {prereq_detail}",
                row_color="#FFF1F0",
            )
            self._verify_stats["fail"] += 1
            return

        # ── 定式检测 ──
        result_n = detect_n_shape_jump(indicators, index)
        result_sideways = detect_sideways_jump(indicators, index)
        result_uptrend = detect_uptrend_continue(indicators, index)

        matched_results = [r for r in (result_n, result_sideways, result_uptrend) if r.matched]

        if not matched_results:
            details = []
            if result_n.description:
                details.append(f"N型:{result_n.description}")
            if result_sideways.description:
                details.append(f"横盘:{result_sideways.description}")
            if result_uptrend.description:
                details.append(f"延续:{result_uptrend.description}")

            self._set_row_result(
                row,
                prereq="OK",
                matched="无匹配",
                score="--",
                risk="--",
                detail=" | ".join(details) if details else "不符合任何定式",
                row_color="#FFF1F0",
            )
            self._verify_stats["fail"] += 1
            return

        # ── V2 评分：对每个匹配的定式计算完整分数，取最高 ──
        best_match = None
        best_breakdown = None
        best_final = -1.0
        best_risk_details = []

        for match_r in matched_results:
            specific_score = match_r.score
            specific_items = match_r.extra.get("specific_items", {})

            common_score, common_items = compute_common_quality_score(
                indicators, index, match_r.pattern_type,
            )

            macd_score, macd_items = compute_macd_auxiliary_score(
                indicators, index, match_r.pattern_type,
            )

            risk_penalty, risk_items, risk_details_list = compute_risk_penalty(
                indicators, index, match_r.pattern_type,
            )

            signal_score, signal_items = compute_signal_strength_score(
                indicators, index,
            )

            breakdown = ScoreBreakdown(
                specific_score=specific_score,
                specific_items=specific_items,
                common_score=common_score,
                common_items=common_items,
                macd_score=macd_score,
                macd_items=macd_items,
                signal_score=signal_score,
                signal_items=signal_items,
                risk_penalty=risk_penalty,
                risk_items=risk_items,
            )

            if breakdown.final_score > best_final:
                best_final = breakdown.final_score
                best_breakdown = breakdown
                best_match = match_r
                best_risk_details = risk_details_list

        grade = best_breakdown.grade
        final_score = best_breakdown.final_score
        risk_level = best_breakdown.risk_level

        # 评分列显示
        score_text = f"{final_score:.0f} ({grade})"

        # 风险列显示
        if best_breakdown.risk_penalty == 0:
            risk_text = "无风险"
        else:
            risk_text = f"{risk_level}({best_breakdown.risk_penalty:.0f})"

        # 详情
        all_matched_names = ", ".join(r.pattern_type.value for r in matched_results)
        detail_parts = [best_match.description]
        if len(matched_results) > 1:
            detail_parts.append(f"同时匹配: {all_matched_names}")

        triggered_risks = [r for r in best_risk_details if r.triggered]
        if triggered_risks:
            risk_descs = "; ".join(r.description for r in triggered_risks)
            detail_parts.append(f"风险: {risk_descs}")

        row_color = GRADE_COLORS.get(grade, "#FFF")

        self._set_row_result(
            row,
            prereq="OK",
            matched=best_match.pattern_type.value,
            score=score_text,
            risk=risk_text,
            detail=" | ".join(detail_parts),
            row_color=row_color,
            breakdown=best_breakdown,
        )

        matched_item = self.table.item(row, self.COL_MATCHED)
        if matched_item:
            matched_item.setData(QtCore.Qt.UserRole, {
                "pattern_type": best_match.pattern_type.value,
                "extra": best_match.extra,
                "grade": grade,
            })

        if triggered_risks:
            self._verify_stats["risk"] += 1
        else:
            self._verify_stats["pass"] += 1

    # ── 表格辅助 ─────────────────────────────────────────────

    def _set_row_result(
        self,
        row: int,
        prereq: str,
        matched: str,
        score: str,
        risk: str,
        detail: str,
        row_color: str = "",
        breakdown: ScoreBreakdown | None = None,
    ):
        self.table.item(row, self.COL_PREREQ).setText(prereq)
        self.table.item(row, self.COL_MATCHED).setText(matched)
        self.table.item(row, self.COL_SCORE).setText(score)
        self.table.item(row, self.COL_RISK).setText(risk)
        self.table.item(row, self.COL_DETAIL).setText(detail)

        if row_color:
            bg = QtGui.QColor(row_color)
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(bg)

        # 评分列tooltip
        if breakdown:
            tooltip = _build_score_tooltip(breakdown)
            score_item = self.table.item(row, self.COL_SCORE)
            if score_item:
                score_item.setToolTip(tooltip)
            detail_item = self.table.item(row, self.COL_DETAIL)
            if detail_item:
                detail_item.setToolTip(tooltip)

        # 匹配列颜色
        matched_item = self.table.item(row, self.COL_MATCHED)
        expected_item = self.table.item(row, self.COL_EXPECTED)
        if matched_item and expected_item:
            expected = expected_item.text().strip()
            actual = matched_item.text().strip()
            if actual and actual != "--" and actual != "无匹配":
                if expected and actual == expected:
                    matched_item.setForeground(QtGui.QColor("#52C41A"))
                elif expected and actual != expected:
                    matched_item.setForeground(QtGui.QColor("#FAAD14"))
                else:
                    matched_item.setForeground(QtGui.QColor("#1890FF"))

    def _set_row_error(self, row: int, message: str):
        self.table.item(row, self.COL_PREREQ).setText("ERR")
        self.table.item(row, self.COL_MATCHED).setText("--")
        self.table.item(row, self.COL_SCORE).setText("--")
        self.table.item(row, self.COL_RISK).setText("--")
        self.table.item(row, self.COL_DETAIL).setText(message)

        bg = QtGui.QColor("#FFF2E8")
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setBackground(bg)

        self._verify_stats["error"] += 1

    def _update_stats(self):
        total = self.table.rowCount()
        verified = 0
        for row in range(total):
            prereq_item = self.table.item(row, self.COL_PREREQ)
            if prereq_item and prereq_item.text().strip():
                verified += 1
        self.stats_label.setText(f"共 {total} 条 | 已验证 {verified} 条")

    # ── 双击弹窗查看图表 ─────────────────────────────────────

    def _on_row_double_clicked(self, index: QtCore.QModelIndex):
        row = index.row()
        code_item = self.table.item(row, self.COL_CODE)
        date_item = self.table.item(row, self.COL_DATE)
        if not code_item or not date_item:
            return

        code = code_item.text().strip().zfill(6)
        target_date = date_item.text().strip()

        try:
            stock_daily_data_dir = self.root / "stock_daily_data"
            df = load_daily_csv(stock_daily_data_dir, code)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self, "提示", f"未找到 {code} 的日线数据")
            return

        if df.empty:
            QtWidgets.QMessageBox.warning(self, "提示", f"{code} 数据为空")
            return

        df_full = df.copy().reset_index(drop=True)

        target_index = None
        for i, row_data in df_full.iterrows():
            date_val = row_data["date"]
            date_str = (
                date_val.strftime("%Y-%m-%d")
                if hasattr(date_val, "strftime")
                else str(date_val)[:10]
            )
            if date_str <= target_date:
                target_index = i
            else:
                break

        if target_index is None:
            QtWidgets.QMessageBox.warning(
                self, "提示", f"{code} 在 {target_date} 之前无数据",
            )
            return

        expected_item = self.table.item(row, self.COL_EXPECTED)
        expected = expected_item.text().strip() if expected_item else ""
        matched_item = self.table.item(row, self.COL_MATCHED)
        matched = matched_item.text().strip() if matched_item else ""
        score_item = self.table.item(row, self.COL_SCORE)
        score_text = score_item.text().strip() if score_item else ""

        dialog_title = f"{code} @ {target_date}"
        if expected:
            dialog_title += f"  期望: {expected}"
        if matched and matched not in ("--", "无匹配"):
            dialog_title += f"  匹配: {matched}"
        if score_text and score_text != "--":
            dialog_title += f"  {score_text}"

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(dialog_title)
        dialog.resize(1100, 700)

        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(4, 4, 4, 4)

        chart = StockChartWidget()
        chart.set_daily(df_full)

        half_width = chart._item_half_width
        right_padding = chart._right_view_padding
        x_right = target_index + half_width + right_padding
        visible_days = min(target_index + 1, 120)
        x_left = target_index - visible_days + 1 - half_width

        chart.pricePlot.setXRange(x_left, x_right, padding=0)

        dialog_layout.addWidget(chart)

        dialog.exec()

    # ── 右键菜单：查找相似例子 ───────────────────────────────

    def _on_table_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()

        matched_item = self.table.item(row, self.COL_MATCHED)
        if not matched_item:
            return
        matched_text = matched_item.text().strip()
        if not matched_text or matched_text in ("--", "无匹配"):
            return

        user_data = matched_item.data(QtCore.Qt.UserRole)
        if not user_data or not isinstance(user_data, dict):
            return

        menu = QtWidgets.QMenu(self)
        find_action = menu.addAction("查找相似例子")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == find_action:
            self._start_similar_search(row, user_data)

    def _start_similar_search(self, row: int, user_data: dict):
        pattern_value = user_data.get("pattern_type", "")
        try:
            pattern_type = PatternType(pattern_value)
        except ValueError:
            return

        code_item = self.table.item(row, self.COL_CODE)
        date_item = self.table.item(row, self.COL_DATE)
        exclude_symbol = code_item.text().strip().zfill(6) if code_item else ""
        exclude_date = date_item.text().strip() if date_item else ""

        self._similar_worker = SimilarPatternWorker(
            stock_daily_data_dir=self.root / "stock_daily_data",
            stocklist_csv=self.root / "stocklist.csv",
            pattern_type=pattern_type,
            exclude_symbol=exclude_symbol,
            exclude_date=exclude_date,
            ref_extra=user_data.get("extra", {}),
            ref_grade=user_data.get("grade", ""),
        )

        self._similar_progress_dialog = SimilarSearchProgressDialog(
            pattern_value, parent=self,
        )
        self._similar_progress_dialog.stopRequested.connect(self._similar_worker.cancel)

        self._similar_thread = start_worker(
            self,
            self._similar_worker,
            on_progress=self._on_similar_progress,
            on_finished=self._on_similar_finished,
            on_error=self._on_similar_error,
        )

        self._similar_progress_dialog.show()

    def _on_similar_progress(self, payload: dict):
        if self._similar_progress_dialog:
            self._similar_progress_dialog.update_progress(payload)

    def _on_similar_finished(self, payload: dict):
        if self._similar_progress_dialog:
            results = payload.get("results", [])
            pattern_name = payload.get("pattern_type", "")
            self._similar_progress_dialog.mark_finished(
                f"搜索完成，共找到 {len(results)} 条相似例子",
            )
            self._similar_progress_dialog.accept()
            self._similar_progress_dialog = None

            if not results:
                QtWidgets.QMessageBox.information(
                    self, "查找相似例子",
                    f"未找到与 {pattern_name} 相似的例子",
                )
                return

            dialog = SimilarPatternResultDialog(
                results=results,
                pattern_name=pattern_name,
                stock_daily_data_dir=self.root / "stock_daily_data",
                parent=self,
            )
            dialog.exec()

    def _on_similar_error(self, error_msg: str):
        if self._similar_progress_dialog:
            self._similar_progress_dialog.mark_finished(f"搜索出错: {error_msg}")
        QtWidgets.QMessageBox.warning(self, "错误", f"查找相似例子失败：{error_msg}")
