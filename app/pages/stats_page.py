"""统计页：API 数据采集 + 持仓分析 + 收益图表。

主类 StatsPage；其余组件已拆到 .stats 子包。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.stats import DataStorage
from app.widgets import StockChartWidget

from .stats import (
    API_DISPLAY_NAMES,
    API_ID_MAP,
    ApiCard,
    CollectWorker,
    OPERATION_MAP,
    OPERATION_SORT_ORDER,
    OperationTag,
    PositionsTable,
    RateDetailDialog,
    SingleStockUpdateWorker,
    StockPreviewDialog,
    _name_initials,
)

logger = logging.getLogger(__name__)


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
        self._raw_positions_data: list[dict] = []
        self._pending_preview: tuple[str, str] | None = None  # (symbol, name) 等待更新完成后展示
        self._setup_ui()
        self._load_positions()

    # ── UI 构建 ───────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet("background: #0f172a;")

        container = QtWidgets.QWidget()
        container.setStyleSheet("background: #0f172a;")
        main_layout = QtWidgets.QVBoxLayout(container)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
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
        top_row_widget = QtWidgets.QWidget()
        top_row_widget.setLayout(top_row)
        top_row_widget.setFixedHeight(46)
        main_layout.addWidget(top_row_widget)
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

        # 搜索框
        self.searchInput = QtWidgets.QLineEdit()
        self.searchInput.setPlaceholderText("🔍 输入股票代码、名称或首字母筛选...")
        self.searchInput.setClearButtonEnabled(True)
        self.searchInput.setFixedHeight(36)
        self.searchInput.setStyleSheet("""
            QLineEdit {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                color: #e2e8f0;
                font-size: 13px;
                padding: 0 12px;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
            }
            QLineEdit::placeholder {
                color: #475569;
            }
        """)
        self.searchInput.textChanged.connect(self._on_search_text_changed)
        main_layout.addWidget(self.searchInput)

        # 持仓表格
        self.positionsTable = PositionsTable()
        self.positionsTable.stockDoubleClicked.connect(self._on_stock_double_clicked)
        self.positionsTable.rateDetailRequested.connect(self._on_rate_detail_requested)
        main_layout.addWidget(self.positionsTable, 1)

        # 空数据提示
        self.emptyLabel = QtWidgets.QLabel("暂无持仓数据，请先执行采集")
        self.emptyLabel.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.emptyLabel.setStyleSheet("color: #475569; font-size: 14px; padding: 48px 20px;")
        main_layout.addWidget(self.emptyLabel)

        page_layout = QtWidgets.QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(container)

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
        self._raw_positions_data = raw_positions

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
        """按股票代码+操作类型去重，统计持有人数"""
        group_map: dict[str, dict] = {}
        for item in data:
            code = str(item.get("code", ""))
            op = str(item.get("op", "0"))
            group_key = f"{code}_{op}"
            if group_key in group_map:
                group_map[group_key]["_count"] += 1
            else:
                group_map[group_key] = {**item, "_count": 1}
        return list(group_map.values())

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
    def _on_search_text_changed(self, text: str):
        self.positionsTable.filter_by_text(text)

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

    # ── 收益详情弹窗 ─────────────────────────────────────────────────────

    @QtCore.Slot(str, str, str)
    def _on_rate_detail_requested(self, code: str, op: str, name: str):
        """点击收益详情按钮：传入该股票的所有原始记录，弹窗内支持操作类型切换"""
        all_stock_records = [
            item for item in self._raw_positions_data
            if str(item.get("code", "")) == code
        ]
        dialog = RateDetailDialog(code, name, op, all_stock_records, parent=self)
        dialog.exec()

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