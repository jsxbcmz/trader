"""回测页面：配置态（表单）→ 执行 → 结果态（三栏布局）。

支持对比模式：一键运行两种买入时机并对比绩效差异。
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from core.backtest.cache import get_cached_result, save_cached_result
from core.backtest.engine import BacktestEngine
from core.backtest.metrics import calculate_metrics
from core.backtest.models import BacktestConfig, BacktestResult, BuyTiming
from core.backtest.report import export_snapshots_csv, export_trades_csv, generate_markdown_report
from core.backtest.sell_strategy import SELL_STRATEGY_REGISTRY
from core.templates import TemplateService

from ..data_loader import load_daily_csv
from ..utils import start_worker
from ..widgets import StockChartWidget


class _PercentAxisItem(pg.AxisItem):
    """Y 轴刻度以百分比显示。"""

    def tickStrings(self, values, scale, spacing):
        return [f"{v:+.1f}%" for v in values]


# ── 回测后台 Worker ──────────────────────────────────────────


class BacktestWorker(QtCore.QObject):
    """回测后台任务 Worker"""

    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, engine: BacktestEngine, config: BacktestConfig):
        super().__init__()
        self.engine = engine
        self.config = config
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @QtCore.Slot()
    def run(self):
        try:
            result = self.engine.run(
                self.config,
                progress_callback=lambda p: self.progressChanged.emit(p),
                cancelled_fn=lambda: self._cancelled,
            )
            # 计算绩效指标
            result.metrics = calculate_metrics(result)
            self.finished.emit({"result": result})
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


# ── 回测进度弹窗 ──────────────────────────────────────────


class BacktestProgressDialog(QtWidgets.QDialog):
    """回测进度弹窗"""

    stopRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("回测进度")
        self.setModal(True)
        self.resize(420, 200)

        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始回测...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.dateLabel = QtWidgets.QLabel("当前日期：-")
        self.statsLabel = QtWidgets.QLabel("总资产：-  今日交易：0")

        self.stopButton = QtWidgets.QPushButton("停止")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.stopButton)
        button_layout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.dateLabel)
        layout.addWidget(self.statsLabel)
        layout.addStretch(1)
        layout.addLayout(button_layout)

        self.stopButton.clicked.connect(self._on_stop)
        self.closeButton.clicked.connect(self.accept)

    def _on_stop(self):
        self.stopButton.setEnabled(False)
        self.progressLabel.setText("正在停止，请稍候...")
        self.stopRequested.emit()

    def update_progress(self, payload: dict):
        current = int(payload.get("current", 0))
        total = max(int(payload.get("total", 1)), 1)
        phase = payload.get("phase", "simulate")

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))

        if phase == "precompute":
            if payload.get("cache_hit"):
                self.progressLabel.setText("信号缓存命中，跳过预计算")
            else:
                self.progressLabel.setText(f"信号预计算：{current} / {total} 只股票")
            self.dateLabel.setText("")
            self.statsLabel.setText("")
        else:
            date = payload.get("date", "")
            total_assets = payload.get("total_assets", 0)
            trades_today = payload.get("trades_today", 0)
            self.progressLabel.setText(f"回测进度：{current} / {total} 个交易日")
            self.dateLabel.setText(f"当前日期：{date or '-'}")
            self.statsLabel.setText(
                f"总资产：{total_assets:,.0f}  今日交易：{trades_today}"
            )

    def mark_finished(self, summary: str = ""):
        self.stopButton.setEnabled(False)
        self.closeButton.setEnabled(True)
        if summary:
            self.progressLabel.setText(summary)


# ── 交易详情弹窗 ──────────────────────────────────────────


class TradeDetailDialog(QtWidgets.QDialog):
    """点击交易明细中的股票后弹出的图表弹窗。

    展示 K 线主图 + 成交量 + 砖型图 + KDJ 四个子图，
    并在主图上用 B▲ / S▼ 标记该股票的所有买卖点。
    """

    def __init__(
        self,
        symbol: str,
        stock_name: str,
        trades: list,
        stock_daily_data_dir: Path,
        focus_date: str = "",
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{symbol} {stock_name} — 交易详情")
        self.resize(1400, 720)
        self.setModal(True)

        self._symbol = symbol
        self._stock_name = stock_name
        self._trades = trades
        self._stock_daily_data_dir = stock_daily_data_dir
        self._focus_date = focus_date
        self._marker_items: list[pg.TextItem] = []
        self._df = None  # 保存 DataFrame 供点击跳转使用

        self._setup_ui()
        self._load_chart_data()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 左右分栏：图表 + 交易记录 ──
        content_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # 左侧：图表
        self.chart = StockChartWidget()
        self.chart.set_stock_info(self._symbol, self._stock_name)
        content_splitter.addWidget(self.chart)

        # 右侧：交易记录面板
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        trades_label = QtWidgets.QLabel("交易记录")
        trades_label_font = QtGui.QFont()
        trades_label_font.setBold(True)
        trades_label.setFont(trades_label_font)
        right_layout.addWidget(trades_label)

        self.detail_table = QtWidgets.QTableWidget()
        self.detail_table.setColumnCount(5)
        self.detail_table.setHorizontalHeaderLabels([
            "交易日期", "方向", "成交价", "数量", "金额",
        ])
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.detail_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._fill_detail_table()
        self.detail_table.cellClicked.connect(self._on_detail_row_clicked)
        right_layout.addWidget(self.detail_table)

        content_splitter.addWidget(right_panel)

        # 设置右侧面板固定最小宽度，图表自适应拉伸
        right_panel.setMinimumWidth(360)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        layout.addWidget(content_splitter, stretch=1)

        close_button = QtWidgets.QPushButton("关闭")
        close_button.setFixedWidth(100)
        close_button.clicked.connect(self.accept)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def _fill_detail_table(self):
        """填充交易记录详情表格"""
        self.detail_table.setRowCount(len(self._trades))
        for i, trade in enumerate(self._trades):
            self.detail_table.setItem(
                i, 0, QtWidgets.QTableWidgetItem(trade.trade_date),
            )

            direction_text = "买入" if trade.action == "BUY" else "卖出"
            direction_item = QtWidgets.QTableWidgetItem(direction_text)
            if trade.action == "BUY":
                direction_item.setForeground(QtGui.QColor("#FF4444"))
            else:
                direction_item.setForeground(QtGui.QColor("#00CC00"))
            self.detail_table.setItem(i, 1, direction_item)

            self.detail_table.setItem(
                i, 2, QtWidgets.QTableWidgetItem(f"{trade.price:.2f}"),
            )
            self.detail_table.setItem(
                i, 3, QtWidgets.QTableWidgetItem(f"{trade.quantity:,}"),
            )
            self.detail_table.setItem(
                i, 4, QtWidgets.QTableWidgetItem(f"{trade.amount:,.2f}"),
            )
        self.detail_table.resizeColumnsToContents()

    def _load_chart_data(self):
        try:
            df_daily = load_daily_csv(self._stock_daily_data_dir, self._symbol)
            if df_daily.empty:
                return
            self._df = df_daily
            self.chart.set_daily(df_daily)
            self._draw_trade_markers(df_daily)
            self._center_on_focus_date(df_daily)
        except Exception:
            pass

    def _on_detail_row_clicked(self, row: int, _column: int):
        """点击交易记录行，跳转图表到该交易日期"""
        if self._df is None:
            return
        date_item = self.detail_table.item(row, 0)
        if not date_item:
            return
        self._focus_date = date_item.text()
        self._center_on_focus_date(self._df)

    def _center_on_focus_date(self, df: "pd.DataFrame"):
        """将图表可视范围定位到 focus_date 为中心"""
        if not self._focus_date:
            return

        date_strings = [
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            for d in df["date"]
        ]
        date_to_index = {ds: i for i, ds in enumerate(date_strings)}
        center_index = date_to_index.get(self._focus_date[:10])
        if center_index is None:
            return

        visible_bars = 100
        half_window = visible_bars // 2
        half_width = self.chart._item_half_width
        right_padding = self.chart._right_view_padding

        x_left = max(self.chart._x_min, center_index - half_window - half_width)
        x_right = min(self.chart._x_max, center_index + half_window + half_width + right_padding)

        self.chart.pricePlot.setXRange(x_left, x_right, padding=0)

    def _draw_trade_markers(self, df: "pd.DataFrame"):
        """在 K 线主图上标记买卖点"""
        for marker_item in self._marker_items:
            self.chart.pricePlot.removeItem(marker_item)
        self._marker_items.clear()

        date_strings = [
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            for d in df["date"]
        ]
        date_to_index: dict[str, int] = {ds: i for i, ds in enumerate(date_strings)}

        # 获取趋势线数据用于避让
        short_trend = getattr(self.chart, "_short_trend_values", np.array([]))
        long_short = getattr(self.chart, "_long_short_values", np.array([]))

        high_values = df["high"].values.astype(float)
        low_values = df["low"].values.astype(float)
        total_bars = len(df)

        for trade in self._trades:
            trade_date = trade.trade_date[:10]
            x_pos = date_to_index.get(trade_date)
            if x_pos is None:
                continue

            row = df.iloc[x_pos]
            bar_high = float(row["high"])
            bar_low = float(row["low"])

            # 用标记附近的局部价格范围计算偏移，避免全局范围过大导致标记超出可视区域
            local_window = 30
            window_left = max(0, x_pos - local_window)
            window_right = min(total_bars, x_pos + local_window + 1)
            local_price_range = float(
                np.max(high_values[window_left:window_right])
                - np.min(low_values[window_left:window_right])
            )
            if local_price_range <= 0:
                local_price_range = 1.0
            marker_offset = local_price_range * 0.02

            # 综合柱子和趋势线，计算该位置实际占用区域的上下边界
            local_top = bar_high
            local_bottom = bar_low
            if x_pos < len(short_trend):
                local_top = max(local_top, float(short_trend[x_pos]))
                local_bottom = min(local_bottom, float(short_trend[x_pos]))
            if x_pos < len(long_short):
                local_top = max(local_top, float(long_short[x_pos]))
                local_bottom = min(local_bottom, float(long_short[x_pos]))

            # 用局部窗口的高低点判断标记放置方向
            local_high = float(np.max(high_values[window_left:window_right]))
            local_low = float(np.min(low_values[window_left:window_right]))

            color = "#FF4444" if trade.action == "BUY" else "#00CC00"
            letter = "B" if trade.action == "BUY" else "S"

            # 比较上方和下方的空间，选择更宽裕的一侧
            space_above = local_high - local_top
            space_below = local_bottom - local_low
            place_below = space_below >= space_above

            if place_below:
                # 标记放在下方：三角在上（靠近柱子），字母在下（远离柱子）
                text = f"▲\n{letter}"
                y_pos = local_bottom - marker_offset
                anchor = (0.5, 0)
            else:
                # 标记放在上方：三角在下（靠近柱子），字母在上（远离柱子）
                text = f"{letter}\n▼"
                y_pos = local_top + marker_offset
                anchor = (0.5, 1)

            marker = pg.TextItem(text=text, color=color, anchor=anchor)
            font = QtGui.QFont("Arial", 8, QtGui.QFont.Weight.Bold)
            marker.setFont(font)
            marker.setPos(x_pos, y_pos)
            self.chart.pricePlot.addItem(marker)
            self._marker_items.append(marker)


# ── 回测页面 ──────────────────────────────────────────


class BacktestPage(QtWidgets.QWidget):
    """回测页面：配置态 → 结果态"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.template_service = TemplateService.from_root(self.root)

        self._backtest_thread: QtCore.QThread | None = None
        self._backtest_worker: BacktestWorker | None = None
        self._progress_dialog: BacktestProgressDialog | None = None
        self._last_result: BacktestResult | None = None

        self._month_highlight_curve = None  # 月度高亮曲线

        # 资金曲线 hover 状态
        self._equity_snapshots: list | None = None  # 当前展示的快照列表
        self._equity_crosshair: pg.InfiniteLine | None = None
        self._equity_info_text: pg.TextItem | None = None

        # 对比模式状态
        self._compare_mode: bool = False
        self._compare_results: list[BacktestResult] = []
        self._compare_pending_configs: list[BacktestConfig] = []
        self._compare_current_index: int = 0

        self._setup_ui()
        self._connect_signals()
        self.reload_templates()

    # ── UI 构建 ──────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.page_stack = QtWidgets.QStackedWidget()
        main_layout.addWidget(self.page_stack)

        self.page_stack.addWidget(self._build_config_panel())
        self.page_stack.addWidget(self._build_result_panel())

        self.page_stack.setCurrentIndex(0)

    def _build_config_panel(self) -> QtWidgets.QWidget:
        """构建配置态表单"""
        panel = QtWidgets.QWidget()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        form_container = QtWidgets.QWidget()
        form_layout = QtWidgets.QFormLayout(form_container)
        form_layout.setContentsMargins(80, 40, 80, 40)
        form_layout.setSpacing(12)
        form_layout.setLabelAlignment(QtCore.Qt.AlignRight)

        # 标题
        title = QtWidgets.QLabel("回测参数配置")
        title_font = QtGui.QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(QtCore.Qt.AlignCenter)
        form_layout.addRow(title)
        form_layout.addRow(QtWidgets.QLabel(""))  # 间距

        # 策略模板
        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.setMinimumWidth(300)
        form_layout.addRow("策略模板：", self.template_combo)

        # ── 时间范围 ──
        form_layout.addRow(self._make_section_label("时间范围"))

        self.start_date_edit = QtWidgets.QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QtCore.QDate(2024, 1, 1))
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        form_layout.addRow("开始日期：", self.start_date_edit)

        self.end_date_edit = QtWidgets.QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QtCore.QDate(2025, 12, 31))
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        form_layout.addRow("结束日期：", self.end_date_edit)

        # ── 资金参数 ──
        form_layout.addRow(self._make_section_label("资金参数"))

        self.capital_spin = QtWidgets.QSpinBox()
        self.capital_spin.setRange(10_000, 100_000_000)
        self.capital_spin.setSingleStep(100_000)
        self.capital_spin.setValue(100_000)
        self.capital_spin.setSuffix(" 元")
        self.capital_spin.setGroupSeparatorShown(True)
        form_layout.addRow("初始资金：", self.capital_spin)

        self.position_spin = QtWidgets.QSpinBox()
        self.position_spin.setRange(1, 100)
        self.position_spin.setValue(33)
        self.position_spin.setSuffix(" %")
        form_layout.addRow("单只仓位：", self.position_spin)

        self.max_positions_spin = QtWidgets.QSpinBox()
        self.max_positions_spin.setRange(1, 100)
        self.max_positions_spin.setValue(3)
        self.max_positions_spin.setSuffix(" 只")
        form_layout.addRow("最大持仓：", self.max_positions_spin)

        # ── 交易成本 ──
        form_layout.addRow(self._make_section_label("交易成本"))

        self.commission_combo = QtWidgets.QComboBox()
        self.commission_combo.addItems(["万1", "万1.5", "万2", "万3"])
        self.commission_combo.setCurrentIndex(0)
        form_layout.addRow("佣金费率：", self.commission_combo)

        self.stamp_tax_label = QtWidgets.QLabel("千1（仅卖出）")
        form_layout.addRow("印花税率：", self.stamp_tax_label)

        # ── 买入策略 ──
        form_layout.addRow(self._make_section_label("买入策略"))

        self.buy_timing_group = QtWidgets.QButtonGroup(self)
        self.buy_close_radio = QtWidgets.QRadioButton("信号日收盘价")
        self.buy_next_open_radio = QtWidgets.QRadioButton("次日开盘价")
        self.buy_next_open_radio.setChecked(True)
        self.buy_timing_group.addButton(self.buy_close_radio)
        self.buy_timing_group.addButton(self.buy_next_open_radio)

        timing_layout = QtWidgets.QHBoxLayout()
        timing_layout.addWidget(self.buy_close_radio)
        timing_layout.addWidget(self.buy_next_open_radio)
        timing_layout.addStretch()
        form_layout.addRow("买入时机：", timing_layout)

        # 对比模式复选框
        self.compare_checkbox = QtWidgets.QCheckBox(
            "对比模式：同时运行两种买入时机并对比绩效差异"
        )
        self.compare_checkbox.setToolTip(
            "勾选后将分别以「信号日收盘价」和「次日开盘价」运行回测，\n"
            "结果页面并排展示两组绩效指标和两条资金曲线。"
        )
        form_layout.addRow("", self.compare_checkbox)

        # ── 卖出策略 ──
        form_layout.addRow(self._make_section_label("卖出策略"))

        self.sell_strategy_combo = QtWidgets.QComboBox()
        self.sell_strategy_combo.addItems(list(SELL_STRATEGY_REGISTRY.keys()))
        form_layout.addRow("卖出策略：", self.sell_strategy_combo)

        # 砖形图专属参数
        self.sell_params_widget = QtWidgets.QWidget()
        sell_params_layout = QtWidgets.QFormLayout(self.sell_params_widget)
        sell_params_layout.setContentsMargins(0, 0, 0, 0)

        self.profit_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.profit_threshold_spin.setRange(0.5, 50.0)
        self.profit_threshold_spin.setValue(3.0)
        self.profit_threshold_spin.setSuffix(" %")
        self.profit_threshold_spin.setSingleStep(0.5)
        sell_params_layout.addRow("分批止盈阈值：", self.profit_threshold_spin)

        self.partial_sell_spin = QtWidgets.QSpinBox()
        self.partial_sell_spin.setRange(5, 100)
        self.partial_sell_spin.setValue(25)
        self.partial_sell_spin.setSuffix(" %")
        sell_params_layout.addRow("分批卖出比例：", self.partial_sell_spin)

        self.sell_params_widget.setVisible(False)
        form_layout.addRow("", self.sell_params_widget)

        # 买入评分权重（砖形图策略专属）
        self.scorer_widget = QtWidgets.QWidget()
        scorer_layout = QtWidgets.QFormLayout(self.scorer_widget)
        scorer_layout.setContentsMargins(0, 0, 0, 0)

        self.scorer_checkbox = QtWidgets.QCheckBox("启用买入评分排序")
        self.scorer_checkbox.setChecked(True)
        scorer_layout.addRow("", self.scorer_checkbox)

        self.weight_brick_body_spin = QtWidgets.QDoubleSpinBox()
        self.weight_brick_body_spin.setRange(0, 100)
        self.weight_brick_body_spin.setValue(30)
        self.weight_brick_body_spin.setSingleStep(5)
        scorer_layout.addRow("砖大柱短权重：", self.weight_brick_body_spin)

        self.weight_trend_spin = QtWidgets.QDoubleSpinBox()
        self.weight_trend_spin.setRange(0, 100)
        self.weight_trend_spin.setValue(25)
        self.weight_trend_spin.setSingleStep(5)
        scorer_layout.addRow("趋势线权重：", self.weight_trend_spin)

        self.weight_first_red_spin = QtWidgets.QDoubleSpinBox()
        self.weight_first_red_spin.setRange(0, 100)
        self.weight_first_red_spin.setValue(25)
        self.weight_first_red_spin.setSingleStep(5)
        scorer_layout.addRow("首根翻红权重：", self.weight_first_red_spin)

        self.weight_exhaustion_spin = QtWidgets.QDoubleSpinBox()
        self.weight_exhaustion_spin.setRange(0, 100)
        self.weight_exhaustion_spin.setValue(20)
        self.weight_exhaustion_spin.setSingleStep(5)
        scorer_layout.addRow("衰竭反转权重：", self.weight_exhaustion_spin)

        self.scorer_widget.setVisible(False)
        form_layout.addRow("", self.scorer_widget)

        # ── 开始回测按钮 ──
        form_layout.addRow(QtWidgets.QLabel(""))  # 间距

        start_layout = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("🚀 开始回测")
        self.start_button.setMinimumHeight(40)
        start_font = QtGui.QFont()
        start_font.setPointSize(12)
        start_font.setBold(True)
        self.start_button.setFont(start_font)
        start_layout.addWidget(self.start_button)

        self.force_start_button = QtWidgets.QPushButton("🔄 强制回测")
        self.force_start_button.setMinimumHeight(40)
        self.force_start_button.setToolTip("忽略缓存，重新执行完整回测流程")
        start_layout.addWidget(self.force_start_button)

        form_layout.addRow(start_layout)

        # 参数敏感性分析按钮
        self.sensitivity_button = QtWidgets.QPushButton("📊 参数敏感性分析")
        self.sensitivity_button.setToolTip(
            "网格搜索不同止盈阈值和卖出比例的组合，\n"
            "输出参数-收益矩阵，帮助找到最优参数。\n"
            "仅在卖出策略为 brick_chart 时可用。"
        )
        form_layout.addRow(self.sensitivity_button)

        scroll.setWidget(form_container)

        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(scroll)

        return panel

    def _build_result_panel(self) -> QtWidgets.QWidget:
        """构建结果态三栏布局"""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 左栏：绩效概览 ──
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        metrics_title = QtWidgets.QLabel("绩效概览")
        metrics_title_font = QtGui.QFont()
        metrics_title_font.setBold(True)
        metrics_title_font.setPointSize(12)
        metrics_title.setFont(metrics_title_font)
        left_layout.addWidget(metrics_title)

        self.metrics_table = QtWidgets.QTableWidget()
        self.metrics_table.setColumnCount(2)
        self.metrics_table.setHorizontalHeaderLabels(["指标", "数值"])
        self.metrics_table.horizontalHeader().setStretchLastSection(True)
        self.metrics_table.verticalHeader().setVisible(False)
        self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.metrics_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.metrics_table.setMinimumWidth(220)
        self.metrics_table.setMaximumWidth(280)
        left_layout.addWidget(self.metrics_table)

        self.back_button = QtWidgets.QPushButton("← 返回配置")
        left_layout.addWidget(self.back_button)

        layout.addWidget(left_widget)

        # ── 中栏：图表 ──
        center_widget = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)

        chart_title = QtWidgets.QLabel("资金曲线")
        chart_title_font = QtGui.QFont()
        chart_title_font.setBold(True)
        chart_title_font.setPointSize(12)
        chart_title.setFont(chart_title_font)
        center_layout.addWidget(chart_title)

        self.equity_plot = pg.PlotWidget(
            axisItems={"left": _PercentAxisItem(orientation="left")},
        )
        self.equity_plot.setBackground("w")
        self.equity_plot.showGrid(x=True, y=True, alpha=0.3)
        self.equity_plot.setLabel("left", "涨幅 (%)")
        self.equity_plot.setLabel("bottom", "交易日")
        # 禁用缩放和拖拽，保持初始比例
        vb = self.equity_plot.getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        vb.setMenuEnabled(False)

        # 十字线和信息浮窗
        self._equity_crosshair = pg.InfiniteLine(
            angle=90, pen=pg.mkPen(color=(150, 150, 150), width=1, style=QtCore.Qt.DashLine),
        )
        self._equity_crosshair.setVisible(False)
        self.equity_plot.addItem(self._equity_crosshair, ignoreBounds=True)

        self._equity_info_text = pg.TextItem(
            text="", anchor=(0, 0), color=(30, 30, 30),
        )
        self._equity_info_text.setVisible(False)
        self._equity_info_text.setZValue(100)
        font = QtGui.QFont("Consolas, Menlo, monospace", 9)
        self._equity_info_text.setFont(font)
        self.equity_plot.addItem(self._equity_info_text, ignoreBounds=True)

        # 鼠标追踪
        self.equity_plot.scene().sigMouseMoved.connect(self._on_equity_mouse_moved)
        self.equity_plot.setMouseTracking(True)

        center_layout.addWidget(self.equity_plot, stretch=3)

        # 月度收益表格标题栏（标题 + 还原按钮）
        monthly_header_layout = QtWidgets.QHBoxLayout()
        monthly_title = QtWidgets.QLabel("月度收益分布")
        monthly_title_font = QtGui.QFont()
        monthly_title_font.setBold(True)
        monthly_title.setFont(monthly_title_font)
        monthly_header_layout.addWidget(monthly_title)
        monthly_header_layout.addStretch()
        self.monthly_reset_button = QtWidgets.QPushButton("还原")
        self.monthly_reset_button.setFixedSize(50, 24)
        self.monthly_reset_button.setVisible(False)
        monthly_header_layout.addWidget(self.monthly_reset_button)
        center_layout.addLayout(monthly_header_layout)

        self.monthly_table = QtWidgets.QTableWidget()
        self.monthly_table.setColumnCount(4)
        self.monthly_table.setHorizontalHeaderLabels(["月份", "收益率", "交易次数", "胜率"])
        self.monthly_table.horizontalHeader().setStretchLastSection(True)
        self.monthly_table.verticalHeader().setVisible(False)
        self.monthly_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.monthly_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.monthly_table.setCursor(QtCore.Qt.PointingHandCursor)
        self.monthly_table.setMouseTracking(True)
        self.monthly_table.setStyleSheet(
            "QTableWidget { selection-background-color: #BAE7FF; }"
        )
        center_layout.addWidget(self.monthly_table, stretch=1)

        # 导出按钮
        export_layout = QtWidgets.QHBoxLayout()
        self.export_md_button = QtWidgets.QPushButton("导出 Markdown 报告")
        self.export_csv_button = QtWidgets.QPushButton("导出 CSV 交易明细")
        export_layout.addWidget(self.export_md_button)
        export_layout.addWidget(self.export_csv_button)
        export_layout.addStretch()
        center_layout.addLayout(export_layout)

        layout.addWidget(center_widget, stretch=1)

        # ── 右栏：交易明细 ──
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        trades_title = QtWidgets.QLabel("交易明细")
        trades_title_font = QtGui.QFont()
        trades_title_font.setBold(True)
        trades_title_font.setPointSize(12)
        trades_title.setFont(trades_title_font)
        right_layout.addWidget(trades_title)

        self.trades_table = QtWidgets.QTableWidget()
        self.trades_table.setColumnCount(7)
        self.trades_table.setHorizontalHeaderLabels([
            "日期", "代码", "名称", "方向", "价格", "数量", "原因",
        ])
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.trades_table.verticalHeader().setVisible(False)
        self.trades_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.trades_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.trades_table.setCursor(QtCore.Qt.PointingHandCursor)
        self.trades_table.setMouseTracking(True)
        self.trades_table.setStyleSheet(
            "QTableWidget { selection-background-color: #BAE7FF; }"
        )
        self.trades_table.setMinimumWidth(350)
        self.trades_table.setMaximumWidth(450)
        right_layout.addWidget(self.trades_table)

        layout.addWidget(right_widget)

        return panel

    def _make_section_label(self, text: str) -> QtWidgets.QLabel:
        """创建分组标题标签"""
        label = QtWidgets.QLabel(f"── {text} ──")
        font = QtGui.QFont()
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet("color: #666; margin-top: 8px;")
        return label

    # ── 信号连接 ──────────────────────────────────────────

    def _connect_signals(self):
        self.start_button.clicked.connect(self._on_start_backtest)
        self.force_start_button.clicked.connect(self._on_force_start_backtest)
        self.back_button.clicked.connect(self._on_back_to_config)
        self.export_md_button.clicked.connect(self._on_export_markdown)
        self.export_csv_button.clicked.connect(self._on_export_csv)
        self.sell_strategy_combo.currentTextChanged.connect(self._on_sell_strategy_changed)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        self.sensitivity_button.clicked.connect(self._on_start_sensitivity)
        self.trades_table.cellClicked.connect(self._on_trades_table_cell_clicked)
        self.monthly_table.cellClicked.connect(self._on_monthly_row_clicked)
        self.monthly_reset_button.clicked.connect(self._on_monthly_reset)

    # ── 模板管理 ──────────────────────────────────────────

    def reload_templates(self):
        """重新加载模板列表"""
        self.template_combo.blockSignals(True)
        current_id = self.template_combo.currentData()
        self.template_combo.clear()

        templates = self.template_service.list_templates()
        for template in templates:
            self.template_combo.addItem(template.name, template.id)

        # 恢复之前选中的模板
        if current_id:
            for i in range(self.template_combo.count()):
                if self.template_combo.itemData(i) == current_id:
                    self.template_combo.setCurrentIndex(i)
                    break

        self.template_combo.blockSignals(False)

        # blockSignals 阻止了信号触发，需要手动同步卖出策略
        self._sync_sell_strategy()

    def prefill_template(self, template_id: str):
        """从模板页跳转时预填模板"""
        for i in range(self.template_combo.count()):
            if self.template_combo.itemData(i) == template_id:
                self.template_combo.setCurrentIndex(i)
                break

    def _on_template_changed(self, index: int):
        """模板切换时自动匹配卖出策略"""
        self._sync_sell_strategy()

    def _sync_sell_strategy(self):
        """根据当前模板自动匹配卖出策略

        检测规则：模板名称包含"砖"，或 TDX 源码包含砖型图公式关键词。
        """
        template_id = self.template_combo.currentData()
        if not template_id:
            return

        template = self.template_service.get_template(template_id)
        if template is None:
            return

        is_brick = "砖" in template.name
        if not is_brick and template.tdx_source:
            brick_keywords = ("砖型图", "砖形图", "VAR1A", "VAR6A")
            is_brick = any(kw in template.tdx_source for kw in brick_keywords)

        if is_brick:
            idx = self.sell_strategy_combo.findText("brick_chart")
            if idx >= 0:
                self.sell_strategy_combo.setCurrentIndex(idx)

    def _on_sell_strategy_changed(self, strategy_name: str):
        """卖出策略切换时显示/隐藏参数"""
        is_brick = strategy_name == "brick_chart"
        self.sell_params_widget.setVisible(is_brick)
        self.scorer_widget.setVisible(is_brick)

    # ── 回测执行 ──────────────────────────────────────────

    def _build_config_from_form(self) -> BacktestConfig | None:
        """从表单构建回测配置，返回 None 表示校验失败"""
        template_id = self.template_combo.currentData()
        if not template_id:
            QtWidgets.QMessageBox.warning(self, "提示", "请先选择策略模板")
            return None

        template = self.template_service.get_template(template_id)
        if not template:
            QtWidgets.QMessageBox.warning(self, "提示", "模板不存在")
            return None

        commission_text = self.commission_combo.currentText()
        commission_map = {"万1": 0.0001, "万1.5": 0.00015, "万2": 0.0002, "万3": 0.0003}
        commission_rate = commission_map.get(commission_text, 0.0001)

        sell_strategy_name = self.sell_strategy_combo.currentText()
        sell_params = {}
        buy_scorer_name = ""
        buy_scorer_params = {}
        if sell_strategy_name == "brick_chart":
            sell_params = {
                "partial_profit_threshold": self.profit_threshold_spin.value() / 100.0,
                "partial_sell_ratio": self.partial_sell_spin.value() / 100.0,
            }
            if self.scorer_checkbox.isChecked():
                buy_scorer_name = "brick"
                buy_scorer_params = {
                    "weight_big_brick_small_body": self.weight_brick_body_spin.value(),
                    "weight_near_trend": self.weight_trend_spin.value(),
                    "weight_first_red": self.weight_first_red_spin.value(),
                    "weight_bear_exhaustion": self.weight_exhaustion_spin.value(),
                }

        return BacktestConfig(
            template_id=template.id,
            template_name=template.name,
            tdx_source=template.tdx_source,
            stock_pool_name=template.stock_pool_name,
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=self.end_date_edit.date().toString("yyyy-MM-dd"),
            initial_capital=float(self.capital_spin.value()),
            position_size=self.position_spin.value() / 100.0,
            max_positions=self.max_positions_spin.value(),
            commission_rate=commission_rate,
            stamp_tax_rate=0.001,
            buy_timing=BuyTiming.CLOSE if self.buy_close_radio.isChecked() else BuyTiming.NEXT_OPEN,
            sell_strategy_name=sell_strategy_name,
            sell_strategy_params=sell_params,
            buy_scorer_name=buy_scorer_name,
            buy_scorer_params=buy_scorer_params,
        )

    def _on_start_backtest(self):
        """开始回测"""
        config = self._build_config_from_form()
        if config is None:
            return

        if self.compare_checkbox.isChecked():
            self._start_compare_backtest(config)
        else:
            self._compare_mode = False
            self._start_single_backtest(config)

    def _on_force_start_backtest(self):
        """强制回测：跳过缓存，重新执行完整回测流程"""
        config = self._build_config_from_form()
        if config is None:
            return

        if self.compare_checkbox.isChecked():
            self._start_compare_backtest(config)
        else:
            self._compare_mode = False
            self._start_single_backtest(config, skip_cache=True)

    def _start_single_backtest(self, config: BacktestConfig, skip_cache: bool = False):
        """启动单次回测（先检查缓存）"""
        cached = None if skip_cache else get_cached_result(self.root, config)
        if cached is not None:
            self._last_result = cached
            self._display_result(cached)
            self.statusMessageRequested.emit(
                f"[缓存命中] 总收益率 {cached.metrics.total_return * 100:+.2f}%", 5000,
            )
            return

        engine = BacktestEngine.from_root(self.root)
        self._backtest_worker = BacktestWorker(engine, config)

        self._progress_dialog = BacktestProgressDialog(self)
        self._progress_dialog.stopRequested.connect(self._backtest_worker.cancel)

        self._backtest_thread = start_worker(
            self,
            self._backtest_worker,
            on_progress=self._on_backtest_progress,
            on_finished=self._on_backtest_finished,
            on_error=self._on_backtest_error,
            on_cleanup=self._on_backtest_cleanup,
        )

        self._progress_dialog.show()
        self.start_button.setEnabled(False)
        self.force_start_button.setEnabled(False)

    def _start_compare_backtest(self, base_config: BacktestConfig):
        """启动对比模式回测：依次运行收盘价和次日开盘价两种模式"""
        self._compare_mode = True
        self._compare_results = []
        self._compare_current_index = 0

        config_close = copy.copy(base_config)
        config_close.buy_timing = BuyTiming.CLOSE

        config_next_open = copy.copy(base_config)
        config_next_open.buy_timing = BuyTiming.NEXT_OPEN

        self._compare_pending_configs = [config_close, config_next_open]

        # 先检查两个配置是否都有缓存
        cached_results = []
        all_cached = True
        for cfg in self._compare_pending_configs:
            cached = get_cached_result(self.root, cfg)
            if cached is not None:
                cached_results.append(cached)
            else:
                all_cached = False
                break

        if all_cached and len(cached_results) == len(self._compare_pending_configs):
            self._compare_results = cached_results
            self._last_result = cached_results[0]
            self._display_compare_results(cached_results)
            self.statusMessageRequested.emit("[缓存命中] 对比回测结果已加载", 5000)
            self._compare_mode = False
            return

        # 有未命中的缓存，需要重新运行全部回测
        self._compare_results = []
        self._compare_current_index = 0

        self._progress_dialog = BacktestProgressDialog(self)
        self._progress_dialog.setWindowTitle("对比回测进度")
        self._progress_dialog.progressLabel.setText(
            "对比模式 [1/2]：运行「信号日收盘价」买入..."
        )

        self.start_button.setEnabled(False)
        self.force_start_button.setEnabled(False)
        self._progress_dialog.show()
        self._run_next_compare_backtest()

    def _run_next_compare_backtest(self):
        """运行对比模式中的下一个回测"""
        if self._compare_current_index >= len(self._compare_pending_configs):
            return

        config = self._compare_pending_configs[self._compare_current_index]
        engine = BacktestEngine.from_root(self.root)
        self._backtest_worker = BacktestWorker(engine, config)

        if self._progress_dialog:
            self._progress_dialog.stopRequested.connect(self._backtest_worker.cancel)

        self._backtest_thread = start_worker(
            self,
            self._backtest_worker,
            on_progress=self._on_backtest_progress,
            on_finished=self._on_backtest_finished,
            on_error=self._on_backtest_error,
            on_cleanup=self._on_backtest_cleanup,
        )

    def _on_backtest_progress(self, payload: dict):
        if self._progress_dialog:
            phase = payload.get("phase", "simulate")

            if phase == "precompute":
                current = payload.get("current", 0)
                total = payload.get("total", 1)
                if payload.get("cache_hit"):
                    self._progress_dialog.progressLabel.setText("信号缓存命中，跳过预计算")
                else:
                    self._progress_dialog.progressLabel.setText(
                        f"信号预计算：{current} / {total} 只股票"
                    )
                self._progress_dialog.update_progress(payload)
                return

            if self._compare_mode:
                step = self._compare_current_index + 1
                total_steps = len(self._compare_pending_configs)
                timing_label = (
                    "信号日收盘价" if self._compare_current_index == 0 else "次日开盘价"
                )
                self._progress_dialog.progressLabel.setText(
                    f"对比模式 [{step}/{total_steps}]「{timing_label}」"
                    f"：{payload.get('current', 0)} / {payload.get('total', 1)} 个交易日"
                )
            self._progress_dialog.update_progress(payload)

    def _on_backtest_finished(self, payload: dict):
        result: BacktestResult = payload.get("result")
        if result is None:
            return

        # 保存回测结果到缓存
        try:
            save_cached_result(self.root, result.config, result)
        except Exception:
            pass  # 缓存保存失败不影响主流程

        if self._compare_mode:
            self._compare_results.append(result)
            self._compare_current_index += 1

            if self._compare_current_index < len(self._compare_pending_configs):
                # 还有下一轮对比回测
                if self._progress_dialog:
                    self._progress_dialog.progressLabel.setText(
                        "对比模式 [2/2]：运行「次日开盘价」买入..."
                    )
                    self._progress_dialog.progressBar.setValue(0)
                self._run_next_compare_backtest()
                return

            # 全部对比回测完成
            if self._progress_dialog:
                self._progress_dialog.accept()

            self._last_result = self._compare_results[0]
            self._display_compare_results(self._compare_results)
            self.statusMessageRequested.emit("对比回测完成", 5000)
        else:
            self._last_result = result

            if self._progress_dialog:
                self._progress_dialog.accept()

            self._display_result(result)
            self.statusMessageRequested.emit(
                f"回测完成：总收益率 {result.metrics.total_return * 100:+.2f}%", 5000,
            )

    def _on_backtest_error(self, error_msg: str):
        if self._progress_dialog:
            self._progress_dialog.mark_finished(f"回测失败：{error_msg}")
        QtWidgets.QMessageBox.critical(self, "回测失败", error_msg)
        self._compare_mode = False
        self._compare_results.clear()

    def _on_backtest_cleanup(self):
        self._backtest_thread = None
        self._backtest_worker = None
        # 对比模式下，仅在全部回测完成后才恢复按钮
        if not self._compare_mode or self._compare_current_index >= len(self._compare_pending_configs):
            self.start_button.setEnabled(True)
            self.force_start_button.setEnabled(True)

    # ── 结果展示 ──────────────────────────────────────────

    def _display_result(self, result: BacktestResult):
        """展示单次回测结果"""
        self._fill_metrics_table(result)
        self._draw_equity_curve(result)
        self._fill_monthly_table(result)
        self._fill_trades_table(result)
        self.page_stack.setCurrentIndex(1)

    def _display_compare_results(self, results: list[BacktestResult]):
        """展示对比模式回测结果"""
        self._fill_compare_metrics_table(results)
        self._draw_compare_equity_curves(results)
        self._fill_monthly_table(results[0])
        self._fill_trades_table(results[0])
        self.page_stack.setCurrentIndex(1)

    # ── 绩效指标 ──────────────────────────────────────────

    @staticmethod
    def _format_metrics_rows(result: BacktestResult) -> list[tuple[str, str]]:
        """将回测结果格式化为指标行列表"""
        metrics = result.metrics
        rows = [
            ("总收益率", f"{metrics.total_return * 100:+.2f}%"),
            ("年化收益率", f"{metrics.annual_return * 100:+.2f}%"),
            ("最大回撤", f"{metrics.max_drawdown * 100:.2f}%"),
            ("夏普比率", f"{metrics.sharpe_ratio:.2f}"),
            ("胜率", f"{metrics.win_rate * 100:.1f}%"),
            ("盈亏比", f"{metrics.profit_loss_ratio:.2f}"),
            ("总交易次数", f"{metrics.total_trades}"),
            ("平均持仓天数", f"{metrics.average_hold_days:.1f}"),
            ("最大连续亏损", f"{metrics.max_consecutive_losses}"),
            ("年化波动率", f"{metrics.annual_volatility * 100:.2f}%"),
            ("Calmar 比率", f"{metrics.calmar_ratio:.2f}"),
        ]
        final_assets = (
            result.snapshots[-1].total_assets
            if result.snapshots
            else result.config.initial_capital
        )
        rows.append(("期末总资产", f"{final_assets:,.0f}"))
        rows.append(("期末现金", f"{result.final_cash:,.0f}"))

        # 基准对比指标
        if result.benchmark_snapshots:
            rows.append(("── 基准对比 ──", ""))
            rows.append(("基准收益率", f"{metrics.benchmark_return * 100:+.2f}%"))
            rows.append(("基准年化收益", f"{metrics.benchmark_annual_return * 100:+.2f}%"))
            rows.append(("超额收益率", f"{metrics.excess_return * 100:+.2f}%"))

        return rows

    def _fill_metrics_table(self, result: BacktestResult):
        """填充绩效指标表格（单结果模式）"""
        rows = self._format_metrics_rows(result)

        self.metrics_table.setColumnCount(2)
        self.metrics_table.setHorizontalHeaderLabels(["指标", "数值"])
        self.metrics_table.setRowCount(len(rows))

        for i, (label, value) in enumerate(rows):
            self.metrics_table.setItem(i, 0, QtWidgets.QTableWidgetItem(label))
            value_item = QtWidgets.QTableWidgetItem(value)
            value_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.metrics_table.setItem(i, 1, value_item)

        self.metrics_table.resizeColumnsToContents()

    def _fill_compare_metrics_table(self, results: list[BacktestResult]):
        """填充对比模式绩效指标表格（三列：指标 / 收盘价 / 次日开盘价）"""
        rows_list = [self._format_metrics_rows(r) for r in results]
        labels = [row[0] for row in rows_list[0]]

        headers = ["指标", "收盘价买入", "次日开盘价买入"]
        self.metrics_table.setColumnCount(len(headers))
        self.metrics_table.setHorizontalHeaderLabels(headers)
        self.metrics_table.setRowCount(len(labels))

        for i, label in enumerate(labels):
            self.metrics_table.setItem(i, 0, QtWidgets.QTableWidgetItem(label))
            for col_idx, rows in enumerate(rows_list):
                value = rows[i][1] if i < len(rows) else ""
                value_item = QtWidgets.QTableWidgetItem(value)
                value_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.metrics_table.setItem(i, col_idx + 1, value_item)

        self.metrics_table.resizeColumnsToContents()
        self.metrics_table.setMinimumWidth(320)
        self.metrics_table.setMaximumWidth(420)

    # ── 资金曲线 ──────────────────────────────────────────

    def _draw_benchmark_overlay(self, result: BacktestResult):
        """在资金曲线图上叠加基准指数走势（涨幅%）"""
        if not result.benchmark_snapshots or not result.snapshots:
            return

        strategy_dates = {s.date: i for i, s in enumerate(result.snapshots)}

        x_values = []
        y_values = []
        for benchmark in result.benchmark_snapshots:
            if benchmark.date in strategy_dates:
                x_values.append(strategy_dates[benchmark.date])
                y_values.append(benchmark.cumulative_return * 100)

        if x_values:
            pen = pg.mkPen(color=(180, 180, 180), width=1.5, style=QtCore.Qt.DashLine)
            self.equity_plot.plot(x_values, y_values, pen=pen, name="基准（大盘）")

    def _draw_equity_curve(self, result: BacktestResult):
        """绘制资金曲线（单结果模式）"""
        self.equity_plot.clear()
        self._equity_snapshots = result.snapshots

        if not result.snapshots:
            return

        # 添加图例
        self.equity_plot.addLegend(offset=(60, 10))

        x_values = list(range(len(result.snapshots)))
        y_values = [s.cumulative_return * 100 for s in result.snapshots]

        pen = pg.mkPen(color=(0, 120, 215), width=2)
        self.equity_plot.plot(x_values, y_values, pen=pen, name="策略")

        # 零线（初始基准）
        zero_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen(color=(220, 220, 220), width=1, style=QtCore.Qt.DashLine),
        )
        self.equity_plot.addItem(zero_line)

        # 叠加基准线
        self._draw_benchmark_overlay(result)

        # 重新添加十字线和信息浮窗（clear 会移除它们）
        self._restore_equity_hover_items()

    def _draw_compare_equity_curves(self, results: list[BacktestResult]):
        """绘制对比模式资金曲线（两条策略线 + 基准线）"""
        self.equity_plot.clear()
        # 对比模式使用第一个结果的快照
        self._equity_snapshots = results[0].snapshots if results else None

        colors = [(0, 120, 215), (255, 127, 14)]  # 蓝色、橙色
        labels = ["收盘价买入", "次日开盘价买入"]

        self.equity_plot.addLegend(offset=(60, 10))

        for idx, result in enumerate(results):
            if not result.snapshots:
                continue
            x_values = list(range(len(result.snapshots)))
            y_values = [s.cumulative_return * 100 for s in result.snapshots]
            pen = pg.mkPen(color=colors[idx % len(colors)], width=2)
            self.equity_plot.plot(x_values, y_values, pen=pen, name=labels[idx])

        # 零线（初始基准）
        if results and results[0].snapshots:
            zero_line = pg.InfiniteLine(
                pos=0,
                angle=0,
                pen=pg.mkPen(color=(220, 220, 220), width=1, style=QtCore.Qt.DashLine),
            )
            self.equity_plot.addItem(zero_line)

            # 叠加基准线（使用第一个结果的基准数据）
            self._draw_benchmark_overlay(results[0])

        # 重新添加十字线和信息浮窗
        self._restore_equity_hover_items()

    def _fill_monthly_table(self, result: BacktestResult):
        """填充月度收益表格"""
        monthly = result.metrics.monthly_returns
        self.monthly_table.setRowCount(len(monthly))

        for i, entry in enumerate(monthly):
            self.monthly_table.setItem(i, 0, QtWidgets.QTableWidgetItem(entry["month"]))

            ret_item = QtWidgets.QTableWidgetItem(f"{entry['return'] * 100:+.2f}%")
            ret_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if entry["return"] > 0:
                ret_item.setForeground(QtGui.QColor("red"))
            elif entry["return"] < 0:
                ret_item.setForeground(QtGui.QColor("green"))
            self.monthly_table.setItem(i, 1, ret_item)

            self.monthly_table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(entry["trades"])))
            self.monthly_table.setItem(i, 3, QtWidgets.QTableWidgetItem(f"{entry['win_rate'] * 100:.0f}%"))

        self.monthly_table.resizeColumnsToContents()

    def _fill_trades_table(self, result: BacktestResult):
        """填充交易明细表格"""
        trades = result.trades
        self.trades_table.setRowCount(len(trades))

        for i, trade in enumerate(trades):
            self.trades_table.setItem(i, 0, QtWidgets.QTableWidgetItem(trade.trade_date))

            self.trades_table.setItem(i, 1, QtWidgets.QTableWidgetItem(trade.symbol))

            self.trades_table.setItem(i, 2, QtWidgets.QTableWidgetItem(trade.name))

            direction_item = QtWidgets.QTableWidgetItem("买入" if trade.action == "BUY" else "卖出")
            if trade.action == "BUY":
                direction_item.setForeground(QtGui.QColor("red"))
            else:
                direction_item.setForeground(QtGui.QColor("green"))
            self.trades_table.setItem(i, 3, direction_item)

            self.trades_table.setItem(i, 4, QtWidgets.QTableWidgetItem(f"{trade.price:.2f}"))
            self.trades_table.setItem(i, 5, QtWidgets.QTableWidgetItem(str(trade.quantity)))
            reason_item = QtWidgets.QTableWidgetItem(trade.reason)
            reason_item.setToolTip(trade.reason)
            self.trades_table.setItem(i, 6, reason_item)

        self.trades_table.resizeColumnsToContents()

    def _on_monthly_row_clicked(self, row: int, _column: int):
        """点击月度收益行：资金曲线标红对应区间，交易明细筛选对应月份"""
        if not self._last_result or not self._last_result.snapshots:
            return

        month_item = self.monthly_table.item(row, 0)
        if not month_item:
            return

        selected_month = month_item.text()  # "YYYY-MM"

        # ── 1. 资金曲线：在对应月份区间叠加红色高亮线 ──
        self._highlight_month_on_equity(selected_month)

        # ── 2. 交易明细：筛选对应月份 ──
        self._filter_trades_by_month(selected_month)

        # 显示还原按钮
        self.monthly_reset_button.setVisible(True)

    def _highlight_month_on_equity(self, month_key: str):
        """在资金曲线上用红色高亮指定月份的区间"""
        result = self._last_result
        if not result:
            return

        # 清除之前的高亮
        self._remove_month_highlight()

        snapshots = result.snapshots
        month_x = []
        month_y = []
        for i, snapshot in enumerate(snapshots):
            if snapshot.date[:7] == month_key:
                month_x.append(i)
                month_y.append(snapshot.cumulative_return * 100)

        if month_x:
            highlight_pen = pg.mkPen(color=(255, 50, 50), width=3)
            self._month_highlight_curve = self.equity_plot.plot(
                month_x, month_y, pen=highlight_pen,
            )

    def _remove_month_highlight(self):
        """移除资金曲线上的月度高亮"""
        if hasattr(self, "_month_highlight_curve") and self._month_highlight_curve is not None:
            self.equity_plot.removeItem(self._month_highlight_curve)
            self._month_highlight_curve = None

    def _filter_trades_by_month(self, month_key: str):
        """交易明细表格只显示指定月份的交易"""
        result = self._last_result
        if not result:
            return

        filtered_trades = [t for t in result.trades if t.trade_date[:7] == month_key]
        self.trades_table.setRowCount(len(filtered_trades))

        for i, trade in enumerate(filtered_trades):
            self.trades_table.setItem(i, 0, QtWidgets.QTableWidgetItem(trade.trade_date))
            self.trades_table.setItem(i, 1, QtWidgets.QTableWidgetItem(trade.symbol))
            self.trades_table.setItem(i, 2, QtWidgets.QTableWidgetItem(trade.name))

            direction_item = QtWidgets.QTableWidgetItem("买入" if trade.action == "BUY" else "卖出")
            if trade.action == "BUY":
                direction_item.setForeground(QtGui.QColor("red"))
            else:
                direction_item.setForeground(QtGui.QColor("green"))
            self.trades_table.setItem(i, 3, direction_item)

            self.trades_table.setItem(i, 4, QtWidgets.QTableWidgetItem(f"{trade.price:.2f}"))
            self.trades_table.setItem(i, 5, QtWidgets.QTableWidgetItem(str(trade.quantity)))
            reason_item = QtWidgets.QTableWidgetItem(trade.reason)
            reason_item.setToolTip(trade.reason)
            self.trades_table.setItem(i, 6, reason_item)

        self.trades_table.resizeColumnsToContents()

    def _on_monthly_reset(self):
        """还原按钮：重置资金曲线高亮和交易明细"""
        self._remove_month_highlight()

        # 恢复完整交易明细
        if self._last_result:
            self._fill_trades_table(self._last_result)

        # 清除月度表格选中状态
        self.monthly_table.clearSelection()

        # 隐藏还原按钮
        self.monthly_reset_button.setVisible(False)

    # ── 资金曲线 hover 交互 ──────────────────────────────────

    def _restore_equity_hover_items(self):
        """clear() 后重新添加十字线和信息浮窗到 equity_plot"""
        if self._equity_crosshair is not None:
            self.equity_plot.addItem(self._equity_crosshair, ignoreBounds=True)
            self._equity_crosshair.setVisible(False)
        if self._equity_info_text is not None:
            self.equity_plot.addItem(self._equity_info_text, ignoreBounds=True)
            self._equity_info_text.setVisible(False)

    def _on_equity_mouse_moved(self, pos):
        """资金曲线鼠标移动事件：更新十字线和信息浮窗"""
        if self._equity_snapshots is None or not self._equity_snapshots:
            return

        vb = self.equity_plot.getViewBox()
        if not self.equity_plot.sceneBoundingRect().contains(pos):
            if self._equity_crosshair is not None:
                self._equity_crosshair.setVisible(False)
            if self._equity_info_text is not None:
                self._equity_info_text.setVisible(False)
            return

        mouse_point = vb.mapSceneToView(pos)
        x = mouse_point.x()
        index = int(round(x))

        if index < 0 or index >= len(self._equity_snapshots):
            if self._equity_crosshair is not None:
                self._equity_crosshair.setVisible(False)
            if self._equity_info_text is not None:
                self._equity_info_text.setVisible(False)
            return

        snapshot = self._equity_snapshots[index]

        # 更新十字线
        if self._equity_crosshair is not None:
            self._equity_crosshair.setPos(index)
            self._equity_crosshair.setVisible(True)

        # 构建信息文本
        info_lines = [
            f"  {snapshot.date}  ",
            f"  总资产: {snapshot.total_assets:,.0f}  ",
            f"  现金:   {snapshot.cash:,.0f}  ",
            f"  持仓值: {snapshot.holdings_value:,.0f}  ",
            f"  持仓数: {snapshot.holdings_count}只  仓位: {getattr(snapshot, 'position_ratio', 0) * 100:.1f}%  ",
            f"  日收益: {snapshot.daily_return * 100:+.2f}%  累计: {snapshot.cumulative_return * 100:+.2f}%  ",
        ]

        info_text = "\n".join(info_lines)

        if self._equity_info_text is not None:
            self._equity_info_text.setText(info_text)
            self._equity_info_text.setVisible(True)

            # 用背景色填充
            self._equity_info_text.fill = pg.mkBrush(255, 255, 255, 210)
            self._equity_info_text.border = pg.mkPen(color=(180, 180, 180), width=1)

            # 定位浮窗：放在十字线右侧，如果靠近右边则放左侧
            view_range = vb.viewRange()
            x_range = view_range[0]
            y_range = view_range[1]
            mid_x = (x_range[0] + x_range[1]) / 2

            y_top = y_range[1] - (y_range[1] - y_range[0]) * 0.02
            if index < mid_x:
                self._equity_info_text.setAnchor((0, 0))
                self._equity_info_text.setPos(index + 1, y_top)
            else:
                self._equity_info_text.setAnchor((1, 0))
                self._equity_info_text.setPos(index - 1, y_top)

    def _on_trades_table_cell_clicked(self, row: int, column: int):
        """点击交易明细表格任意单元格时，弹出该股票的图表弹窗"""
        if not self._last_result:
            return

        symbol_item = self.trades_table.item(row, 1)
        name_item = self.trades_table.item(row, 2)
        if not symbol_item:
            return

        symbol = symbol_item.text()
        stock_name = name_item.text() if name_item else ""

        # 获取点击行的交易日期
        date_item = self.trades_table.item(row, 0)
        focus_date = date_item.text() if date_item else ""

        # 收集该股票的所有交易记录
        symbol_trades = [t for t in self._last_result.trades if t.symbol == symbol]

        stock_daily_data_dir = self.root / "stock_daily_data"
        dialog = TradeDetailDialog(
            symbol=symbol,
            stock_name=stock_name,
            trades=symbol_trades,
            stock_daily_data_dir=stock_daily_data_dir,
            focus_date=focus_date,
            parent=self,
        )
        dialog.exec()

    # ── 导出功能 ──────────────────────────────────────────

    def _on_export_markdown(self):
        if not self._last_result:
            return

        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出 Markdown 报告", "backtest_report.md",
            "Markdown 文件 (*.md)",
        )
        if not file_path:
            return

        report = generate_markdown_report(self._last_result)
        Path(file_path).write_text(report, encoding="utf-8")
        self.statusMessageRequested.emit(f"报告已导出：{file_path}", 3000)

    def _on_export_csv(self):
        if not self._last_result:
            return

        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出 CSV 交易明细", "backtest_trades.csv",
            "CSV 文件 (*.csv)",
        )
        if not file_path:
            return

        export_trades_csv(self._last_result, file_path)
        self.statusMessageRequested.emit(f"交易明细已导出：{file_path}", 3000)

    # ── 页面切换 ──────────────────────────────────────────

    def _on_back_to_config(self):
        self.page_stack.setCurrentIndex(0)

    # ── 参数敏感性分析 ──────────────────────────────────────

    def _on_start_sensitivity(self):
        """启动参数敏感性分析"""
        if self.sell_strategy_combo.currentText() != "brick_chart":
            QtWidgets.QMessageBox.information(
                self, "提示",
                "参数敏感性分析仅支持砖形图（brick_chart）卖出策略。\n"
                "请先将卖出策略切换为 brick_chart。",
            )
            return

        config = self._build_config_from_form()
        if config is None:
            return

        dialog = SensitivityConfigDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return

        row_values, col_values = dialog.get_param_ranges()

        # 启动后台分析
        from core.backtest.sensitivity import run_sensitivity_analysis

        engine = BacktestEngine.from_root(self.root)

        self._sensitivity_worker = SensitivityWorker(
            engine, config,
            row_param_name="partial_profit_threshold",
            row_values=row_values,
            col_param_name="partial_sell_ratio",
            col_values=col_values,
        )

        progress_dialog = BacktestProgressDialog(self)
        progress_dialog.setWindowTitle("参数敏感性分析")
        progress_dialog.progressLabel.setText("正在进行参数网格搜索...")
        progress_dialog.stopRequested.connect(self._sensitivity_worker.cancel)

        self._sensitivity_progress = progress_dialog
        self.sensitivity_button.setEnabled(False)

        self._sensitivity_thread = start_worker(
            self,
            self._sensitivity_worker,
            on_progress=self._on_sensitivity_progress,
            on_finished=self._on_sensitivity_finished,
            on_error=self._on_sensitivity_error,
            on_cleanup=self._on_sensitivity_cleanup,
        )

        progress_dialog.show()

    def _on_sensitivity_progress(self, payload: dict):
        if self._sensitivity_progress:
            current = payload.get("current", 0)
            total = payload.get("total", 1)
            row_val = payload.get("row_value", 0)
            col_val = payload.get("col_value", 0)
            self._sensitivity_progress.progressBar.setMaximum(max(total, 1))
            self._sensitivity_progress.progressBar.setValue(min(current, total))
            self._sensitivity_progress.progressLabel.setText(
                f"网格搜索：{current} / {total} 组参数"
            )
            self._sensitivity_progress.dateLabel.setText(
                f"止盈阈值={row_val * 100:.1f}%  卖出比例={col_val * 100:.0f}%"
            )
            total_return = payload.get("total_return", 0)
            sharpe = payload.get("sharpe_ratio", 0)
            self._sensitivity_progress.statsLabel.setText(
                f"当前组合：收益率 {total_return * 100:+.2f}%  夏普 {sharpe:.2f}"
            )

    def _on_sensitivity_finished(self, payload: dict):
        from core.backtest.sensitivity import SensitivityResult

        result: SensitivityResult = payload.get("result")
        if result is None:
            return

        if self._sensitivity_progress:
            self._sensitivity_progress.mark_finished("参数敏感性分析完成！")

        # 展示结果对话框
        result_dialog = SensitivityResultDialog(result, self)
        result_dialog.exec()

        self.statusMessageRequested.emit("参数敏感性分析完成", 5000)

    def _on_sensitivity_error(self, error_msg: str):
        if self._sensitivity_progress:
            self._sensitivity_progress.mark_finished(f"分析失败：{error_msg}")
        QtWidgets.QMessageBox.critical(self, "分析失败", error_msg)

    def _on_sensitivity_cleanup(self):
        self._sensitivity_thread = None
        self._sensitivity_worker = None
        self._sensitivity_progress = None
        self.sensitivity_button.setEnabled(True)


# ── 参数敏感性分析 Worker ──────────────────────────────────

class SensitivityWorker(QtCore.QObject):
    """参数敏感性分析后台 Worker"""

    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(
        self,
        engine: BacktestEngine,
        config: BacktestConfig,
        row_param_name: str,
        row_values: list[float],
        col_param_name: str,
        col_values: list[float],
    ):
        super().__init__()
        self.engine = engine
        self.config = config
        self.row_param_name = row_param_name
        self.row_values = row_values
        self.col_param_name = col_param_name
        self.col_values = col_values
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @QtCore.Slot()
    def run(self):
        from core.backtest.sensitivity import run_sensitivity_analysis

        try:
            result = run_sensitivity_analysis(
                engine=self.engine,
                base_config=self.config,
                row_param_name=self.row_param_name,
                row_values=self.row_values,
                col_param_name=self.col_param_name,
                col_values=self.col_values,
                progress_callback=lambda p: self.progressChanged.emit(p),
                cancelled_fn=lambda: self._cancelled,
            )
            self.finished.emit({"result": result})
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


# ── 参数敏感性分析配置对话框 ──────────────────────────────

class SensitivityConfigDialog(QtWidgets.QDialog):
    """参数敏感性分析参数配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("参数敏感性分析配置")
        self.setModal(True)
        self.resize(400, 320)

        layout = QtWidgets.QVBoxLayout(self)

        info_label = QtWidgets.QLabel(
            "网格搜索不同「分批止盈阈值」和「分批卖出比例」的组合，\n"
            "找到最优参数。搜索范围越大，耗时越长。"
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        form = QtWidgets.QFormLayout()

        # 止盈阈值范围
        form.addRow(QtWidgets.QLabel("── 分批止盈阈值 ──"))

        self.threshold_min = QtWidgets.QDoubleSpinBox()
        self.threshold_min.setRange(0.5, 50.0)
        self.threshold_min.setValue(2.0)
        self.threshold_min.setSuffix(" %")
        self.threshold_min.setSingleStep(0.5)
        form.addRow("最小值：", self.threshold_min)

        self.threshold_max = QtWidgets.QDoubleSpinBox()
        self.threshold_max.setRange(0.5, 50.0)
        self.threshold_max.setValue(10.0)
        self.threshold_max.setSuffix(" %")
        self.threshold_max.setSingleStep(0.5)
        form.addRow("最大值：", self.threshold_max)

        self.threshold_step = QtWidgets.QDoubleSpinBox()
        self.threshold_step.setRange(0.5, 10.0)
        self.threshold_step.setValue(1.0)
        self.threshold_step.setSuffix(" %")
        self.threshold_step.setSingleStep(0.5)
        form.addRow("步长：", self.threshold_step)

        # 卖出比例范围
        form.addRow(QtWidgets.QLabel("── 分批卖出比例 ──"))

        self.ratio_min = QtWidgets.QSpinBox()
        self.ratio_min.setRange(5, 100)
        self.ratio_min.setValue(10)
        self.ratio_min.setSuffix(" %")
        form.addRow("最小值：", self.ratio_min)

        self.ratio_max = QtWidgets.QSpinBox()
        self.ratio_max.setRange(5, 100)
        self.ratio_max.setValue(50)
        self.ratio_max.setSuffix(" %")
        form.addRow("最大值：", self.ratio_max)

        self.ratio_step = QtWidgets.QSpinBox()
        self.ratio_step.setRange(5, 50)
        self.ratio_step.setValue(10)
        self.ratio_step.setSuffix(" %")
        form.addRow("步长：", self.ratio_step)

        layout.addLayout(form)

        # 预估组合数
        self.estimate_label = QtWidgets.QLabel()
        layout.addWidget(self.estimate_label)
        self._update_estimate()

        self.threshold_min.valueChanged.connect(self._update_estimate)
        self.threshold_max.valueChanged.connect(self._update_estimate)
        self.threshold_step.valueChanged.connect(self._update_estimate)
        self.ratio_min.valueChanged.connect(self._update_estimate)
        self.ratio_max.valueChanged.connect(self._update_estimate)
        self.ratio_step.valueChanged.connect(self._update_estimate)

        # 按钮
        button_layout = QtWidgets.QHBoxLayout()
        self.ok_button = QtWidgets.QPushButton("开始分析")
        self.cancel_button = QtWidgets.QPushButton("取消")
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def _update_estimate(self):
        row_values, col_values = self.get_param_ranges()
        total = len(row_values) * len(col_values)
        self.estimate_label.setText(
            f"预计搜索 {len(row_values)} × {len(col_values)} = {total} 组参数"
        )

    def get_param_ranges(self) -> tuple[list[float], list[float]]:
        """返回行参数和列参数的值列表"""
        threshold_min = self.threshold_min.value() / 100.0
        threshold_max = self.threshold_max.value() / 100.0
        threshold_step = self.threshold_step.value() / 100.0

        ratio_min = self.ratio_min.value() / 100.0
        ratio_max = self.ratio_max.value() / 100.0
        ratio_step = self.ratio_step.value() / 100.0

        row_values = []
        current = threshold_min
        while current <= threshold_max + 1e-9:
            row_values.append(round(current, 4))
            current += threshold_step

        col_values = []
        current = ratio_min
        while current <= ratio_max + 1e-9:
            col_values.append(round(current, 4))
            current += ratio_step

        return row_values, col_values


# ── 参数敏感性分析结果对话框 ──────────────────────────────

class SensitivityResultDialog(QtWidgets.QDialog):
    """参数敏感性分析结果展示对话框"""

    def __init__(self, result, parent=None):
        from core.backtest.sensitivity import SensitivityResult

        super().__init__(parent)
        self.result: SensitivityResult = result
        self.setWindowTitle("参数敏感性分析结果")
        self.setModal(False)
        self.resize(700, 500)

        layout = QtWidgets.QVBoxLayout(self)

        # 最优参数提示
        if result.best_cell:
            best = result.best_cell
            best_label = QtWidgets.QLabel(
                f"🏆 最优参数（按夏普比率）：止盈阈值 = {best.row_value * 100:.1f}%，"
                f"卖出比例 = {best.col_value * 100:.0f}%\n"
                f"总收益率 {best.total_return * 100:+.2f}%  "
                f"夏普比率 {best.sharpe_ratio:.2f}  "
                f"最大回撤 {best.max_drawdown * 100:.2f}%  "
                f"胜率 {best.win_rate * 100:.1f}%"
            )
            best_label.setWordWrap(True)
            best_font = QtGui.QFont()
            best_font.setBold(True)
            best_label.setFont(best_font)
            layout.addWidget(best_label)

        # 指标选择
        metric_layout = QtWidgets.QHBoxLayout()
        metric_layout.addWidget(QtWidgets.QLabel("展示指标："))
        self.metric_combo = QtWidgets.QComboBox()
        self.metric_combo.addItems([
            "总收益率", "年化收益率", "夏普比率", "最大回撤", "胜率", "交易次数",
        ])
        self.metric_combo.setCurrentIndex(0)
        self.metric_combo.currentIndexChanged.connect(self._refresh_table)
        metric_layout.addWidget(self.metric_combo)
        metric_layout.addStretch()
        layout.addLayout(metric_layout)

        # 矩阵表格
        self.matrix_table = QtWidgets.QTableWidget()
        self.matrix_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.matrix_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.matrix_table)

        # 关闭按钮
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

        self._refresh_table()

    def _refresh_table(self):
        """根据选中的指标刷新矩阵表格"""
        result = self.result
        metric_name = self.metric_combo.currentText()

        col_headers = [f"{v * 100:.0f}%" for v in result.col_values]
        row_headers = [f"{v * 100:.1f}%" for v in result.row_values]

        self.matrix_table.setColumnCount(len(col_headers))
        self.matrix_table.setRowCount(len(row_headers))
        self.matrix_table.setHorizontalHeaderLabels(col_headers)
        self.matrix_table.setVerticalHeaderLabels(row_headers)

        # 设置表头标签
        self.matrix_table.horizontalHeader().setDefaultSectionSize(80)
        self.matrix_table.verticalHeader().setDefaultSectionSize(30)

        # 收集所有值用于颜色映射
        all_values = []
        for row_cells in result.cells:
            for cell in row_cells:
                all_values.append(self._get_cell_value(cell, metric_name))

        min_val = min(all_values) if all_values else 0
        max_val = max(all_values) if all_values else 1
        value_range = max_val - min_val if max_val != min_val else 1

        for row_idx, row_cells in enumerate(result.cells):
            for col_idx, cell in enumerate(row_cells):
                value = self._get_cell_value(cell, metric_name)
                text = self._format_cell_value(value, metric_name)

                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)

                # 热力图着色：绿色（好）→ 红色（差）
                normalized = (value - min_val) / value_range
                # 对于回撤，值越大越差，需要反转
                if metric_name == "最大回撤":
                    normalized = 1 - normalized
                color = self._value_to_color(normalized)
                item.setBackground(color)

                # 标记最优单元格
                if (result.best_cell
                        and cell.row_value == result.best_cell.row_value
                        and cell.col_value == result.best_cell.col_value):
                    item.setFont(QtGui.QFont("", -1, QtGui.QFont.Bold))

                self.matrix_table.setItem(row_idx, col_idx, item)

        self.matrix_table.resizeColumnsToContents()

    @staticmethod
    def _get_cell_value(cell, metric_name: str) -> float:
        metric_map = {
            "总收益率": cell.total_return,
            "年化收益率": cell.annual_return,
            "夏普比率": cell.sharpe_ratio,
            "最大回撤": cell.max_drawdown,
            "胜率": cell.win_rate,
            "交易次数": float(cell.total_trades),
        }
        return metric_map.get(metric_name, 0.0)

    @staticmethod
    def _format_cell_value(value: float, metric_name: str) -> str:
        if metric_name in ("总收益率", "年化收益率", "最大回撤", "胜率"):
            return f"{value * 100:+.1f}%" if metric_name != "最大回撤" else f"{value * 100:.1f}%"
        if metric_name == "夏普比率":
            return f"{value:.2f}"
        if metric_name == "交易次数":
            return str(int(value))
        return f"{value:.2f}"

    @staticmethod
    def _value_to_color(normalized: float) -> QtGui.QColor:
        """将 0~1 的归一化值映射为热力图颜色（红→黄→绿）"""
        normalized = max(0.0, min(1.0, normalized))
        if normalized < 0.5:
            # 红色 → 黄色
            ratio = normalized * 2
            red = 255
            green = int(200 * ratio)
            blue = int(80 * ratio)
        else:
            # 黄色 → 绿色
            ratio = (normalized - 0.5) * 2
            red = int(255 * (1 - ratio))
            green = int(200 + 55 * ratio)
            blue = int(80 + 100 * ratio)
        return QtGui.QColor(red, green, blue, 60)
