from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import get_last_trade_date, load_daily_csv
from app.history_updater import HistoryUpdater
from app.stats import (
    ApiRequester,
    ApiResponse,
    ConfigLoader,
    DataAnalyzer,
    DataStorage,
)
from app.widgets import StockChartWidget

logger = logging.getLogger(__name__)

# ── 接口名称到内部 ID 的映射 ──────────────────────────────────────────────
API_ID_MAP = {
    "比赛排名": "api1",
    "每日持仓": "api2",
}

API_DISPLAY_NAMES = {
    "api1": "📋 比赛排名",
    "api2": "📈 每日持仓",
}

# ── 持仓操作映射 ──────────────────────────────────────────────────────────
OPERATION_MAP = {
    "0": {"label": "不变", "color": "#94a3b8", "background": "#334155"},
    "1": {"label": "加仓", "color": "#6ee7b7", "background": "#1a332e"},
    "2": {"label": "减仓", "color": "#fbbf24", "background": "#3b2c1c"},
    "3": {"label": "建仓", "color": "#4ade80", "background": "#1a3329"},
    "4": {"label": "清仓", "color": "#f87171", "background": "#3b1c1c"},
    "7": {"label": "大幅加仓", "color": "#22c55e", "background": "#14532d"},
    "8": {"label": "大幅减仓", "color": "#f97316", "background": "#451a03"},
    "9": {"label": "T操作", "color": "#60a5fa", "background": "#1e3a5f"},
}

OPERATION_SORT_ORDER = {"3": 1, "7": 2, "1": 3, "9": 4, "0": 5, "2": 6, "8": 7, "4": 8}


# ═══════════════════════════════════════════════════════════════════════════
#  采集工作线程
# ═══════════════════════════════════════════════════════════════════════════

class CollectWorker(QtCore.QObject):
    """在后台线程中执行 API 采集任务"""

    logMessage = QtCore.Signal(str, str)          # (message, level)
    apiStart = QtCore.Signal(str, int)             # (api_id, total)
    apiProgress = QtCore.Signal(str, int, int, float)  # (api_id, current, total, elapsed)
    apiDone = QtCore.Signal(str, int, float)       # (api_id, count, elapsed)
    apiCached = QtCore.Signal(str)                 # (api_id,)
    apiError = QtCore.Signal(str)                  # (api_id,)
    allDone = QtCore.Signal(str)                   # (report_text,)

    def __init__(self):
        super().__init__()

    def _progress_callback(self, api_name: str, event_type: str, **kwargs):
        """requester 的进度回调，转发为 Qt 信号"""
        api_id = API_ID_MAP.get(api_name, api_name)

        if event_type == "log":
            self.logMessage.emit(kwargs.get("message", ""), kwargs.get("level", "info"))
        elif event_type == "api_start":
            self.apiStart.emit(api_id, kwargs.get("total", 0))
        elif event_type == "progress":
            self.apiProgress.emit(
                api_id,
                kwargs.get("current", 0),
                kwargs.get("total", 0),
                kwargs.get("elapsed", 0.0),
            )
        elif event_type == "api_done":
            self.apiDone.emit(api_id, kwargs.get("count", 0), kwargs.get("elapsed", 0.0))
        elif event_type == "api_error":
            self.apiError.emit(api_id)

    @QtCore.Slot()
    def run(self):
        try:
            config_loader = ConfigLoader()
            api_configs, settings = config_loader.load()
            storage = DataStorage()

            self.logMessage.emit(f"加载了 {len(api_configs)} 个接口配置", "info")

            apis_to_request = []
            for api_config in api_configs:
                api_id = API_ID_MAP.get(api_config.name, api_config.name)
                if storage.is_cache_valid(api_config.output_file):
                    self.apiCached.emit(api_id)
                    self.logMessage.emit(f"[{api_config.name}] 当天缓存有效，跳过", "success")
                else:
                    apis_to_request.append(api_config)

            if not apis_to_request:
                self.logMessage.emit("所有接口数据均为当天缓存，无需重新请求", "success")
                self.allDone.emit("")
                return

            self.logMessage.emit(f"开始请求 {len(apis_to_request)} 个接口...", "info")

            requester = ApiRequester(settings, progress_callback=self._progress_callback)
            responses: list = []
            try:
                for api_config in apis_to_request:
                    if api_config.pagination.enabled:
                        response = requester._request_paginated(api_config)
                    elif api_config.batch.enabled:
                        response = requester._request_batched(api_config)
                    else:
                        response = requester._request_single(api_config)
                    response.output_file = api_config.output_file
                    responses.append(response)

                    # 逐个保存，确保后续接口能读到前置接口的输出
                    if response.success:
                        saved_paths = storage.save_responses([response])
                        for path in saved_paths:
                            self.logMessage.emit(f"已保存: {path}", "success")
                    else:
                        self.logMessage.emit(
                            f"跳过失败接口: {api_config.name}", "warning",
                        )
            finally:
                requester.close()

            analyzer = DataAnalyzer()
            report = analyzer.analyze(responses)
            report_text = analyzer.format_report(report)

            self.logMessage.emit(
                f"采集完成 | 成功: {report.success_count}/{report.total_apis} | 平均耗时: {report.average_elapsed_seconds}s",
                "success",
            )
            self.allDone.emit(report_text)

        except Exception as error:
            self.logMessage.emit(f"任务异常: {error}", "error")
            self.allDone.emit("")


# ═══════════════════════════════════════════════════════════════════════════
#  进度卡片 Widget
# ═══════════════════════════════════════════════════════════════════════════

class ApiCard(QtWidgets.QFrame):
    """单个接口的进度卡片"""

    def __init__(self, api_id: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.api_id = api_id
        self.setObjectName(f"apiCard_{api_id}")
        self.setStyleSheet("""
            ApiCard {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 16px;
            }
        """)
        self._setup_ui()
        self.reset()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.titleLabel = QtWidgets.QLabel(API_DISPLAY_NAMES.get(self.api_id, self.api_id))
        self.titleLabel.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0;")
        layout.addWidget(self.titleLabel)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFixedHeight(18)
        self.progressBar.setMinimumWidth(120)
        self.progressBar.setStyleSheet("""
            QProgressBar {
                background: #0f172a;
                border: none;
                border-radius: 6px;
                text-align: center;
                font-size: 11px;
                font-weight: 600;
                color: #e2e8f0;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3b82f6, stop:1 #8b5cf6);
                border-radius: 6px;
            }
        """)
        layout.addWidget(self.progressBar, 1)

        self.infoLabel = QtWidgets.QLabel("")
        self.infoLabel.setStyleSheet("font-size: 11px; color: #94a3b8;")
        layout.addWidget(self.infoLabel)

        self.statusLabel = QtWidgets.QLabel("等待中")
        self.statusLabel.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.statusLabel.setFixedWidth(64)
        self._set_status_style("waiting")
        layout.addWidget(self.statusLabel)

    def _set_status_style(self, status: str):
        styles = {
            "waiting": "background: #334155; color: #94a3b8;",
            "running": "background: #1e3a5f; color: #60a5fa;",
            "cached": "background: #1a3329; color: #4ade80;",
            "done": "background: #1a3329; color: #4ade80;",
            "error": "background: #3b1c1c; color: #f87171;",
        }
        base_style = "padding: 4px 12px; border-radius: 10px; font-size: 12px; font-weight: 600;"
        self.statusLabel.setStyleSheet(base_style + styles.get(status, styles["waiting"]))

    def reset(self):
        self.statusLabel.setText("等待中")
        self._set_status_style("waiting")
        self.progressBar.setValue(0)
        self.infoLabel.setText("")

    def set_status(self, status: str, text: str):
        self.statusLabel.setText(text)
        self._set_status_style(status)

    def set_progress(self, percent: int):
        self.progressBar.setValue(min(percent, 100))

    def set_info(self, text: str):
        self.infoLabel.setText(text)


# ═══════════════════════════════════════════════════════════════════════════
#  操作标签 Widget
# ═══════════════════════════════════════════════════════════════════════════

class OperationTag(QtWidgets.QPushButton):
    """可点击的操作筛选标签"""

    filterClicked = QtCore.Signal(str)  # op_code or "all"

    def __init__(self, op_code: str, label: str, count: int,
                 color: str, background: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.op_code = op_code
        self.is_active = False
        self.base_color = color
        self.base_background = background
        self.setText(f"{label} {count}" if count >= 0 else label)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self._update_style()
        self.clicked.connect(lambda: self.filterClicked.emit(self.op_code))

    def set_active(self, active: bool):
        self.is_active = active
        self._update_style()

    def _update_style(self):
        border = f"border: 2px solid #e2e8f0;" if self.is_active else "border: 2px solid transparent;"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {self.base_background};
                color: {self.base_color};
                padding: 3px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 600;
                {border}
            }}
            QPushButton:hover {{ opacity: 0.85; }}
        """)


# ═══════════════════════════════════════════════════════════════════════════
#  持仓表格 Widget
# ═══════════════════════════════════════════════════════════════════════════

class PositionsTable(QtWidgets.QTableWidget):
    """持仓操作数据表格，支持排序"""

    stockDoubleClicked = QtCore.Signal(str, str)  # (code, name)

    COLUMNS = [
        ("code", "股票代码", "string"),
        ("name", "股票名称", "string"),
        ("op", "操作", "op"),
        ("_count", "持有人数", "number"),
    ]

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._raw_data: list[dict] = []
        self._display_data: list[dict] = []
        self._sort_column: int = -1
        self._sort_ascending: bool = True
        self._setup_ui()

    def _setup_ui(self):
        self.setColumnCount(len(self.COLUMNS))
        headers = [col[1] for col in self.COLUMNS]
        self.setHorizontalHeaderLabels(headers)

        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)

        self.setStyleSheet("""
            QTableWidget {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 12px;
                color: #e2e8f0;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 12px 16px;
                border-bottom: 1px solid #1a2536;
            }
            QTableWidget::item:selected {
                background: #263348;
                color: #e2e8f0;
            }
            QHeaderView::section {
                background: #0f172a;
                color: #94a3b8;
                padding: 14px 16px;
                font-size: 13px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #334155;
            }
            QHeaderView::section:hover {
                color: #e2e8f0;
            }
        """)

    def set_data(self, data: list[dict]):
        self._raw_data = data
        self._display_data = list(data)
        self._sort_column = 3  # _count 列
        self._sort_ascending = False  # 降序
        self._sort_and_refresh()

    def _on_header_clicked(self, logical_index: int):
        if self._sort_column == logical_index:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = logical_index
            self._sort_ascending = True
        self._sort_and_refresh()

    def _sort_and_refresh(self):
        if self._sort_column < 0 or self._sort_column >= len(self.COLUMNS):
            return

        key, _, col_type = self.COLUMNS[self._sort_column]

        def sort_key(item):
            if col_type == "number":
                return float(item.get(key, 0) or 0)
            elif col_type == "op":
                return OPERATION_SORT_ORDER.get(str(item.get(key, "")), 99)
            else:
                return str(item.get(key, ""))

        self._display_data.sort(key=sort_key, reverse=not self._sort_ascending)
        self._refresh_table()

    def _refresh_table(self):
        self.setRowCount(len(self._display_data))
        default_color = QtGui.QColor("#e2e8f0")

        for row, item in enumerate(self._display_data):
            code_item = QtWidgets.QTableWidgetItem(str(item.get("code", "")))
            code_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            code_item.setFont(QtGui.QFont("Courier", 13))
            code_item.setForeground(default_color)
            self.setItem(row, 0, code_item)

            name_item = QtWidgets.QTableWidgetItem(str(item.get("name", "")))
            name_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            name_item.setForeground(default_color)
            self.setItem(row, 1, name_item)

            op_code = str(item.get("op", "0"))
            op_info = OPERATION_MAP.get(op_code, OPERATION_MAP["0"])
            op_item = QtWidgets.QTableWidgetItem(op_info["label"])
            op_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            op_item.setForeground(QtGui.QColor(op_info["color"]))
            self.setItem(row, 2, op_item)

            count_item = QtWidgets.QTableWidgetItem(str(item.get("_count", 1)))
            count_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            count_item.setForeground(default_color)
            self.setItem(row, 3, count_item)

    def filter_by_ops(self, active_ops: set[str]):
        if not active_ops:
            self._display_data = list(self._raw_data)
        else:
            self._display_data = [
                item for item in self._raw_data
                if str(item.get("op", "")) in active_ops
            ]
        if self._sort_column >= 0:
            self._sort_and_refresh()
        else:
            self._refresh_table()

    def _on_cell_double_clicked(self, row: int, _column: int):
        code_item = self.item(row, 0)
        name_item = self.item(row, 1)
        if code_item:
            code = code_item.text()
            name = name_item.text() if name_item else ""
            self.stockDoubleClicked.emit(code, name)


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

class SingleStockUpdateWorker(QtCore.QObject):
    """在后台线程中更新单只股票的日线数据"""

    finished = QtCore.Signal(bool, str)  # (success, message)

    def __init__(self, symbol: str, stocklist_csv: Path, stock_daily_data_dir: Path):
        super().__init__()
        self._symbol = symbol
        self._stocklist_csv = stocklist_csv
        self._stock_daily_data_dir = stock_daily_data_dir

    @QtCore.Slot()
    def run(self):
        try:
            updater = HistoryUpdater(self._stocklist_csv, self._stock_daily_data_dir)
            result = updater.update_symbol(self._symbol)
            if result.status == "failed":
                self.finished.emit(False, result.message)
            else:
                self.finished.emit(True, result.message)
        except Exception as error:
            self.finished.emit(False, str(error))


# ═══════════════════════════════════════════════════════════════════════════
#  统计页面主 Widget
# ═══════════════════════════════════════════════════════════════════════════

class StatsPage(QtWidgets.QWidget):
    """统计页面 — 复刻 test 项目的 API 采集与持仓数据可视化功能"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.stocklist_csv = self.root / "stocklist.csv"
        self.stock_daily_data_dir = self.root / "stock_daily_data"
        self._worker_thread: QtCore.QThread | None = None
        self._update_thread: QtCore.QThread | None = None
        self._active_filter_ops: set[str] = set()
        self._positions_data: list[dict] = []
        self._pending_preview: tuple[str, str] | None = None  # (symbol, name) 等待更新完成后展示
        self._setup_ui()
        self._load_positions()

    # ── UI 构建 ───────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet("background: #0f172a;")

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0f172a; }")

        container = QtWidgets.QWidget()
        container.setStyleSheet("background: #0f172a;")
        main_layout = QtWidgets.QVBoxLayout(container)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        # 时钟
        self.timeLabel = QtWidgets.QLabel("等待操作...")
        self.timeLabel.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.timeLabel.setStyleSheet("color: #94a3b8; font-size: 13px;")
        main_layout.addWidget(self.timeLabel)

        self._clock_timer = QtCore.QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        # 开始按钮 + 进度卡片（同一行）
        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(12)

        self.startButton = QtWidgets.QPushButton("🚀 开始采集")
        self.startButton.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.startButton.setFixedSize(140, 38)
        self.startButton.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #3b82f6, stop:1 #8b5cf6);
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2563eb, stop:1 #7c3aed);
            }
            QPushButton:disabled {
                opacity: 0.5;
                background: #475569;
            }
        """)
        self.startButton.clicked.connect(self._start_collect)
        top_row.addWidget(self.startButton)

        self.api_cards: dict[str, ApiCard] = {}
        for api_id in ["api1", "api2"]:
            card = ApiCard(api_id)
            self.api_cards[api_id] = card
            top_row.addWidget(card, 1)
        main_layout.addLayout(top_row)
        # 持仓操作区域
        positions_header = QtWidgets.QHBoxLayout()
        positions_title = QtWidgets.QLabel("📈 每日持仓操作一览")
        positions_title.setStyleSheet("font-size: 16px; font-weight: 600; color: #94a3b8;")
        positions_header.addWidget(positions_title)
        positions_header.addStretch()

        self.filterContainer = QtWidgets.QHBoxLayout()
        self.filterContainer.setSpacing(8)
        positions_header.addLayout(self.filterContainer)
        main_layout.addLayout(positions_header)

        # 持仓表格
        self.positionsTable = PositionsTable()
        self.positionsTable.setMinimumHeight(400)
        self.positionsTable.stockDoubleClicked.connect(self._on_stock_double_clicked)
        main_layout.addWidget(self.positionsTable)

        # 空数据提示
        self.emptyLabel = QtWidgets.QLabel("暂无持仓数据，请先执行采集")
        self.emptyLabel.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.emptyLabel.setStyleSheet("color: #475569; font-size: 14px; padding: 48px 20px;")
        main_layout.addWidget(self.emptyLabel)

        main_layout.addStretch()

        scroll.setWidget(container)

        page_layout = QtWidgets.QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

    # ── 时钟 ──────────────────────────────────────────────────────────────

    def _update_clock(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.timeLabel.setText(now)

    # ── 采集控制 ──────────────────────────────────────────────────────────

    def _start_collect(self):
        self.startButton.setEnabled(False)
        self.startButton.setText("⏳ 采集中...")

        for card in self.api_cards.values():
            card.reset()

        self._worker_thread = QtCore.QThread()
        self._worker = CollectWorker()
        self._worker.moveToThread(self._worker_thread)

        self._worker.logMessage.connect(self._on_log_message)
        self._worker.apiStart.connect(self._on_api_start)
        self._worker.apiProgress.connect(self._on_api_progress)
        self._worker.apiDone.connect(self._on_api_done)
        self._worker.apiCached.connect(self._on_api_cached)
        self._worker.apiError.connect(self._on_api_error)
        self._worker.allDone.connect(self._on_all_done)

        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.start()

    # ── 信号处理 ──────────────────────────────────────────────────────────

    @QtCore.Slot(str, str)
    def _on_log_message(self, message: str, level: str):
        logger.debug("[%s] %s", level, message)

    @QtCore.Slot(str, int)
    def _on_api_start(self, api_id: str, total: int):
        card = self.api_cards.get(api_id)
        if card:
            card.set_status("running", "请求中")
            if total > 0:
                card.set_info(f"共 {total}")

    @QtCore.Slot(str, int, int, float)
    def _on_api_progress(self, api_id: str, current: int, total: int, elapsed: float):
        card = self.api_cards.get(api_id)
        if card:
            percent = round((current / total) * 100) if total > 0 else 0
            card.set_progress(percent)
            card.set_info(f"{current}/{total}  {elapsed:.1f}s")

    @QtCore.Slot(str, int, float)
    def _on_api_done(self, api_id: str, count: int, elapsed: float):
        card = self.api_cards.get(api_id)
        if card:
            card.set_status("done", "✅ 完成")
            card.set_progress(100)
            card.set_info(f"{count} 条  {elapsed:.1f}s")

    @QtCore.Slot(str)
    def _on_api_cached(self, api_id: str):
        card = self.api_cards.get(api_id)
        if card:
            card.set_status("cached", "已缓存")
            card.set_progress(100)

    @QtCore.Slot(str)
    def _on_api_error(self, api_id: str):
        card = self.api_cards.get(api_id)
        if card:
            card.set_status("error", "❌ 失败")

    @QtCore.Slot(str)
    def _on_all_done(self, report_text: str):
        self.startButton.setEnabled(True)
        self.startButton.setText("🚀 重新采集")

        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None

        self._load_positions()
        self.statusMessageRequested.emit("采集完成", 3000)

    # ── 持仓数据 ──────────────────────────────────────────────────────────

    def _load_positions(self):
        storage = DataStorage()
        raw_positions = storage.load_positions()

        deduplicated = self._deduplicate_by_code(raw_positions)
        self._positions_data = deduplicated

        if deduplicated:
            self.positionsTable.setVisible(True)
            self.emptyLabel.setVisible(False)
            self.positionsTable.set_data(deduplicated)
        else:
            self.positionsTable.setVisible(False)
            self.emptyLabel.setVisible(True)

        self._rebuild_filter_tags(deduplicated)

    @staticmethod
    def _deduplicate_by_code(data: list[dict]) -> list[dict]:
        """按股票代码去重，统计持有人数"""
        code_map: dict[str, dict] = {}
        for item in data:
            code = str(item.get("code", ""))
            if code in code_map:
                code_map[code]["_count"] += 1
            else:
                code_map[code] = {**item, "_count": 1}
        return list(code_map.values())

    def _rebuild_filter_tags(self, data: list[dict]):
        """重建操作筛选标签"""
        # 清除旧标签
        while self.filterContainer.count():
            child = self.filterContainer.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 统计各操作数量
        op_counts: dict[str, int] = {}
        for item in data:
            op = str(item.get("op", "0"))
            op_counts[op] = op_counts.get(op, 0) + 1

        # "全部" 标签
        all_tag = OperationTag("all", f"共 {len(data)} 只", -1, "#e2e8f0", "#1e293b")
        all_tag.set_active(len(self._active_filter_ops) == 0)
        all_tag.filterClicked.connect(self._on_filter_tag_clicked)
        self.filterContainer.addWidget(all_tag)

        # 各操作标签（按排序顺序）
        sorted_ops = sorted(op_counts.keys(), key=lambda op: OPERATION_SORT_ORDER.get(op, 99))
        for op_code in sorted_ops:
            count = op_counts[op_code]
            op_info = OPERATION_MAP.get(op_code, OPERATION_MAP["0"])
            tag = OperationTag(
                op_code, op_info["label"], count,
                op_info["color"], op_info["background"],
            )
            tag.set_active(op_code in self._active_filter_ops)
            tag.filterClicked.connect(self._on_filter_tag_clicked)
            self.filterContainer.addWidget(tag)

    @QtCore.Slot(str)
    def _on_filter_tag_clicked(self, op_code: str):
        if op_code == "all":
            self._active_filter_ops.clear()
        elif op_code in self._active_filter_ops:
            self._active_filter_ops.discard(op_code)
        else:
            self._active_filter_ops.add(op_code)

        self.positionsTable.filter_by_ops(self._active_filter_ops)
        self._rebuild_filter_tags(self._positions_data)

    # ── 双击股票预览 ─────────────────────────────────────────────────────

    def _is_data_fresh(self, symbol: str) -> bool:
        """检查股票本地数据是否包含最近一个交易日"""
        last_date = get_last_trade_date(self.stock_daily_data_dir, symbol)
        if last_date is None:
            return False
        today = pd.Timestamp.today().normalize()
        # 如果今天是周末，最近交易日是周五
        weekday = today.weekday()
        if weekday == 5:  # 周六
            latest_expected = today - pd.Timedelta(days=1)
        elif weekday == 6:  # 周日
            latest_expected = today - pd.Timedelta(days=2)
        else:
            # 工作日：15:00 之前认为昨天是最新的，之后认为今天是最新的
            now = datetime.now()
            if now.hour < 15:
                latest_expected = today - pd.Timedelta(days=1)
                # 如果昨天是周末，回退到周五
                if latest_expected.weekday() == 6:
                    latest_expected -= pd.Timedelta(days=2)
                elif latest_expected.weekday() == 5:
                    latest_expected -= pd.Timedelta(days=1)
            else:
                latest_expected = today
        return last_date >= latest_expected

    @QtCore.Slot(str, str)
    def _on_stock_double_clicked(self, code: str, name: str):
        """双击股票行：检查数据新鲜度 → 需要时更新 → 弹出图表"""
        symbol = str(code).zfill(6)

        if self._is_data_fresh(symbol):
            self._show_preview_dialog(symbol, name)
        else:
            self._pending_preview = (symbol, name)
            self.statusMessageRequested.emit(f"正在更新 {symbol} {name} 的数据...", 5000)
            self._start_single_update(symbol)

    def _start_single_update(self, symbol: str):
        if self._update_thread is not None:
            self.statusMessageRequested.emit("已有更新任务进行中，请稍候", 3000)
            return

        self._update_thread = QtCore.QThread()
        worker = SingleStockUpdateWorker(symbol, self.stocklist_csv, self.stock_daily_data_dir)
        worker.moveToThread(self._update_thread)
        worker.finished.connect(self._on_single_update_finished)
        self._update_thread.started.connect(worker.run)
        # 防止 worker 被回收
        self._update_worker = worker
        self._update_thread.start()

    @QtCore.Slot(bool, str)
    def _on_single_update_finished(self, success: bool, message: str):
        if self._update_thread:
            self._update_thread.quit()
            self._update_thread.wait()
            self._update_thread = None
            self._update_worker = None

        if self._pending_preview:
            symbol, name = self._pending_preview
            self._pending_preview = None
            if success:
                # 清除缓存以加载最新数据
                from app.data_loader import _daily_data_cache
                cache_key = f"{self.stock_daily_data_dir}:{symbol}"
                _daily_data_cache.pop(cache_key, None)
                self._show_preview_dialog(symbol, name)
            else:
                self.statusMessageRequested.emit(f"更新失败: {message}，尝试使用本地数据", 3000)
                self._show_preview_dialog(symbol, name)

    def _show_preview_dialog(self, symbol: str, name: str):
        dialog = StockPreviewDialog(symbol, name, self.stock_daily_data_dir, parent=self)
        dialog.exec()
