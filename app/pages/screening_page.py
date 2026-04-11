from __future__ import annotations

import random
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from core.models.trade import TradeAction
from core.screening.service import ScreeningService
from core.templates import TemplateService
from core.trade.simulator import TradeSimulator

from ..data_loader import load_daily_csv, load_stock_list
from ..utils import start_worker
from ..widgets import ScreeningProgressDialog, StockChartWidget


class ScreeningWorker(QtCore.QObject):
    """选股后台任务 Worker"""

    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, screening_service: ScreeningService, request):
        super().__init__()
        self.screening_service = screening_service
        self.request = request
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @QtCore.Slot()
    def run(self):
        try:
            payload = self.screening_service.screen_with_cache(
                self.request,
                progress_callback=lambda p: self.progressChanged.emit(p),
                cancelled_fn=lambda: self._cancelled,
            )
            self.finished.emit(payload)
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


class ScreeningPage(QtWidgets.QWidget):
    """选股视图页面：配置态（选日期+模板）→ 选股 → 结果态（股票列表+图表）"""

    statusMessageRequested = QtCore.Signal(str, int)

    CHART_FIXED_DAYS = 90

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.stocklist_csv = self.root / "stocklist.csv"
        self.stock_daily_data_dir = self.root / "stock_daily_data"

        self.screening_service = ScreeningService.from_root(self.root)
        self.template_service = TemplateService.from_root(self.root)

        self._screening_thread: QtCore.QThread | None = None
        self._screening_worker: ScreeningWorker | None = None
        self._screening_progress_dialog: ScreeningProgressDialog | None = None
        self._screening_matches: list = []
        self._target_date: str = ""

        # ── 模拟交易状态 ──
        self._simulator = TradeSimulator()
        self._initial_capital: int = 100_000
        self._available_capital: float = 100_000.0
        self._current_sim_date: str = ""
        self._current_symbol: str = ""
        self._current_stock_name: str = ""
        self._current_df = None
        self._trade_marker_items: list = []
        self._is_at_open: bool = False  # 当前是否处于开盘阶段

        self._setup_ui()
        self._connect_signals()
        self.reload_templates()

    # ── UI 构建 ──────────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.page_stack = QtWidgets.QStackedWidget()
        main_layout.addWidget(self.page_stack)

        self.page_stack.addWidget(self._build_config_panel())
        self.page_stack.addWidget(self._build_result_panel())

        self.page_stack.setCurrentIndex(0)

    def _build_config_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        outer_layout = QtWidgets.QVBoxLayout(panel)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        outer_layout.addStretch(1)

        form_container = QtWidgets.QWidget()
        form_container.setFixedWidth(400)
        form_layout = QtWidgets.QVBoxLayout(form_container)
        form_layout.setSpacing(16)

        title = QtWidgets.QLabel("选股配置")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(QtCore.Qt.AlignCenter)
        form_layout.addWidget(title)

        form_layout.addSpacing(8)

        # 日期选择器 + 随机按钮
        date_label = QtWidgets.QLabel("选股日期：")
        form_layout.addWidget(date_label)

        date_row = QtWidgets.QHBoxLayout()
        self.date_edit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.random_date_btn = QtWidgets.QPushButton("随机")
        self.random_date_btn.setFixedWidth(60)
        date_row.addWidget(self.date_edit, 1)
        date_row.addWidget(self.random_date_btn)
        form_layout.addLayout(date_row)

        form_layout.addSpacing(8)

        # 条件模板下拉
        template_label = QtWidgets.QLabel("条件模板：")
        form_layout.addWidget(template_label)

        self.template_box = QtWidgets.QComboBox()
        form_layout.addWidget(self.template_box)

        form_layout.addSpacing(16)

        # 确认按钮
        self.confirm_btn = QtWidgets.QPushButton("开始选股")
        self.confirm_btn.setMinimumHeight(36)
        form_layout.addWidget(self.confirm_btn)

        center_layout = QtWidgets.QHBoxLayout()
        center_layout.addStretch(1)
        center_layout.addWidget(form_container)
        center_layout.addStretch(1)
        outer_layout.addLayout(center_layout)

        outer_layout.addStretch(1)

        return panel

    def _build_result_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        left_panel = self._build_left_panel()
        self.chart = StockChartWidget()
        self._disable_chart_interaction()
        trade_panel = self._build_trade_panel()

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left_panel)
        splitter.addWidget(self.chart)
        splitter.addWidget(trade_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 880, 160])

        layout.addWidget(splitter)
        return panel

    def _build_left_panel(self) -> QtWidgets.QWidget:
        """构建左侧面板：选股结果列表 + 持有股票列表 + 返回按钮"""
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)

        # 选股结果区域
        result_label = QtWidgets.QLabel("选股结果")
        result_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(result_label)

        self.result_table = QtWidgets.QTableWidget()
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["代码", "名称"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        left_layout.addWidget(self.result_table, 1)

        # 持有股票区域
        holding_label = QtWidgets.QLabel("持有股票")
        holding_label.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(holding_label)

        self.holding_table = QtWidgets.QTableWidget()
        self.holding_table.setColumnCount(6)
        self.holding_table.setHorizontalHeaderLabels(
            ["代码", "名称", "数量", "成本", "现价", "盈亏%"]
        )
        self.holding_table.horizontalHeader().setStretchLastSection(True)
        self.holding_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.holding_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        left_layout.addWidget(self.holding_table, 1)

        # 底部按钮行：返回配置 + 重置
        bottom_row = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("返回配置")
        self.reset_btn = QtWidgets.QPushButton("🔄 重置")
        bottom_row.addWidget(self.back_btn)
        bottom_row.addWidget(self.reset_btn)
        left_layout.addLayout(bottom_row)

        return left

    def _build_trade_panel(self) -> QtWidgets.QWidget:
        """构建右侧操作面板：模拟交易控制区"""
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(160)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题
        title = QtWidgets.QLabel("📅 模拟交易")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        layout.addWidget(self._make_separator())

        # ── 初始配置区：金额输入 + 开始训练按钮 ──
        self.trade_setup_widget = QtWidgets.QWidget()
        setup_layout = QtWidgets.QVBoxLayout(self.trade_setup_widget)
        setup_layout.setContentsMargins(0, 0, 0, 0)

        setup_layout.addWidget(QtWidgets.QLabel("初始资金"))
        self.initial_capital_input = QtWidgets.QSpinBox()
        self.initial_capital_input.setRange(10_000, 100_000_000)
        self.initial_capital_input.setSingleStep(10_000)
        self.initial_capital_input.setValue(100_000)
        self.initial_capital_input.setPrefix("¥ ")
        self.initial_capital_input.setGroupSeparatorShown(True)
        setup_layout.addWidget(self.initial_capital_input)

        setup_layout.addSpacing(12)

        self.start_training_btn = QtWidgets.QPushButton("🚀 开始训练")
        self.start_training_btn.setMinimumHeight(36)
        self.start_training_btn.setStyleSheet(
            "background-color: #1890FF; color: white; font-weight: bold; font-size: 13px;"
        )
        self.start_training_btn.clicked.connect(self._on_start_training)
        setup_layout.addWidget(self.start_training_btn)

        layout.addWidget(self.trade_setup_widget)

        # ── 交易操作区：选股后点击"开始训练"才显示 ──
        self.trade_ops_widget = QtWidgets.QWidget()
        ops_layout = QtWidgets.QVBoxLayout(self.trade_ops_widget)
        ops_layout.setContentsMargins(0, 0, 0, 0)

        # 可用资金
        ops_layout.addWidget(QtWidgets.QLabel("可用资金"))
        self.available_capital_label = QtWidgets.QLabel("¥ 100,000")
        self.available_capital_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #1890FF;")
        ops_layout.addWidget(self.available_capital_label)

        ops_layout.addWidget(self._make_separator())

        # 当前日期
        ops_layout.addWidget(QtWidgets.QLabel("当前日期"))
        self.sim_date_label = QtWidgets.QLabel("--")
        self.sim_date_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        ops_layout.addWidget(self.sim_date_label)

        # 当前价格
        ops_layout.addWidget(QtWidgets.QLabel("当前价格"))
        self.sim_price_label = QtWidgets.QLabel("--")
        self.sim_price_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        ops_layout.addWidget(self.sim_price_label)

        ops_layout.addSpacing(8)

        # 下一天 / 快进到收盘 按钮
        self.next_day_btn = QtWidgets.QPushButton("▶ 下一天")
        self.next_day_btn.setEnabled(False)
        self.next_day_btn.clicked.connect(self._on_advance_day)
        ops_layout.addWidget(self.next_day_btn)

        ops_layout.addWidget(self._make_separator())

        # 买入操作
        ops_layout.addWidget(QtWidgets.QLabel("买入数量(股)"))
        self.buy_quantity_input = QtWidgets.QSpinBox()
        self.buy_quantity_input.setRange(100, 1_000_000)
        self.buy_quantity_input.setSingleStep(100)
        self.buy_quantity_input.setValue(100)
        ops_layout.addWidget(self.buy_quantity_input)

        self.buy_open_btn = QtWidgets.QPushButton("🟢 开盘价买入")
        self.buy_open_btn.setStyleSheet(
            "background-color: #CC3333; color: white; font-weight: bold;"
        )
        self.buy_open_btn.setEnabled(False)
        self.buy_open_btn.clicked.connect(self._on_buy_open)
        ops_layout.addWidget(self.buy_open_btn)

        self.buy_close_btn = QtWidgets.QPushButton("🟢 收盘价买入")
        self.buy_close_btn.setStyleSheet(
            "background-color: #CC3333; color: white; font-weight: bold;"
        )
        self.buy_close_btn.setEnabled(False)
        self.buy_close_btn.clicked.connect(self._on_buy_close)
        ops_layout.addWidget(self.buy_close_btn)

        ops_layout.addWidget(self._make_separator())

        # 卖出操作
        ops_layout.addWidget(QtWidgets.QLabel("卖出数量(股)"))
        self.sell_quantity_input = QtWidgets.QSpinBox()
        self.sell_quantity_input.setRange(100, 1_000_000)
        self.sell_quantity_input.setSingleStep(100)
        self.sell_quantity_input.setValue(100)
        ops_layout.addWidget(self.sell_quantity_input)

        self.sell_btn = QtWidgets.QPushButton("🔴 卖出")
        self.sell_btn.setStyleSheet(
            "background-color: #33AA33; color: white; font-weight: bold;"
        )
        self.sell_btn.setEnabled(False)
        self.sell_btn.clicked.connect(self._on_sell)
        ops_layout.addWidget(self.sell_btn)

        ops_layout.addWidget(self._make_separator())

        # 汇总信息
        ops_layout.addWidget(QtWidgets.QLabel("总投入"))
        self.total_cost_label = QtWidgets.QLabel("¥ 0.00")
        ops_layout.addWidget(self.total_cost_label)

        ops_layout.addWidget(QtWidgets.QLabel("总市值"))
        self.total_value_label = QtWidgets.QLabel("¥ 0.00")
        ops_layout.addWidget(self.total_value_label)

        ops_layout.addWidget(QtWidgets.QLabel("总盈亏"))
        self.total_pnl_label = QtWidgets.QLabel("0.00%")
        ops_layout.addWidget(self.total_pnl_label)

        ops_layout.addSpacing(8)

        # 结算按钮
        self.settle_btn = QtWidgets.QPushButton("💰 结算")
        self.settle_btn.clicked.connect(self._on_settle)
        ops_layout.addWidget(self.settle_btn)

        self.trade_ops_widget.setVisible(False)
        layout.addWidget(self.trade_ops_widget)

        layout.addStretch()

        return panel

    def _on_start_training(self):
        """点击开始训练：记录初始资金，切换到交易操作区"""
        self._initial_capital = self.initial_capital_input.value()
        self._available_capital = self._initial_capital
        self.available_capital_label.setText(f"¥ {self._available_capital:,.2f}")

        self.trade_setup_widget.setVisible(False)
        self.trade_ops_widget.setVisible(True)

    @staticmethod
    def _make_separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    def _disable_chart_interaction(self):
        """禁用图表的拖动和缩放，仅保留十字光标悬停"""
        for plot in (self.chart.pricePlot, self.chart.volPlot,
                     self.chart.brickPlot, self.chart.kdjPlot):
            viewbox = plot.getViewBox()
            viewbox.setMouseEnabled(x=False, y=False)
            viewbox.setMenuEnabled(False)

    # ── 信号连接 ─────────────────────────────────────────────

    def _connect_signals(self):
        self.random_date_btn.clicked.connect(self._on_random_date)
        self.confirm_btn.clicked.connect(self._on_confirm)
        self.result_table.itemSelectionChanged.connect(self._on_stock_selected)
        self.holding_table.itemSelectionChanged.connect(self._on_holding_selected)
        self.back_btn.clicked.connect(self._on_back_to_config)
        self.reset_btn.clicked.connect(self._on_reset)

    # ── 配置态操作 ────────────────────────────────────────────

    def reload_templates(self):
        """刷新模板下拉列表"""
        current_data = self.template_box.currentData()
        self.template_box.blockSignals(True)
        self.template_box.clear()

        templates = self.template_service.list_templates()
        selected_index = -1
        for index, template in enumerate(templates):
            self.template_box.addItem(template.name, template.id)
            if current_data and template.id == current_data:
                selected_index = index

        if selected_index >= 0:
            self.template_box.setCurrentIndex(selected_index)
        elif templates:
            self.template_box.setCurrentIndex(0)

        self.template_box.blockSignals(False)

    def _on_random_date(self):
        """从多只股票交叉验证，随机选取一个确认开市的 A 股交易日

        策略：取多只股票的交易日交集，确保该日期 A 股市场确实开市。
        限制：>= 2020-01-01，排除最近 60 个交易日，不超过今天。
        """
        try:
            import pandas as pd

            df_list = load_stock_list(self.stocklist_csv)
            if df_list.empty:
                return

            # 取多只样本股票做交叉验证（最多取 5 只，确保覆盖面）
            sample_symbols = [
                str(s).zfill(6)
                for s in df_list["symbol"].head(5).tolist()
            ]

            date_sets: list[set[str]] = []
            for symbol in sample_symbols:
                try:
                    df_daily = load_daily_csv(self.stock_daily_data_dir, symbol)
                except FileNotFoundError:
                    continue
                if df_daily.empty or "date" not in df_daily.columns:
                    continue
                date_strs = {
                    d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                    for d in df_daily["date"].tolist()
                }
                date_sets.append(date_strs)

            if not date_sets:
                return

            # 取所有样本股票交易日的交集 → A 股真正开市的日期
            trading_dates = date_sets[0]
            for ds in date_sets[1:]:
                trading_dates = trading_dates & ds

            if not trading_dates:
                return

            earliest_allowed = "2020-01-01"
            today = pd.Timestamp.now().strftime("%Y-%m-%d")

            # 排序后排除最近 60 个交易日，并限制范围
            sorted_dates = sorted(trading_dates)
            if len(sorted_dates) > self.CHART_FIXED_DAYS:
                sorted_dates = sorted_dates[:-self.CHART_FIXED_DAYS]
            else:
                return

            candidates = [
                d for d in sorted_dates
                if d >= earliest_allowed and d <= today
            ]
            if not candidates:
                return

            random_date_str = random.choice(candidates)
            qdate = QtCore.QDate.fromString(random_date_str, "yyyy-MM-dd")
            if qdate.isValid():
                self.date_edit.setDate(qdate)
        except FileNotFoundError:
            pass

    # ── 选股执行 ──────────────────────────────────────────────

    def _on_confirm(self):
        """点击确认：校验参数 → 弹出进度弹窗 → 执行选股"""
        if self._screening_thread is not None:
            QtWidgets.QMessageBox.information(self, "提示", "已有选股任务正在进行中")
            return

        template_id = str(self.template_box.currentData() or "").strip()
        if not template_id:
            QtWidgets.QMessageBox.warning(self, "提示", "请选择条件模板")
            return

        self._target_date = self.date_edit.date().toString("yyyy-MM-dd")

        try:
            request = self.template_service.build_screening_request(
                template_id, self._target_date
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "选股失败", str(exc))
            return

        self.confirm_btn.setEnabled(False)

        # 创建并显示选股进度弹窗
        self._screening_progress_dialog = ScreeningProgressDialog(
            self.window() if isinstance(self.window(), QtWidgets.QWidget) else self
        )
        self._screening_progress_dialog.stopRequested.connect(self._on_screening_stop)
        self._screening_progress_dialog.show()

        # 启动后台线程
        self._screening_worker = ScreeningWorker(self.screening_service, request)
        self._screening_thread = start_worker(
            self,
            self._screening_worker,
            on_progress=self._on_screening_progress,
            on_finished=self._on_screening_finished,
            on_error=self._on_screening_error,
            on_cleanup=self._cleanup_screening,
        )

    def _on_screening_stop(self):
        if self._screening_worker is not None:
            self._screening_worker.cancel()

    def _on_screening_progress(self, payload: dict):
        if self._screening_progress_dialog is not None:
            self._screening_progress_dialog.update_progress(payload)

    def _on_screening_finished(self, payload: dict):
        result = payload["result"]
        cache_hit = payload.get("cache_hit", False)
        resumed = payload.get("resumed", False)
        was_cancelled = (
            self._screening_worker is not None and self._screening_worker._cancelled
        )

        # 收集命中的股票
        self._screening_matches = [
            match for match in result.matches if match.matched
        ]

        if cache_hit:
            summary = f"命中缓存：直接返回 {result.matched_count} 只命中股票"
        elif resumed:
            summary = f"从缓存断点续选完成：命中 {result.matched_count} 只"
        elif was_cancelled:
            summary = f"选股已停止：已处理部分中命中 {result.matched_count} 只"
        else:
            summary = payload["summary"]

        self.statusMessageRequested.emit(summary, 5000)

        # 关闭进度弹窗
        if self._screening_progress_dialog is not None:
            if was_cancelled:
                self._screening_progress_dialog.mark_finished(summary)
            else:
                self._screening_progress_dialog.accept()

        # 展示结果
        if self._screening_matches:
            self._show_results()
        else:
            QtWidgets.QMessageBox.information(
                self, "选股完成", "未找到符合条件的股票"
            )

    def _on_screening_error(self, message: str):
        if self._screening_progress_dialog is not None:
            self._screening_progress_dialog.mark_finished(f"选股失败：{message}")
        QtWidgets.QMessageBox.warning(self, "选股失败", message)
        self.statusMessageRequested.emit(f"选股失败：{message}", 5000)

    def _cleanup_screening(self):
        self._screening_thread = None
        self._screening_worker = None
        self._screening_progress_dialog = None
        self.confirm_btn.setEnabled(True)

    # ── 结果态操作 ────────────────────────────────────────────

    def _show_results(self):
        """填充股票列表并切换到结果面板"""
        self.result_table.setRowCount(len(self._screening_matches))
        for row, match in enumerate(self._screening_matches):
            self.result_table.setItem(
                row, 0, QtWidgets.QTableWidgetItem(str(match.symbol))
            )
            self.result_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(str(match.name or ""))
            )
        self.result_table.resizeColumnsToContents()

        # 初始化模拟交易日期为选股目标日期
        self._current_sim_date = self._target_date
        self._is_at_open = False
        self._simulator.reset()
        self._trade_marker_items.clear()
        self.holding_table.setRowCount(0)

        # 切换到结果态
        self.page_stack.setCurrentIndex(1)

        # 默认选中第一只股票
        if self.result_table.rowCount() > 0:
            self.result_table.selectRow(0)

    def _on_stock_selected(self):
        """点击选股结果列表项，加载该股票数据并刷新图表"""
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self._screening_matches):
            return

        match = self._screening_matches[row]
        self._current_symbol = str(match.symbol)
        self._current_stock_name = str(match.name or "")
        self._load_chart_for_current_symbol()

    def _on_holding_selected(self):
        """持有股票列表选中事件：切换图表到该股票"""
        row = self.holding_table.currentRow()
        if row < 0:
            return

        symbol_item = self.holding_table.item(row, 0)
        name_item = self.holding_table.item(row, 1)
        if symbol_item is None:
            return

        self._current_symbol = symbol_item.text()
        self._current_stock_name = name_item.text() if name_item else ""
        self._load_chart_for_current_symbol()

    def _load_chart_for_current_symbol(self):
        """加载当前股票截止到模拟日期的数据并刷新图表"""
        symbol = self._current_symbol
        if not symbol or not self._current_sim_date:
            return

        try:
            df_daily = load_daily_csv(self.stock_daily_data_dir, symbol)
            if df_daily.empty:
                self.statusMessageRequested.emit(
                    f"{symbol} 暂无本地日线数据", 3000
                )
                return

            df_up_to_date = df_daily[
                df_daily["date"] <= self._current_sim_date
            ].copy()
            df_up_to_date = df_up_to_date.reset_index(drop=True)

            if df_up_to_date.empty:
                self.statusMessageRequested.emit(
                    f"{symbol} 在 {self._current_sim_date} 之前无数据", 3000
                )
                return

            # 开盘阶段：最后一根K线用开盘价替代收盘价
            if self._is_at_open:
                df_display = df_up_to_date.copy()
                last_idx = len(df_display) - 1
                open_price = df_display.at[last_idx, "open"]
                df_display.at[last_idx, "close"] = open_price
                df_display.at[last_idx, "high"] = open_price
                df_display.at[last_idx, "low"] = open_price
            else:
                df_display = df_up_to_date

            self._current_df = df_up_to_date
            self.chart.set_daily(df_display)
            self._set_chart_visible_range(df_display)
            self._update_sim_info()
            self._redraw_trade_markers()

            # 启用交易按钮：根据开盘/收盘阶段控制
            self.next_day_btn.setEnabled(True)
            self.sell_btn.setEnabled(True)
            is_first_day = self._current_sim_date == self._target_date
            if self._is_at_open:
                self.buy_open_btn.setVisible(True)
                self.buy_open_btn.setEnabled(True)
                self.buy_close_btn.setVisible(False)
            else:
                self.buy_open_btn.setVisible(False)
                self.buy_close_btn.setVisible(True)
                self.buy_close_btn.setEnabled(True)

            display_days = min(len(df_up_to_date), self.CHART_FIXED_DAYS)
            self.statusMessageRequested.emit(
                f"{symbol} {self._current_stock_name}  "
                f"展示最近 {display_days} 个交易日", 2000
            )
        except Exception:
            self.statusMessageRequested.emit(
                f"{symbol} 数据加载失败", 3000
            )

    def _set_chart_visible_range(self, df_up_to_date):
        """设置图表的 X 轴可见范围为最后 CHART_FIXED_DAYS 个交易日"""
        total_bars = len(df_up_to_date)
        visible_start = max(0, total_bars - self.CHART_FIXED_DAYS)
        half_width = self.chart._item_half_width
        right_padding = self.chart._right_view_padding
        x_left = visible_start - half_width
        x_right = (total_bars - 1) + half_width + right_padding
        self.chart.pricePlot.setXRange(x_left, x_right, padding=0)

        for plot in (self.chart.pricePlot, self.chart.volPlot,
                     self.chart.brickPlot, self.chart.kdjPlot):
            plot.getViewBox().setLimits(
                xMin=x_left,
                xMax=x_right,
                minXRange=x_right - x_left,
                maxXRange=x_right - x_left,
            )

    # ── 模拟交易操作 ─────────────────────────────────────────

    def _on_advance_day(self):
        """下一天/快进到收盘：根据当前阶段切换"""
        if self._is_at_open:
            self._advance_to_close()
        else:
            self._advance_to_next_open()

    def _advance_to_next_open(self):
        """推进到下一个交易日的开盘阶段"""
        if not self._current_symbol or not self._current_sim_date:
            return

        try:
            df_full = load_daily_csv(
                self.stock_daily_data_dir, self._current_symbol
            )
        except FileNotFoundError:
            return

        current_mask = df_full["date"] <= self._current_sim_date
        current_count = current_mask.sum()
        if current_count >= len(df_full):
            self.next_day_btn.setEnabled(False)
            self.statusMessageRequested.emit("已无更多交易日数据", 3000)
            return

        next_row = df_full.iloc[current_count]
        next_date = next_row["date"]
        self._current_sim_date = (
            next_date.strftime("%Y-%m-%d")
            if hasattr(next_date, "strftime")
            else str(next_date)[:10]
        )

        # 进入开盘阶段
        self._is_at_open = True
        self.next_day_btn.setText("⏩ 快进到收盘")

        # 加载数据并用开盘价替代收盘价
        self._load_chart_for_current_symbol()

        # 更新所有持仓的当前价格（开盘阶段用开盘价）
        self._update_holding_prices()
        self._refresh_trade_summary()

    def _advance_to_close(self):
        """快进到当天收盘，展示完整K线数据"""
        self._is_at_open = False
        self.next_day_btn.setText("▶ 下一天")

        # 重新加载完整数据
        self._load_chart_for_current_symbol()

        # 更新所有持仓的当前价格（收盘价）
        self._update_holding_prices()
        self._refresh_trade_summary()

    def _on_buy_open(self):
        """以开盘价买入当前股票"""
        self._execute_buy(price_field="open", price_label="开盘价")

    def _on_buy_close(self):
        """以收盘价买入当前股票"""
        self._execute_buy(price_field="close", price_label="收盘价")

    def _execute_buy(self, price_field: str, price_label: str):
        """执行买入操作"""
        if not self._current_symbol or self._current_df is None:
            return

        quantity = self.buy_quantity_input.value()
        if quantity <= 0:
            self.statusMessageRequested.emit("请输入有效买入数量", 2000)
            return

        price = float(self._current_df.iloc[-1][price_field])
        buy_amount = price * quantity

        if buy_amount > self._available_capital:
            QtWidgets.QMessageBox.warning(
                self, "资金不足",
                f"买入需要 ¥{buy_amount:,.2f}，可用资金仅 ¥{self._available_capital:,.2f}",
            )
            return

        self._simulator.buy(
            self._current_symbol,
            self._current_stock_name,
            price,
            quantity,
            self._current_sim_date,
        )

        self._available_capital -= buy_amount
        self.available_capital_label.setText(f"¥ {self._available_capital:,.2f}")

        self._refresh_holding_table()
        self._refresh_trade_summary()
        self._redraw_trade_markers()
        self.statusMessageRequested.emit(
            f"{price_label}买入 {self._current_symbol} {quantity}股 @ ¥{price:.2f}",
            3000,
        )

    def _on_sell(self):
        """卖出当前股票"""
        if not self._current_symbol or self._current_df is None:
            return

        # T+1 限制：当天买入的股票不能当天卖出
        today_buy_records = [
            r for r in self._simulator.trade_records
            if r.symbol == self._current_symbol
            and r.action == TradeAction.BUY
            and r.trade_date == self._current_sim_date
        ]
        if today_buy_records:
            QtWidgets.QMessageBox.warning(
                self, "T+1 限制",
                f"{self._current_symbol} 今日有买入，A股 T+1 规则不允许当天卖出",
            )
            return

        quantity = self.sell_quantity_input.value()
        if quantity <= 0:
            self.statusMessageRequested.emit("请输入有效卖出数量", 2000)
            return

        close_price = float(self._current_df.iloc[-1]["close"])

        try:
            self._simulator.sell(
                self._current_symbol,
                self._current_stock_name,
                close_price,
                quantity,
                self._current_sim_date,
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "卖出失败", str(exc))
            return

        sell_amount = close_price * quantity
        self._available_capital += sell_amount
        self.available_capital_label.setText(f"¥ {self._available_capital:,.2f}")

        self._refresh_holding_table()
        self._refresh_trade_summary()
        self._redraw_trade_markers()
        self.statusMessageRequested.emit(
            f"卖出 {self._current_symbol} {quantity}股 @ ¥{close_price:.2f}",
            3000,
        )

    def _on_settle(self):
        """结算所有持仓"""
        if not self._simulator.holdings:
            QtWidgets.QMessageBox.information(self, "提示", "当前无持仓")
            return

        result = self._simulator.settle()

        # 构建结算明细文本
        lines = [
            f"总投入：¥{result.total_cost:,.2f}",
            f"总市值：¥{result.total_value:,.2f}",
            f"总盈亏：¥{result.total_pnl_amount:,.2f}"
            f"（{result.total_pnl_percent:+.2f}%）",
            f"交易笔数：{result.trade_count}",
            "",
            "── 持仓明细 ──",
        ]
        for holding in result.holdings_at_settle:
            lines.append(
                f"  {holding.symbol} {holding.name}  "
                f"{holding.quantity}股  "
                f"成本¥{holding.average_cost:.2f}  "
                f"现价¥{holding.current_price:.2f}  "
                f"盈亏{holding.pnl_percent:+.2f}%"
            )

        reply = QtWidgets.QMessageBox.question(
            self,
            "结算确认",
            "\n".join(lines) + "\n\n确认结算并重置？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )

        if reply == QtWidgets.QMessageBox.Yes:
            self._simulator.reset()
            self._clear_trade_markers()
            self._refresh_holding_table()
            self._refresh_trade_summary()
            self.statusMessageRequested.emit("模拟交易已结算", 3000)

    # ── 模拟交易辅助方法 ─────────────────────────────────────

    def _update_sim_info(self):
        """更新操作面板的日期和价格显示"""
        if self._current_df is not None and not self._current_df.empty:
            last_row = self._current_df.iloc[-1]
            date_val = last_row["date"]
            date_str = (
                date_val.strftime("%Y-%m-%d")
                if hasattr(date_val, "strftime")
                else str(date_val)[:10]
            )
            self.sim_date_label.setText(date_str)
            # 开盘阶段显示开盘价，收盘阶段显示收盘价
            price_field = "open" if self._is_at_open else "close"
            price_label = "开盘" if self._is_at_open else "收盘"
            price = float(last_row[price_field])
            self.sim_price_label.setText(f"¥ {price:.2f}（{price_label}）")
        else:
            self.sim_date_label.setText("--")
            self.sim_price_label.setText("--")

    def _is_today_bought(self, symbol: str) -> bool:
        """判断该股票是否在当天有买入记录（T+1 限制）"""
        return any(
            r.symbol == symbol
            and r.action == TradeAction.BUY
            and r.trade_date == self._current_sim_date
            for r in self._simulator.trade_records
        )

    def _refresh_holding_table(self):
        """刷新持有股票列表"""
        holdings = self._simulator.get_all_holdings()
        self.holding_table.setRowCount(len(holdings))

        for row, holding in enumerate(holdings):
            is_locked = self._is_today_bought(holding.symbol)

            symbol_text = holding.symbol
            if is_locked:
                symbol_text += " 🔒T+1"
            symbol_item = QtWidgets.QTableWidgetItem(symbol_text)
            if is_locked:
                symbol_item.setForeground(QtGui.QColor("#999999"))
            self.holding_table.setItem(row, 0, symbol_item)

            self.holding_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(holding.name)
            )
            self.holding_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(str(holding.quantity))
            )
            self.holding_table.setItem(
                row, 3, QtWidgets.QTableWidgetItem(f"{holding.average_cost:.2f}")
            )
            self.holding_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(f"{holding.current_price:.2f}")
            )

            pnl_item = QtWidgets.QTableWidgetItem(
                f"{holding.pnl_percent:+.2f}%"
            )
            pnl_color = "#FF4444" if holding.pnl_percent >= 0 else "#00CC00"
            pnl_item.setForeground(QtGui.QColor(pnl_color))
            self.holding_table.setItem(row, 5, pnl_item)

        self.holding_table.resizeColumnsToContents()

    def _refresh_trade_summary(self):
        """刷新操作面板的汇总信息"""
        holdings = self._simulator.get_all_holdings()
        total_cost = sum(h.total_cost for h in holdings)
        total_value = sum(h.current_value for h in holdings)
        total_pnl_pct = (
            ((total_value - total_cost) / total_cost * 100)
            if total_cost > 0
            else 0.0
        )

        self.total_cost_label.setText(f"¥ {total_cost:,.2f}")
        self.total_value_label.setText(f"¥ {total_value:,.2f}")

        pnl_text = f"{total_pnl_pct:+.2f}%"
        pnl_color = "#FF4444" if total_pnl_pct >= 0 else "#00CC00"
        self.total_pnl_label.setText(pnl_text)
        self.total_pnl_label.setStyleSheet(
            f"color: {pnl_color}; font-weight: bold;"
        )

    def _update_holding_prices(self):
        """推进到新交易日后，更新所有持仓的当前价格"""
        price_field = "open" if self._is_at_open else "close"
        price_map: dict[str, float] = {}
        for symbol in self._simulator.holdings:
            try:
                df = load_daily_csv(self.stock_daily_data_dir, symbol)
                mask = df["date"] <= self._current_sim_date
                df_up = df[mask]
                if not df_up.empty:
                    price_map[symbol] = float(df_up.iloc[-1][price_field])
            except FileNotFoundError:
                pass

        self._simulator.update_all_prices(price_map)
        self._refresh_holding_table()

    def _redraw_trade_markers(self):
        """重绘当前股票的 B/S 标记"""
        self._clear_trade_markers()

        if self._current_df is None or self._current_df.empty:
            return

        records = [
            r
            for r in self._simulator.trade_records
            if r.symbol == self._current_symbol
        ]

        for record in records:
            trade_date_str = record.trade_date
            date_indices = self._current_df.index[
                self._current_df["date"].apply(
                    lambda d: (
                        d.strftime("%Y-%m-%d")
                        if hasattr(d, "strftime")
                        else str(d)[:10]
                    )
                )
                == trade_date_str
            ]
            if len(date_indices) == 0:
                continue

            x_pos = int(date_indices[0])
            row = self._current_df.iloc[x_pos]

            if record.action == TradeAction.BUY:
                text = "B▲"
                color = "#FF4444"
                y_pos = float(row["low"]) * 0.995
                anchor = (0.5, 0)
            else:
                text = "S▼"
                color = "#00CC00"
                y_pos = float(row["high"]) * 1.005
                anchor = (0.5, 1)

            marker = pg.TextItem(text=text, color=color, anchor=anchor)
            marker.setFont(QtGui.QFont("Arial", 9, QtGui.QFont.Weight.Bold))
            marker.setPos(x_pos, y_pos)
            self.chart.pricePlot.addItem(marker)
            self._trade_marker_items.append(marker)

    def _clear_trade_markers(self):
        """清除所有交易标记"""
        for item in self._trade_marker_items:
            self.chart.pricePlot.removeItem(item)
        self._trade_marker_items.clear()

    def _on_reset(self):
        """重置模拟交易状态，回到刚进入选股结果时的初始状态"""
        if self._simulator.holdings:
            reply = QtWidgets.QMessageBox.question(
                self,
                "确认重置",
                "当前有未结算的模拟交易，重置将清空所有持仓和交易记录。\n确认重置？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        # 重置模拟交易引擎
        self._simulator.reset()
        self._clear_trade_markers()

        # 重置状态字段：日期回到选股目标日期
        self._is_at_open = False
        self._current_sim_date = self._target_date
        self._current_symbol = ""
        self._current_stock_name = ""
        self._current_df = None

        # 恢复初始资金
        self._available_capital = float(self._initial_capital)

        # 重置 UI 控件
        self.next_day_btn.setText("▶ 下一天")
        self.next_day_btn.setEnabled(False)
        self.holding_table.setRowCount(0)
        self.buy_open_btn.setEnabled(False)
        self.buy_close_btn.setEnabled(False)
        self.sell_btn.setEnabled(False)

        # 交易面板切回初始配置态
        self.trade_setup_widget.setVisible(True)
        self.trade_ops_widget.setVisible(False)
        self.initial_capital_input.setValue(self._initial_capital)

        # 重置汇总信息
        self.available_capital_label.setText(f"¥ {self._available_capital:,.2f}")
        self.total_cost_label.setText("¥ 0.00")
        self.total_value_label.setText("¥ 0.00")
        self.total_pnl_label.setText("0.00%")
        self.total_pnl_label.setStyleSheet("")
        self.sim_date_label.setText("--")
        self.sim_price_label.setText("--")

        # 重新选中第一只股票，触发图表加载回到选股日期
        if self.result_table.rowCount() > 0:
            self.result_table.selectRow(0)

        self.statusMessageRequested.emit("已重置到初始状态", 3000)

    def _on_back_to_config(self):
        """返回配置态，有持仓时提示确认"""
        if self._simulator.holdings:
            reply = QtWidgets.QMessageBox.question(
                self,
                "确认返回",
                "当前有未结算的模拟交易，是否放弃？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        # 重置模拟交易状态
        self._simulator.reset()
        self._clear_trade_markers()
        self._is_at_open = False
        self._current_sim_date = ""
        self._current_symbol = ""
        self._current_stock_name = ""
        self._current_df = None
        self.next_day_btn.setText("▶ 下一天")
        self.holding_table.setRowCount(0)

        # 重置交易面板为初始配置态
        self.trade_setup_widget.setVisible(True)
        self.trade_ops_widget.setVisible(False)

        self.page_stack.setCurrentIndex(0)
