"""统计页：详情/预览对话框。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.widgets import StockChartWidget

from .constants import OPERATION_MAP


class RateDetailDialog(QtWidgets.QDialog):
    """展示某只股票各操作类型下用户的收益率详情，支持类型切换"""

    def __init__(
        self,
        stock_code: str,
        stock_name: str,
        initial_op: str,
        all_stock_records: list[dict],
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._all_records = all_stock_records
        self._current_op = initial_op

        # 按操作类型分组
        self._op_groups: dict[str, list[dict]] = {}
        for item in all_stock_records:
            op = str(item.get("op", "0"))
            self._op_groups.setdefault(op, []).append(item)

        self._update_current_data()

        self.setWindowTitle(f"{stock_code} {stock_name} — 收益详情")
        self.resize(960, 560)
        self.setModal(True)
        self._setup_ui()

    def _update_current_data(self):
        records = self._op_groups.get(self._current_op, [])
        self._user_rates = sorted(records, key=lambda x: x.get("rate", 0), reverse=True)

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background: #0f172a;
            }
            QLabel {
                color: #e2e8f0;
            }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ── 顶部：操作类型切换标签 ──
        op_bar = QtWidgets.QHBoxLayout()
        op_bar.setSpacing(8)
        op_label = QtWidgets.QLabel("操作类型：")
        op_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #94a3b8;")
        op_bar.addWidget(op_label)

        self._op_tag_container = QtWidgets.QHBoxLayout()
        self._op_tag_container.setSpacing(6)
        self._build_op_tags()
        op_bar.addLayout(self._op_tag_container)
        op_bar.addStretch()
        main_layout.addLayout(op_bar)

        # ── 内容区：左表格 + 右分布图 ──
        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(16)

        # ── 左侧：表格 ──
        left_panel = QtWidgets.QVBoxLayout()
        table_title = QtWidgets.QLabel("📋 各用户收益率")
        table_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #94a3b8;")
        left_panel.addWidget(table_title)

        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["用户", "收益率", "仓位占比", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet("""
            QTableWidget {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 10px;
                color: #e2e8f0;
                font-size: 13px;
            }
            QTableWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid #1a2536;
            }
            QTableWidget::item:selected {
                background: #263348;
                color: #e2e8f0;
            }
            QHeaderView::section {
                background: #0f172a;
                color: #94a3b8;
                padding: 10px 12px;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #334155;
            }
        """)
        self._fill_table()
        left_panel.addWidget(self._table)
        content_layout.addLayout(left_panel, 1)

        # ── 右侧：正态分布图 ──
        right_panel = QtWidgets.QVBoxLayout()
        chart_title = QtWidgets.QLabel("📈 收益率分布")
        chart_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #94a3b8;")
        right_panel.addWidget(chart_title)

        self._chart_widget = pg.PlotWidget()
        self._chart_widget.setBackground("#1e293b")
        self._chart_widget.setStyleSheet("border: 1px solid #334155; border-radius: 10px;")
        self._chart_widget.getPlotItem().hideAxis("left")
        self._chart_widget.getPlotItem().getAxis("bottom").setPen(pg.mkPen("#94a3b8"))
        self._chart_widget.getPlotItem().getAxis("bottom").setTextPen(pg.mkPen("#94a3b8"))
        self._chart_widget.getPlotItem().getAxis("bottom").setLabel("收益率 (%)")
        self._draw_distribution()
        right_panel.addWidget(self._chart_widget)
        content_layout.addLayout(right_panel, 1)

        main_layout.addLayout(content_layout)

        # ── 底部关闭按钮 ──
        close_button = QtWidgets.QPushButton("关闭")
        close_button.setFixedWidth(100)
        close_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        close_button.setStyleSheet("""
            QPushButton {
                background: #334155;
                color: #e2e8f0;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #475569;
            }
        """)
        close_button.clicked.connect(self.accept)
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        main_layout.addLayout(button_layout)

    # ── 操作类型标签 ──

    def _build_op_tags(self):
        while self._op_tag_container.count():
            child = self._op_tag_container.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        sorted_ops = sorted(
            self._op_groups.keys(),
            key=lambda op: OPERATION_SORT_ORDER.get(op, 99),
        )
        for op_code in sorted_ops:
            count = len(self._op_groups[op_code])
            op_info = OPERATION_MAP.get(op_code, OPERATION_MAP["0"])
            is_active = (op_code == self._current_op)

            tag = QtWidgets.QPushButton(f"{op_info['label']} ({count})")
            tag.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            tag.setFixedHeight(28)

            border = "border: 2px solid #e2e8f0;" if is_active else "border: 2px solid transparent;"
            tag.setStyleSheet(f"""
                QPushButton {{
                    background: {op_info['background']};
                    color: {op_info['color']};
                    padding: 3px 14px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: 600;
                    {border}
                }}
                QPushButton:hover {{ opacity: 0.85; }}
            """)
            tag.clicked.connect(lambda _checked=False, oc=op_code: self._switch_op(oc))
            self._op_tag_container.addWidget(tag)

    def _switch_op(self, op_code: str):
        if op_code == self._current_op:
            return
        self._current_op = op_code
        self._update_current_data()
        self._fill_table()
        self._chart_widget.clear()
        self._draw_distribution()
        self._build_op_tags()

    # ── 表格填充 ──

    def _fill_table(self):
        self._table.setRowCount(len(self._user_rates))
        for row, item in enumerate(self._user_rates):
            rate_value = item.get("rate", 0)
            rate_percent = rate_value * 100
            position_percent = item.get("position_percent", 0) * 100

            user_item = QtWidgets.QTableWidgetItem(str(item.get("user_key", "")))
            user_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            user_item.setForeground(QtGui.QColor("#e2e8f0"))
            self._table.setItem(row, 0, user_item)

            rate_text = f"{rate_percent:+.2f}%"
            rate_color = "#4ade80" if rate_value >= 0 else "#f87171"
            rate_item = QtWidgets.QTableWidgetItem(rate_text)
            rate_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            rate_item.setForeground(QtGui.QColor(rate_color))
            self._table.setItem(row, 1, rate_item)

            pos_item = QtWidgets.QTableWidgetItem(f"{position_percent:.2f}%")
            pos_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            pos_item.setForeground(QtGui.QColor("#e2e8f0"))
            self._table.setItem(row, 2, pos_item)

            op_info = OPERATION_MAP.get(self._current_op, OPERATION_MAP["0"])
            op_item = QtWidgets.QTableWidgetItem(op_info["label"])
            op_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            op_item.setForeground(QtGui.QColor(op_info["color"]))
            self._table.setItem(row, 3, op_item)

    # ── 正态分布图 ──

    def _draw_distribution(self):
        rates = [item.get("rate", 0) * 100 for item in self._user_rates]

        if len(rates) < 2:
            text_item = pg.TextItem("数据不足，无法绘制分布", color="#94a3b8", anchor=(0.5, 0.5))
            self._chart_widget.addItem(text_item)
            text_item.setPos(0, 0.5)
            return

        rates_array = np.array(rates)
        mean = np.mean(rates_array)
        std = np.std(rates_array)

        if std < 1e-9:
            std = 0.01

        x_min = mean - 4 * std
        x_max = mean + 4 * std
        x_curve = np.linspace(x_min, x_max, 200)
        y_curve = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x_curve - mean) / std) ** 2)

        # 绘制正态分布曲线
        curve_pen = pg.mkPen(color="#60a5fa", width=2)
        self._chart_widget.plot(x_curve, y_curve, pen=curve_pen)

        # 填充曲线下方区域
        fill_brush = pg.mkBrush(96, 165, 250, 40)
        fill_curve = pg.PlotCurveItem(x_curve, y_curve, pen=pg.mkPen(None))
        zero_curve = pg.PlotCurveItem(x_curve, np.zeros_like(x_curve), pen=pg.mkPen(None))
        fill_between = pg.FillBetweenItem(fill_curve, zero_curve, brush=fill_brush)
        self._chart_widget.addItem(fill_between)

        # 绘制可拖动的辅助线
        mean_pen = pg.mkPen(color="#fbbf24", width=2, style=QtCore.Qt.PenStyle.DashLine)
        self._mean_line = pg.InfiniteLine(
            pos=mean, angle=90, pen=mean_pen,
            movable=True,
            hoverPen=pg.mkPen(color="#fde68a", width=3, style=QtCore.Qt.PenStyle.DashLine),
        )
        self._chart_widget.addItem(self._mean_line)

        # 辅助线标注（跟随拖动更新）
        y_peak = float(max(y_curve))
        self._mean_label = pg.TextItem(f"均值: {mean:.2f}%", color="#fbbf24", anchor=(0, 1))
        self._chart_widget.addItem(self._mean_label)
        self._mean_label.setPos(mean, y_peak * 0.95)

        # 保存分布参数供拖动时更新标注
        self._dist_mean = mean
        self._dist_std = std
        self._dist_y_peak = y_peak
        self._mean_line.sigPositionChanged.connect(self._on_line_moved)

        # 绘制各用户散点
        scatter = pg.ScatterPlotItem(size=8, pen=pg.mkPen(None))
        spots = []
        for rate in rates:
            y_pos = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((rate - mean) / std) ** 2)
            color = "#4ade80" if rate >= 0 else "#f87171"
            spots.append({
                "pos": (rate, y_pos),
                "brush": pg.mkBrush(color),
                "pen": pg.mkPen("#0f172a", width=1),
            })
        scatter.addPoints(spots)
        self._chart_widget.addItem(scatter)

    def _on_line_moved(self):
        """辅助线被拖动时，更新标注文本和位置"""
        current_pos = self._mean_line.value()
        std = self._dist_std
        y_at_pos = (1 / (std * np.sqrt(2 * np.pi))) * np.exp(
            -0.5 * ((current_pos - self._dist_mean) / std) ** 2
        )
        self._mean_label.setText(f"{current_pos:.2f}%")
        self._mean_label.setPos(current_pos, max(y_at_pos, self._dist_y_peak * 0.15))



# ═══════════════════════════════════════════════════════════════════════════
#  股票预览弹窗（K线 + 成交量 + 砖型图 + KDJ）
# ═══════════════════════════════════════════════════════════════════════════


class StockPreviewDialog(QtWidgets.QDialog):
    """双击持仓股票后弹出的图表预览弹窗"""

    def __init__(
        self,
        symbol: str,
        stock_name: str,
        stock_daily_data_dir: Path,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{symbol} {stock_name}")
        self.resize(1200, 680)
        self.setModal(True)

        self._symbol = symbol
        self._stock_name = stock_name
        self._stock_daily_data_dir = stock_daily_data_dir

        self._setup_ui()
        self._load_chart()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.chart = StockChartWidget()
        self.chart.set_stock_info(self._symbol, self._stock_name)
        layout.addWidget(self.chart, stretch=1)

        close_button = QtWidgets.QPushButton("关闭")
        close_button.setFixedWidth(100)
        close_button.clicked.connect(self.accept)
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def _load_chart(self):
        try:
            df_daily = load_daily_csv(self._stock_daily_data_dir, self._symbol)
            if not df_daily.empty:
                self.chart.set_daily(df_daily)
        except Exception as error:
            logger.warning("加载 %s 图表数据失败: %s", self._symbol, error)


# ═══════════════════════════════════════════════════════════════════════════
#  单只股票数据更新 Worker
# ═══════════════════════════════════════════════════════════════════════════
