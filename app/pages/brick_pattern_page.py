"""砖形图交易定式批量验证页面 V2。

独立页面：预填充文档中的案例数据，支持批量验证和手动添加，
展示每条数据的验证结论（定式匹配、V2评分等级、评分分解）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.widgets import StockChartWidget
from core.data.repository import StockRepository
from core.data.time_index import locate_time_index
from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_risk_penalty,
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
    lines.append(f"基础分: {breakdown.base_score:.0f} = 专属{breakdown.specific_score:.0f} + 通用{breakdown.common_score:.0f}")
    lines.append("")

    lines.append(f"── 定式专属 ({breakdown.specific_score:.0f}/70) ──")
    for k, v in breakdown.specific_items.items():
        lines.append(f"  {k}: {v:+.0f}" if v < 0 else f"  {k}: {v:.0f}")

    lines.append(f"── 通用质量 ({breakdown.common_score:.0f}/30) ──")
    for k, v in breakdown.common_items.items():
        lines.append(f"  {k}: {v:.0f}")

    if breakdown.risk_penalty != 0:
        lines.append(f"── 风险扣分 ({breakdown.risk_penalty:.0f}) ──")
        for k, v in breakdown.risk_items.items():
            lines.append(f"  {k}: {v:.0f}")

    return "\n".join(lines)


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

        main_layout.addWidget(self.table, 1)

    def _connect_signals(self):
        self.verify_all_btn.clicked.connect(self._on_verify_all)
        self.reset_btn.clicked.connect(self._load_default_cases)
        self.clear_results_btn.clicked.connect(self._clear_results)
        self.delete_selected_btn.clicked.connect(self._delete_selected)
        self.add_btn.clicked.connect(self._on_add_case)
        self.add_date_input.returnPressed.connect(self._on_add_case)
        self.table.doubleClicked.connect(self._on_row_double_clicked)

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

            risk_penalty, risk_items, risk_details_list = compute_risk_penalty(
                indicators, index, match_r.pattern_type,
            )

            breakdown = ScoreBreakdown(
                specific_score=specific_score,
                specific_items=specific_items,
                common_score=common_score,
                common_items=common_items,
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
