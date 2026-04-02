from __future__ import annotations

import random
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from core.screening.service import ScreeningService
from core.templates import TemplateService

from ..data_loader import load_daily_csv, load_stock_list
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
            payload = self.screening_service.screen_with_summary(
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

    CHART_FIXED_DAYS = 60

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

        # 左侧：股票列表 + 返回按钮
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)

        self.result_table = QtWidgets.QTableWidget()
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["代码", "名称"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        left_layout.addWidget(self.result_table, 1)

        self.back_btn = QtWidgets.QPushButton("返回配置")
        left_layout.addWidget(self.back_btn)

        # 右侧：图表
        self.chart = StockChartWidget()
        self._disable_chart_interaction()

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.chart)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 880])

        layout.addWidget(splitter)
        return panel

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
        self.back_btn.clicked.connect(self._on_back_to_config)

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
        """从已有交易日数据中随机选取一个日期（2020-01-01之后，且排除最近60个交易日）"""
        try:
            df_list = load_stock_list(self.stocklist_csv)
            if df_list.empty:
                return

            sample_symbol = str(df_list["symbol"].iloc[0]).zfill(6)
            df_daily = load_daily_csv(self.stock_daily_data_dir, sample_symbol)
            if df_daily.empty or "date" not in df_daily.columns:
                return

            import pandas as pd
            earliest_allowed = pd.Timestamp("2020-01-01")

            dates = df_daily["date"].tolist()
            # 排除最近 60 个交易日
            if len(dates) > self.CHART_FIXED_DAYS:
                dates = dates[:-self.CHART_FIXED_DAYS]
            else:
                return

            # 筛选 >= 2020-01-01 的日期
            candidates = [d for d in dates if d >= earliest_allowed]
            if not candidates:
                return

            random_date = random.choice(candidates)
            date_str = random_date.strftime("%Y-%m-%d") if hasattr(random_date, "strftime") else str(random_date)[:10]

            qdate = QtCore.QDate.fromString(date_str, "yyyy-MM-dd")
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
        self._screening_thread = QtCore.QThread(self)
        self._screening_worker = ScreeningWorker(self.screening_service, request)
        self._screening_worker.moveToThread(self._screening_thread)
        self._screening_thread.started.connect(self._screening_worker.run)
        self._screening_worker.progressChanged.connect(self._on_screening_progress)
        self._screening_worker.finished.connect(self._on_screening_finished)
        self._screening_worker.errorOccurred.connect(self._on_screening_error)
        self._screening_worker.finished.connect(self._screening_thread.quit)
        self._screening_worker.finished.connect(self._screening_worker.deleteLater)
        self._screening_thread.finished.connect(self._screening_thread.deleteLater)
        self._screening_thread.finished.connect(self._cleanup_screening)
        self._screening_thread.start()

    def _on_screening_stop(self):
        if self._screening_worker is not None:
            self._screening_worker.cancel()

    def _on_screening_progress(self, payload: dict):
        if self._screening_progress_dialog is not None:
            self._screening_progress_dialog.update_progress(payload)

    def _on_screening_finished(self, payload: dict):
        result = payload["result"]
        was_cancelled = (
            self._screening_worker is not None and self._screening_worker._cancelled
        )

        # 收集命中的股票
        self._screening_matches = [
            match for match in result.matches if match.matched
        ]

        if was_cancelled:
            summary = f"选股已停止：已处理部分中命中 {result.matched_count} 只"
        else:
            summary = payload["summary"]

        self.statusMessageRequested.emit(summary, 5000)

        # 关闭进度弹窗
        if self._screening_progress_dialog is not None:
            if was_cancelled:
                self._screening_progress_dialog.accept()
            else:
                self._screening_progress_dialog.mark_finished(summary)

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

        # 切换到结果态
        self.page_stack.setCurrentIndex(1)

        # 默认选中第一只股票
        if self.result_table.rowCount() > 0:
            self.result_table.selectRow(0)

    def _on_stock_selected(self):
        """点击股票列表项，加载该股票数据并刷新图表"""
        row = self.result_table.currentRow()
        if row < 0 or row >= len(self._screening_matches):
            return

        symbol = self._screening_matches[row].symbol
        self._load_chart_for_symbol(symbol)

    def _load_chart_for_symbol(self, symbol: str):
        """加载股票日线数据，传入全部历史保证指标准确，视觉上只展示最后60个交易日"""
        try:
            df_daily = load_daily_csv(self.stock_daily_data_dir, symbol)
            if df_daily.empty:
                self.statusMessageRequested.emit(
                    f"{symbol} 暂无本地日线数据", 3000
                )
                return

            # 筛选 <= target_date 的全部数据（保证指标计算有足够历史预热）
            df_up_to_date = df_daily[df_daily["date"] <= self._target_date].copy()
            df_up_to_date = df_up_to_date.reset_index(drop=True)

            if df_up_to_date.empty:
                self.statusMessageRequested.emit(
                    f"{symbol} 在 {self._target_date} 之前无数据", 3000
                )
                return

            # 传入全部数据，让指标计算准确
            self.chart.set_daily(df_up_to_date)

            # 手动设置 X 轴可见范围为最后 CHART_FIXED_DAYS 个交易日
            total_bars = len(df_up_to_date)
            visible_start = max(0, total_bars - self.CHART_FIXED_DAYS)
            half_width = self.chart._item_half_width
            right_padding = self.chart._right_view_padding
            x_left = visible_start - half_width
            x_right = (total_bars - 1) + half_width + right_padding
            self.chart.pricePlot.setXRange(x_left, x_right, padding=0)

            # 同步限制 X 轴范围，禁止拖动超出可见区域
            for plot in (self.chart.pricePlot, self.chart.volPlot,
                         self.chart.brickPlot, self.chart.kdjPlot):
                plot.getViewBox().setLimits(
                    xMin=x_left,
                    xMax=x_right,
                    minXRange=x_right - x_left,
                    maxXRange=x_right - x_left,
                )

            name = self._screening_matches[self.result_table.currentRow()].name or ""
            display_days = min(total_bars, self.CHART_FIXED_DAYS)
            self.statusMessageRequested.emit(
                f"{symbol} {name}  展示最近 {display_days} 个交易日", 2000
            )
        except Exception:
            self.statusMessageRequested.emit(
                f"{symbol} 数据加载失败", 3000
            )

    def _on_back_to_config(self):
        """返回配置态"""
        self.page_stack.setCurrentIndex(0)
