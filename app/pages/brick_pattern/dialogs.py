"""砖形图定式验证页：进度/结果/回测明细对话框。"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.widgets import StockChartWidget
from core.models.brick_pattern import ScoreBreakdown

from .helpers import (
    GRADE_COLORS,
    _build_backtest_tooltip,
    _calc_percentile,
    _find_score_range,
)


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


class BacktestDetailDialog(QtWidgets.QDialog):
    def __init__(self, symbol: str, backtest_data: dict, current_score: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{symbol} 历史回测详情")
        self.resize(900, 600)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QtWidgets.QLabel(
            f"股票 {symbol}  当前评分: {current_score}  "
            f"历史命中总计: {backtest_data.get('total_signals', 0)}次"
        )
        header_font = header.font()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # ── 各等级统计表 ──
        grade_group = QtWidgets.QGroupBox("各评分等级胜率统计")
        grade_layout = QtWidgets.QVBoxLayout(grade_group)
        grade_table = QtWidgets.QTableWidget()
        grade_headers = ["等级", "命中次数", "T+1胜率", "T+1均值", "T+2胜率", "T+2均值"]
        grade_table.setColumnCount(len(grade_headers))
        grade_table.setHorizontalHeaderLabels(grade_headers)
        grade_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        grade_table.horizontalHeader().setStretchLastSection(True)
        grade_table.verticalHeader().setVisible(False)
        grade_table.setAlternatingRowColors(True)

        by_grade = backtest_data.get("by_grade", {})
        grade_table.setRowCount(5)
        for row_idx, g in enumerate(("S", "A", "B", "C", "D")):
            gs = by_grade.get(g, {})
            cnt = gs.get("count", 0)
            grade_table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(g))
            grade_table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(cnt)))
            if cnt > 0:
                grade_table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(f"{gs['t1_win_rate']:.1f}%"))
                grade_table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(f"{gs['t1_mean']:+.2f}%"))
                grade_table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem(f"{gs['t2_win_rate']:.1f}%"))
                grade_table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem(f"{gs['t2_mean']:+.2f}%"))
            else:
                for c in range(2, 6):
                    grade_table.setItem(row_idx, c, QtWidgets.QTableWidgetItem("--"))

        grade_table.setMaximumHeight(180)
        grade_layout.addWidget(grade_table)
        layout.addWidget(grade_group)

        # ── 分数区间统计表 ──
        by_sr = backtest_data.get("by_score_range", {})
        if by_sr:
            sr_group = QtWidgets.QGroupBox("各分数区间胜率统计")
            sr_layout = QtWidgets.QVBoxLayout(sr_group)
            sr_table = QtWidgets.QTableWidget()
            sr_headers = ["分数区间", "命中次数", "T+1胜率", "T+1均值", "T+2胜率", "T+2均值"]
            sr_table.setColumnCount(len(sr_headers))
            sr_table.setHorizontalHeaderLabels(sr_headers)
            sr_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            sr_table.horizontalHeader().setStretchLastSection(True)
            sr_table.verticalHeader().setVisible(False)
            sr_table.setAlternatingRowColors(True)

            score_ranges = ["0-30", "30-40", "40-55", "55-70", "70-85", "85-101"]
            filled = [(k, by_sr[k]) for k in score_ranges if k in by_sr]
            sr_table.setRowCount(len(filled))
            for row_idx, (rng, st) in enumerate(filled):
                sr_table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(f"[{rng})"))
                sr_table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(st["count"])))
                sr_table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(f"{st['t1_win_rate']:.1f}%"))
                sr_table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(f"{st['t1_mean']:+.2f}%"))
                sr_table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem(f"{st['t2_win_rate']:.1f}%"))
                sr_table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem(f"{st['t2_mean']:+.2f}%"))

            sr_table.setMaximumHeight(min(180, 30 + len(filled) * 28))
            sr_layout.addWidget(sr_table)
            layout.addWidget(sr_group)

        # ── 历史命中明细表 ──
        detail_group = QtWidgets.QGroupBox("历史命中明细")
        detail_layout = QtWidgets.QVBoxLayout(detail_group)
        detail_table = QtWidgets.QTableWidget()
        detail_headers = ["日期", "定式类型", "评分", "等级", "T+1收益", "T+2收益"]
        detail_table.setColumnCount(len(detail_headers))
        detail_table.setHorizontalHeaderLabels(detail_headers)
        detail_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        detail_table.horizontalHeader().setStretchLastSection(True)
        detail_table.verticalHeader().setVisible(False)
        detail_table.setAlternatingRowColors(True)
        detail_table.setColumnWidth(0, 100)
        detail_table.setColumnWidth(1, 100)
        detail_table.setColumnWidth(2, 60)
        detail_table.setColumnWidth(3, 50)
        detail_table.setColumnWidth(4, 90)
        detail_table.setColumnWidth(5, 90)

        records = backtest_data.get("records", [])
        records_sorted = sorted(records, key=lambda r: r["date"], reverse=True)
        detail_table.setRowCount(len(records_sorted))
        for row_idx, rec in enumerate(records_sorted):
            detail_table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(rec["date"]))
            detail_table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(rec["pattern"]))
            score_item = QtWidgets.QTableWidgetItem(f"{rec['score']:.0f}")
            score_item.setTextAlignment(QtCore.Qt.AlignCenter)
            detail_table.setItem(row_idx, 2, score_item)
            grade_item = QtWidgets.QTableWidgetItem(rec["grade"])
            grade_item.setTextAlignment(QtCore.Qt.AlignCenter)
            bg = GRADE_COLORS.get(rec["grade"], "#FFF")
            grade_item.setBackground(QtGui.QColor(bg))
            detail_table.setItem(row_idx, 3, grade_item)

            for col, val in [(4, rec["ret_t1"]), (5, rec["ret_t2"])]:
                if math.isnan(val):
                    detail_table.setItem(row_idx, col, QtWidgets.QTableWidgetItem("--"))
                else:
                    ret_item = QtWidgets.QTableWidgetItem(f"{val:+.2f}%")
                    ret_item.setForeground(
                        QtGui.QColor("#FF4D4F") if val < 0 else QtGui.QColor("#52C41A"),
                    )
                    detail_table.setItem(row_idx, col, ret_item)

        detail_layout.addWidget(detail_table)
        layout.addWidget(detail_group, 1)

