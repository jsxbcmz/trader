"""曲线形态匹配页面。

输入股票代码 → 取最近N天收盘价 → 与内置砖形图定式例子曲线逐一比对 → 按相似度排名展示。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.widgets import StockChartWidget
from core.screening.curve_match_engine import SIMILARITY_BASELINE

TEMPLATE_WINDOW = 15

BUILTIN_PATTERNS = [
    {"pattern": "N型起跳", "code": "002444", "date": "20251231"},
    {"pattern": "N型起跳", "code": "600693", "date": "20241225"},
    {"pattern": "N型起跳", "code": "000833", "date": "20241105"},
    {"pattern": "N型起跳", "code": "002792", "date": "20251124"},
    {"pattern": "N型起跳", "code": "600366", "date": "20250806"},
    {"pattern": "N型起跳", "code": "601778", "date": "20260403"},
    {"pattern": "横盘起跳", "code": "600893", "date": "20260212"},
    {"pattern": "横盘起跳", "code": "600744", "date": "20260224"},
    {"pattern": "横盘起跳", "code": "600389", "date": "20250815"},
    {"pattern": "横盘起跳", "code": "002846", "date": "20250620"},
    {"pattern": "上升波段延续", "code": "600410", "date": "20250811"},
    {"pattern": "上升波段延续", "code": "600363", "date": "20241029"},
    {"pattern": "上升波段延续", "code": "002402", "date": "20250916"},
    {"pattern": "上升波段延续", "code": "002536", "date": "20260417"},
]


def _znorm_distance(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    a_std, b_std = a.std(), b.std()
    if a_std < 1e-8 or b_std < 1e-8:
        return float("inf")
    a_norm = (a - a.mean()) / a_std
    b_norm = (b - b.mean()) / b_std
    return float(np.linalg.norm(a_norm - b_norm))


class CurveMatchPage(QtWidgets.QWidget):
    """曲线形态匹配页面"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self._stock_daily_data_dir = root / "stock_daily_data"
        self._results: list[dict] = []
        self._input_df: pd.DataFrame | None = None
        self._templates: list[dict] = []

        self._setup_ui()
        self._connect_signals()
        self._preload_templates()

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── 标题栏 ──
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("曲线形态匹配")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()

        header.addWidget(QtWidgets.QLabel("代码:"))
        self._code_input = QtWidgets.QLineEdit()
        self._code_input.setPlaceholderText("如 002444")
        self._code_input.setFixedWidth(80)
        self._code_input.setMaxLength(6)
        header.addWidget(self._code_input)

        self._search_btn = QtWidgets.QPushButton("匹配")
        self._search_btn.setFixedWidth(80)
        self._search_btn.setMinimumHeight(32)
        self._search_btn.setStyleSheet(
            "background-color: #1890FF; color: white; font-weight: bold;"
            "border-radius: 4px; padding: 4px 12px;"
        )
        header.addWidget(self._search_btn)

        self._clear_btn = QtWidgets.QPushButton("清空")
        self._clear_btn.setFixedWidth(60)
        header.addWidget(self._clear_btn)

        main_layout.addLayout(header)

        # ── 模板状态 ──
        self._template_info = QtWidgets.QLabel("")
        self._template_info.setStyleSheet("color: #888; font-size: 12px;")
        main_layout.addWidget(self._template_info)

        # ── 下方 Splitter：结果表格 + 对比图 ──
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # 结果表格
        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["定式", "例子", "例子日期", "相似度", "距离"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setColumnWidth(0, 90)
        self._table.setColumnWidth(1, 65)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 70)

        # 右侧面板：叠加对比 + K线
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # 叠加对比图（双Y轴，原始收盘价）
        self._compare_plot = pg.PlotWidget()
        self._compare_plot.setBackground("#1e1e1e")
        self._compare_plot.showGrid(x=True, y=True, alpha=0.15)
        self._compare_plot.setLabel("left", "定式模板价格")
        self._compare_plot.getAxis("left").setPen(pg.mkPen("#FF6B6B"))
        self._compare_plot.getAxis("left").setTextPen(pg.mkPen("#FF6B6B"))

        self._right_axis = pg.ViewBox()
        self._compare_plot.scene().addItem(self._right_axis)
        self._compare_plot.getAxis("right").linkToView(self._right_axis)
        self._right_axis.setXLink(self._compare_plot)
        self._compare_plot.getAxis("right").setLabel("匹配曲线价格")
        self._compare_plot.getAxis("right").setPen(pg.mkPen("#4ECDC4"))
        self._compare_plot.getAxis("right").setTextPen(pg.mkPen("#4ECDC4"))
        self._compare_plot.showAxis("right")

        self._compare_plot.addLegend(offset=(60, 10))

        self._template_overlay = self._compare_plot.plot(
            [], [], pen=pg.mkPen("#FF6B6B", width=2), name="定式模板"
        )
        self._match_curve = pg.PlotCurveItem(
            pen=pg.mkPen("#4ECDC4", width=2, style=QtCore.Qt.DashLine)
        )
        self._right_axis.addItem(self._match_curve)
        self._compare_plot.plotItem.legend.addItem(self._match_curve, "匹配曲线")

        self._compare_plot.getViewBox().sigResized.connect(self._sync_right_axis)

        right_layout.addWidget(self._compare_plot, 2)

        # K线图
        self._chart = StockChartWidget()
        right_layout.addWidget(self._chart, 3)

        splitter.addWidget(self._table)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter, 1)

        # ── 状态栏 ──
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 12px;")
        main_layout.addWidget(self._status_label)

    def _connect_signals(self):
        self._search_btn.clicked.connect(self._on_search)
        self._code_input.returnPressed.connect(self._on_search)
        self._clear_btn.clicked.connect(self._on_clear)
        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._table.doubleClicked.connect(self._on_row_double_clicked)

    # ── 预加载内置模板 ──

    def _preload_templates(self):
        loaded = 0
        failed = 0
        for p in BUILTIN_PATTERNS:
            try:
                df = load_daily_csv(self._stock_daily_data_dir, p["code"])
            except FileNotFoundError:
                failed += 1
                continue

            target_date = pd.to_datetime(p["date"])
            date_diffs = (df["date"] - target_date).abs()
            closest_idx = int(date_diffs.idxmin())

            start_idx = max(0, closest_idx - TEMPLATE_WINDOW + 1)
            segment = df.iloc[start_idx : closest_idx + 1]

            if len(segment) < 5:
                failed += 1
                continue

            close = segment["close"].values.astype(np.float64)
            dates = [d.strftime("%m-%d") for d in segment["date"]]

            self._templates.append({
                "pattern": p["pattern"],
                "code": p["code"],
                "date": p["date"],
                "close": close,
                "dates": dates,
            })
            loaded += 1

        info = f"已加载 {loaded} 个定式模板"
        if failed:
            info += f"，{failed} 个加载失败(缺数据)"
        self._template_info.setText(info)

    # ── 匹配 ──

    def _on_search(self):
        code = self._code_input.text().strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            QtWidgets.QMessageBox.warning(self, "提示", "请输入6位股票代码")
            return

        if not self._templates:
            QtWidgets.QMessageBox.warning(self, "提示", "没有可用的定式模板")
            return

        try:
            df = load_daily_csv(self._stock_daily_data_dir, code)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self, "提示", f"未找到 {code} 的日线数据")
            return

        if df.empty or len(df) < 5:
            QtWidgets.QMessageBox.warning(self, "提示", f"{code} 数据不足")
            return

        self._input_df = df
        input_close = df["close"].values.astype(np.float64)
        input_dates = df["date"]

        # 取输入股票最近 TEMPLATE_WINDOW 天（以最新日期为右端）
        m = TEMPLATE_WINDOW
        if len(input_close) >= m:
            stock_segment = input_close[-m:]
            stock_start_idx = len(input_close) - m
        else:
            stock_segment = input_close
            stock_start_idx = 0

        stock_dates = [
            d.strftime("%m-%d") if hasattr(d, "strftime") else str(d)[5:10]
            for d in input_dates.iloc[stock_start_idx:]
        ]

        results: list[dict] = []
        for tmpl in self._templates:
            template = tmpl["close"]
            best_dist = _znorm_distance(template, stock_segment)

            if not np.isfinite(best_dist):
                best_dist = float("inf")

            similarity = max(0.0, (1 - best_dist / SIMILARITY_BASELINE)) * 100

            results.append({
                "pattern": tmpl["pattern"],
                "example_code": tmpl["code"],
                "example_date": tmpl["date"],
                "template_close": tmpl["close"],
                "template_dates": tmpl["dates"],
                "stock_close": stock_segment,
                "stock_dates": stock_dates,
                "similarity": round(similarity, 1),
                "distance": round(best_dist, 4) if np.isfinite(best_dist) else 9999.0,
            })

        results.sort(key=lambda r: r["distance"])
        self._results = results
        self._populate_table(results)

        fmt = lambda d: d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        start_d = fmt(input_dates.iloc[stock_start_idx])
        end_d = fmt(input_dates.iloc[-1])
        self._status_label.setText(
            f"匹配完成: {code} ({start_d}~{end_d}) 与 {len(results)} 个定式模板比对"
        )
        self.statusMessageRequested.emit(
            f"曲线匹配完成: {len(results)} 个结果", 3000
        )

    # ── 结果表格 ──

    def _populate_table(self, items: list[dict]):
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(item["pattern"]))
            self._table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(item["example_code"])
            )
            self._table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(item["example_date"])
            )

            sim_item = QtWidgets.QTableWidgetItem(f"{item['similarity']:.1f}%")
            sim_item.setTextAlignment(QtCore.Qt.AlignCenter)
            sim = item["similarity"]
            if sim >= 80:
                sim_item.setForeground(QtGui.QColor("#52C41A"))
            elif sim >= 60:
                sim_item.setForeground(QtGui.QColor("#FAAD14"))
            else:
                sim_item.setForeground(QtGui.QColor("#FF4D4F"))
            self._table.setItem(row, 3, sim_item)

            dist_item = QtWidgets.QTableWidgetItem(f"{item['distance']:.3f}")
            dist_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self._table.setItem(row, 4, dist_item)

        if items:
            self._table.selectRow(0)

    def _on_row_changed(
        self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex
    ):
        row = current.row()
        if row < 0 or row >= len(self._results):
            return
        item = self._results[row]
        self._show_comparison(item)
        self._show_chart(item)

    def _sync_right_axis(self):
        self._right_axis.setGeometry(self._compare_plot.getViewBox().sceneBoundingRect())

    def _show_comparison(self, item: dict):
        template = item["template_close"]
        stock_seg = item["stock_close"]
        tmpl_dates = item["template_dates"]
        stock_dates = item["stock_dates"]

        n = min(len(template), len(stock_seg))
        t_data = template[-n:]
        s_data = stock_seg[-n:]
        t_dates = tmpl_dates[-n:]
        s_dates = stock_dates[-n:]

        x = np.arange(n)

        self._template_overlay.setData(x, t_data)
        self._match_curve.setData(x, s_data)

        # X轴：上方模板日期、下方股票日期
        ticks = []
        step = max(1, n // 5)
        for i in range(0, n, step):
            ticks.append((float(i), f"{s_dates[i]}\n{t_dates[i]}"))
        if (n - 1) % step != 0:
            ticks.append((float(n - 1), f"{s_dates[-1]}\n{t_dates[-1]}"))
        self._compare_plot.getAxis("bottom").setTicks([ticks])

        self._compare_plot.setTitle(
            f"红: {item['pattern']}({item['example_code']} ~{item['example_date']})  "
            f"绿: 输入股票近期",
            size="10pt",
        )

        self._compare_plot.setXRange(0, n - 1, padding=0.05)

        t_margin = (t_data.max() - t_data.min()) * 0.1 or 1.0
        self._compare_plot.setYRange(
            t_data.min() - t_margin, t_data.max() + t_margin, padding=0
        )

        m_margin = (s_data.max() - s_data.min()) * 0.1 or 1.0
        self._right_axis.setYRange(
            s_data.min() - m_margin, s_data.max() + m_margin, padding=0
        )
        self._sync_right_axis()

    def _show_chart(self, item: dict):
        if self._input_df is None:
            return

        df_full = self._input_df.copy().reset_index(drop=True)
        self._chart.set_daily(df_full)

        last_idx = len(df_full) - 1
        half_width = self._chart._item_half_width
        right_padding = self._chart._right_view_padding
        x_right = last_idx + half_width + right_padding + 10
        visible_days = min(last_idx + 1, 120)
        x_left = last_idx - visible_days + 1 - half_width
        self._chart.pricePlot.setXRange(x_left, x_right, padding=0)

    def _on_row_double_clicked(self, index: QtCore.QModelIndex):
        row = index.row()
        if row < 0 or row >= len(self._results):
            return

        item = self._results[row]
        if self._input_df is None:
            return

        df_full = self._input_df.copy().reset_index(drop=True)
        code = self._code_input.text().strip().zfill(6)

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(
            f"{code}  定式: {item['pattern']}({item['example_code']})  "
            f"相似度: {item['similarity']:.1f}%"
        )
        dialog.resize(1100, 700)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)

        chart = StockChartWidget()
        chart.set_daily(df_full)

        last_idx = len(df_full) - 1
        half_width = chart._item_half_width
        right_padding = chart._right_view_padding
        x_right = last_idx + half_width + right_padding + 10
        visible_days = min(last_idx + 1, 120)
        x_left = last_idx - visible_days + 1 - half_width
        chart.pricePlot.setXRange(x_left, x_right, padding=0)

        layout.addWidget(chart)
        dialog.exec()

    # ── 清空 ──

    def _on_clear(self):
        self._table.setRowCount(0)
        self._results = []
        self._input_df = None
        self._template_overlay.setData([], [])
        self._match_curve.setData([], [])
        self._status_label.setText("")
        self._compare_plot.setTitle("")
