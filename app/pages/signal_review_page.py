from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.widgets import StockChartWidget

logger = logging.getLogger(__name__)

SIGNAL_DATE_MARGIN_BEFORE = 30
SIGNAL_DATE_MARGIN_AFTER = 15

REVIEW_CSV_GLOB = "brick_scoring_backtest_result*.csv"
REVIEW_DIR_NAME = "111"

# CSV列名常量
CSV_COL_CODE = "股票代码"
CSV_COL_NAME = "股票名称"
CSV_COL_DATE = "信号日期"
CSV_COL_MODE = "命中模式"
CSV_COL_GRADE = "信号等级"
CSV_COL_PATTERN_SCORE = "模式得分"
CSV_COL_SCORE = "最终得分"
CSV_COL_RATING = "评价"
CSV_COL_T1_RETURN = "T+1涨幅%"
CSV_COL_T3_RETURN = "T+3涨幅%"
CSV_COL_T5_RETURN = "T+5涨幅%"

TABLE_COLUMNS = ["股票名称", "股票代码", "信号日期", "命中模式", "信号等级", "最终得分", "评价", "T+1涨幅%", "T+3涨幅%", "T+5涨幅%"]

MODE_OPTIONS = ["横盘起跳", "N型起跳", "上升波段延续"]
RATING_OPTIONS = ["", "完全准确", "基本准确", "不准确", "完全不准确"]

MODE_COLUMN_INDEX = TABLE_COLUMNS.index("命中模式")
RATING_COLUMN_INDEX = TABLE_COLUMNS.index("评价")

EDITABLE_COLUMN_INDICES = {MODE_COLUMN_INDEX, RATING_COLUMN_INDEX}


class ComboDelegate(QtWidgets.QStyledItemDelegate):
    """通用下拉框编辑委托。"""

    valueChanged = QtCore.Signal(int, str)  # (visual_row, new_value)

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self._options = options

    def createEditor(self, parent, option, index):
        combo = QtWidgets.QComboBox(parent)
        combo.addItems(self._options)
        return combo

    def setEditorData(self, editor: QtWidgets.QComboBox, index):
        current_text = index.data(QtCore.Qt.DisplayRole) or ""
        idx = editor.findText(str(current_text))
        editor.setCurrentIndex(max(idx, 0))

    def setModelData(self, editor: QtWidgets.QComboBox, model, index):
        new_value = editor.currentText()
        model.setData(index, new_value, QtCore.Qt.DisplayRole)
        self.valueChanged.emit(index.row(), new_value)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

class SignalReviewPage(QtWidgets.QWidget):
    """信号回顾页面：左侧表格 + 右侧四联图，点击表格行后展示对应股票的信号日前后行情。"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.stock_daily_data_dir = self.root / "stock_daily_data"
        self.review_dir = self.root / REVIEW_DIR_NAME

        self._signal_df: pd.DataFrame | None = None
        self._signal_vline: pg.InfiniteLine | None = None
        self._current_chart_df: pd.DataFrame | None = None  # 当前加载的日线数据
        self._current_symbol: str = ""
        self._current_name: str = ""
        self._current_view_right_idx: int = 0  # 当前视图右边界对应的数据索引

        self._setup_ui()
        self._connect_signals()
        self._load_csv_list()

    # ── UI 构建 ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        self.chart = StockChartWidget()
        splitter.addWidget(self.chart)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 840])

        main_layout.addWidget(splitter)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        heading = QtWidgets.QLabel("信号回顾")
        font = QtGui.QFont()
        font.setPointSize(14)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(6)
        file_label = QtWidgets.QLabel("数据文件:")
        self.fileCombo = QtWidgets.QComboBox()
        self.fileCombo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        file_row.addWidget(file_label)
        file_row.addWidget(self.fileCombo, 1)
        layout.addLayout(file_row)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setSpacing(6)
        self.modeFilter = QtWidgets.QComboBox()
        self.modeFilter.addItem("全部模式")
        self.gradeFilter = QtWidgets.QComboBox()
        self.gradeFilter.addItem("全部等级")
        filter_row.addWidget(self.modeFilter, 1)
        filter_row.addWidget(self.gradeFilter, 1)
        layout.addLayout(filter_row)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._modeDelegate = ComboDelegate(MODE_OPTIONS, self.table)
        self.table.setItemDelegateForColumn(MODE_COLUMN_INDEX, self._modeDelegate)
        self._modeDelegate.valueChanged.connect(self._on_mode_changed)

        self._ratingDelegate = ComboDelegate(RATING_OPTIONS, self.table)
        self.table.setItemDelegateForColumn(RATING_COLUMN_INDEX, self._ratingDelegate)
        self._ratingDelegate.valueChanged.connect(self._on_rating_changed)

        layout.addWidget(self.table, 1)

        self.countLabel = QtWidgets.QLabel("共 0 条信号")
        self.countLabel.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.countLabel)

        return panel

    # ── 信号连接 ─────────────────────────────────────────────────────────

    def _connect_signals(self):
        self.fileCombo.currentIndexChanged.connect(self._on_file_changed)
        self.modeFilter.currentTextChanged.connect(self._apply_filter)
        self.gradeFilter.currentTextChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)

    # ── CSV 文件加载 ─────────────────────────────────────────────────────

    def _load_csv_list(self):
        """扫描回测结果目录，填充文件下拉框。"""
        self.fileCombo.blockSignals(True)
        self.fileCombo.clear()

        csv_files: list[Path] = []
        if self.review_dir.is_dir():
            csv_files = sorted(self.review_dir.glob(REVIEW_CSV_GLOB), reverse=True)

        if not csv_files:
            self.fileCombo.addItem("(未找到回测结果文件)")
            self.fileCombo.blockSignals(False)
            return

        for csv_path in csv_files:
            self.fileCombo.addItem(csv_path.name, str(csv_path))

        self.fileCombo.blockSignals(False)
        self._on_file_changed(0)

    def _on_file_changed(self, index: int):
        csv_path_str = self.fileCombo.itemData(index)
        if not csv_path_str:
            return

        csv_path = Path(csv_path_str)
        if not csv_path.exists():
            self.statusMessageRequested.emit(f"文件不存在: {csv_path.name}", 3000)
            return

        try:
            df = pd.read_csv(csv_path, dtype={CSV_COL_CODE: str}, encoding="utf-8-sig")
            if CSV_COL_CODE in df.columns:
                df[CSV_COL_CODE] = df[CSV_COL_CODE].astype(str).str.zfill(6)
            if CSV_COL_RATING not in df.columns:
                df[CSV_COL_RATING] = ""
            self._signal_df = df
        except Exception as exc:
            logger.exception("读取回测结果CSV失败")
            self.statusMessageRequested.emit(f"读取失败: {exc}", 5000)
            return

        self._populate_filters()
        self._apply_filter()
        self.statusMessageRequested.emit(f"已加载 {csv_path.name}，共 {len(df)} 条信号", 3000)

    # ── 筛选 ─────────────────────────────────────────────────────────────

    def _populate_filters(self):
        if self._signal_df is None:
            return

        self.modeFilter.blockSignals(True)
        self.modeFilter.clear()
        self.modeFilter.addItem("全部模式")
        if CSV_COL_MODE in self._signal_df.columns:
            modes = sorted(self._signal_df[CSV_COL_MODE].dropna().unique().tolist())
            self.modeFilter.addItems([str(m) for m in modes])
        self.modeFilter.blockSignals(False)

        self.gradeFilter.blockSignals(True)
        self.gradeFilter.clear()
        self.gradeFilter.addItem("全部等级")
        if CSV_COL_GRADE in self._signal_df.columns:
            grades = sorted(self._signal_df[CSV_COL_GRADE].dropna().unique().tolist())
            self.gradeFilter.addItems([str(g) for g in grades])
        self.gradeFilter.blockSignals(False)

    def _apply_filter(self, _text: str = ""):
        if self._signal_df is None:
            self._populate_table(pd.DataFrame())
            return

        df = self._signal_df.copy()

        mode_text = self.modeFilter.currentText()
        if mode_text and mode_text != "全部模式" and CSV_COL_MODE in df.columns:
            df = df[df[CSV_COL_MODE] == mode_text]

        grade_text = self.gradeFilter.currentText()
        if grade_text and grade_text != "全部等级" and CSV_COL_GRADE in df.columns:
            df = df[df[CSV_COL_GRADE] == grade_text]

        self._populate_table(df)

    # ── 表格填充 ─────────────────────────────────────────────────────────

    def _populate_table(self, df: pd.DataFrame):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        if df.empty:
            self.countLabel.setText("共 0 条信号")
            return

        self.table.setRowCount(len(df))

        for row_idx, (_, row) in enumerate(df.iterrows()):
            for col_idx, col_name in enumerate(TABLE_COLUMNS):
                value = row.get(col_name, "")
                text = str(value) if pd.notna(value) else ""

                item = QtWidgets.QTableWidgetItem()

                if col_name in (CSV_COL_SCORE, CSV_COL_T1_RETURN, CSV_COL_T3_RETURN, CSV_COL_T5_RETURN):
                    try:
                        numeric_val = float(text)
                        item.setData(QtCore.Qt.DisplayRole, numeric_val)
                    except (ValueError, TypeError):
                        item.setText(text)
                else:
                    item.setText(text)

                if col_name in (CSV_COL_T1_RETURN, CSV_COL_T3_RETURN, CSV_COL_T5_RETURN):
                    try:
                        pct = float(text)
                        if pct > 0:
                            item.setForeground(QtGui.QColor("#ff4d4f"))
                        elif pct < 0:
                            item.setForeground(QtGui.QColor("#00b050"))
                    except (ValueError, TypeError):
                        pass

                if col_idx in EDITABLE_COLUMN_INDICES:
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)

                self.table.setItem(row_idx, col_idx, item)

        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        self.countLabel.setText(f"共 {len(df)} 条信号")

    # ── 右键删除 ─────────────────────────────────────────────────────────

    def _on_table_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is None:
            return

        row_idx = item.row()
        name_item = self.table.item(row_idx, 0)
        symbol_item = self.table.item(row_idx, 1)
        signal_date_item = self.table.item(row_idx, 2)

        name = name_item.text() if name_item else ""
        symbol = symbol_item.text() if symbol_item else ""
        signal_date = signal_date_item.text() if signal_date_item else ""

        menu = QtWidgets.QMenu(self)
        delete_action = menu.addAction(f"删除  {name}({symbol}) {signal_date}")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))

        if action == delete_action:
            self._confirm_and_delete_row(row_idx, name, symbol, signal_date)

    def _confirm_and_delete_row(self, row_idx: int, name: str, symbol: str, signal_date: str):
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除 {name}({symbol}) {signal_date} 这条信号吗？\n\n此操作将同步删除 CSV 文件中的记录，不可撤销。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        if self._signal_df is None:
            return

        symbol_clean = symbol.strip()
        signal_date_clean = signal_date.strip()

        match_mask = (
            (self._signal_df[CSV_COL_CODE].astype(str).str.zfill(6) == symbol_clean)
            & (self._signal_df[CSV_COL_DATE].astype(str).str.strip() == signal_date_clean)
        )
        matched_count = int(match_mask.sum())

        if matched_count == 0:
            logger.warning("删除失败: 未匹配到 %s=%s %s=%s", CSV_COL_CODE, symbol_clean, CSV_COL_DATE, signal_date_clean)
            self.statusMessageRequested.emit("未找到匹配的记录，删除失败", 3000)
            return

        before_count = len(self._signal_df)
        self._signal_df = self._signal_df[~match_mask].reset_index(drop=True)
        after_count = len(self._signal_df)
        logger.info("删除 %s(%s) %s: %d -> %d 条", name, symbol_clean, signal_date_clean, before_count, after_count)

        self._save_current_csv()
        self._apply_filter()

    def _save_current_csv(self):
        """将当前内存中的 DataFrame 写回 CSV 文件。"""
        csv_path_str = self.fileCombo.currentData()
        if not csv_path_str or self._signal_df is None:
            return

        csv_path = Path(csv_path_str)
        try:
            self._signal_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        except Exception as exc:
            logger.exception("保存CSV失败")
            self.statusMessageRequested.emit(f"保存CSV失败: {exc}", 5000)

    # ── 模式修改 ─────────────────────────────────────────────────────────

    def _on_mode_changed(self, visual_row: int, new_mode: str):
        """双击修改"模式"列后，同步更新 _signal_df 和 CSV 文件。"""
        if self._signal_df is None:
            return

        symbol_item = self.table.item(visual_row, 1)
        signal_date_item = self.table.item(visual_row, 2)
        if not symbol_item or not signal_date_item:
            return

        symbol_clean = symbol_item.text().strip()
        signal_date_clean = signal_date_item.text().strip()

        match_mask = (
            (self._signal_df[CSV_COL_CODE].astype(str).str.zfill(6) == symbol_clean)
            & (self._signal_df[CSV_COL_DATE].astype(str).str.strip() == signal_date_clean)
        )

        if not match_mask.any():
            logger.warning("模式修改失败: 未匹配到 %s=%s %s=%s", CSV_COL_CODE, symbol_clean, CSV_COL_DATE, signal_date_clean)
            return

        self._signal_df.loc[match_mask, CSV_COL_MODE] = new_mode
        self._save_current_csv()

        name_item = self.table.item(visual_row, 0)
        name = name_item.text() if name_item else ""
        logger.info("模式修改 %s(%s) %s -> %s", name, symbol_clean, signal_date_clean, new_mode)
        self.statusMessageRequested.emit(
            f"{name}({symbol_clean}) 模式已修改为「{new_mode}」", 3000
        )

    def _on_rating_changed(self, visual_row: int, new_rating: str):
        """双击修改"评价"列后，同步更新 _signal_df 和 CSV 文件。"""
        if self._signal_df is None:
            return

        symbol_item = self.table.item(visual_row, 1)
        signal_date_item = self.table.item(visual_row, 2)
        if not symbol_item or not signal_date_item:
            return

        symbol_clean = symbol_item.text().strip()
        signal_date_clean = signal_date_item.text().strip()

        match_mask = (
            (self._signal_df[CSV_COL_CODE].astype(str).str.zfill(6) == symbol_clean)
            & (self._signal_df[CSV_COL_DATE].astype(str).str.strip() == signal_date_clean)
        )

        if not match_mask.any():
            logger.warning("评价修改失败: 未匹配到 %s=%s %s=%s", CSV_COL_CODE, symbol_clean, CSV_COL_DATE, signal_date_clean)
            return

        self._signal_df.loc[match_mask, CSV_COL_RATING] = new_rating
        self._save_current_csv()

        name_item = self.table.item(visual_row, 0)
        name = name_item.text() if name_item else ""
        logger.info("评价修改 %s(%s) %s -> %s", name, symbol_clean, signal_date_clean, new_rating)
        self.statusMessageRequested.emit(
            f"{name}({symbol_clean}) 评价已修改为「{new_rating}」", 3000
        )

    # ── 表格选中 → 加载图表 ──────────────────────────────────────────────

    def _on_table_selection_changed(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        row_idx = selected_rows[0].row()
        symbol_item = self.table.item(row_idx, 1)
        name_item = self.table.item(row_idx, 0)
        signal_date_item = self.table.item(row_idx, 2)

        if not symbol_item or not signal_date_item:
            return

        symbol = symbol_item.text().strip()
        name = name_item.text().strip() if name_item else ""
        signal_date_str = signal_date_item.text().strip()

        if not symbol or not signal_date_str:
            return

        self._load_chart(symbol, name, signal_date_str)

    def _load_chart(self, symbol: str, name: str, signal_date_str: str):
        """加载全量日线数据（保证指标计算准确），然后将视图定位到信号日前后区间，并画纵向虚线。"""
        try:
            df = load_daily_csv(self.stock_daily_data_dir, symbol)
        except Exception as exc:
            logger.exception("加载日线数据失败: %s", symbol)
            self.statusMessageRequested.emit(f"加载 {symbol} 数据失败: {exc}", 5000)
            return

        if df.empty:
            self.statusMessageRequested.emit(f"{symbol} 无日线数据", 3000)
            return

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        try:
            signal_date = pd.Timestamp(signal_date_str)
        except Exception:
            self.statusMessageRequested.emit(f"无法解析信号日期: {signal_date_str}", 3000)
            return

        date_index = df.index[df["date"] == signal_date]
        if len(date_index) == 0:
            date_diffs = (df["date"] - signal_date).abs()
            closest_idx = date_diffs.idxmin()
            signal_pos_in_df = int(closest_idx)
        else:
            signal_pos_in_df = int(date_index[0])

        self._current_chart_df = df
        self._current_symbol = symbol
        self._current_name = name

        self.chart.set_stock_info(symbol, name)
        self.chart.set_daily(df)

        self._add_signal_date_vline(signal_pos_in_df, signal_date_str)

        # 右边界对齐到信号日当天
        self._current_view_right_idx = signal_pos_in_df
        self._apply_view_range()

        self.statusMessageRequested.emit(
            f"已加载 {name}({symbol}) 信号日 {signal_date_str}", 3000
        )

    def _apply_view_range(self):
        """根据 _current_view_right_idx 设置视图范围，右边界对齐到该索引。"""
        if self._current_chart_df is None:
            return
        total = len(self._current_chart_df)
        right_idx = max(0, min(self._current_view_right_idx, total - 1))
        left_idx = max(0, right_idx - SIGNAL_DATE_MARGIN_BEFORE - SIGNAL_DATE_MARGIN_AFTER)
        view_x0 = float(left_idx) - self.chart._item_half_width
        view_x1 = float(right_idx) + self.chart._item_half_width + self.chart._right_view_padding
        self.chart.pricePlot.setXRange(view_x0, view_x1, padding=0)
        self.chart._update_visible_yrange(view_x0, view_x1)

    def _add_signal_date_vline(self, x_index: int, date_str: str):
        """在主图的信号日位置添加纵向虚线标记。"""
        if self._signal_vline is not None:
            try:
                self.chart.pricePlot.removeItem(self._signal_vline)
            except Exception:
                pass
            self._signal_vline = None

        self._signal_vline = pg.InfiniteLine(
            pos=float(x_index),
            angle=90,
            movable=False,
            pen=pg.mkPen(color=(255, 165, 0, 200), width=1.5, style=QtCore.Qt.DashLine),
            label=f"信号日 {date_str}",
            labelOpts={
                "position": 0.95,
                "color": (255, 165, 0),
                "fill": (40, 40, 40, 180),
                "movable": False,
            },
        )
        self._signal_vline.setZValue(1000)
        self.chart.pricePlot.addItem(self._signal_vline, ignoreBounds=True)

    # ── 键盘事件：左右方向键平移视图 ─────────────────────────────────────

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        key = event.key()
        if key == QtCore.Qt.Key_Left:
            self._shift_view(-1)
            event.accept()
        elif key == QtCore.Qt.Key_Right:
            self._shift_view(1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def _shift_view(self, delta: int):
        """将视图右边界平移 delta 个交易日（正数向右/未来，负数向左/过去）。"""
        if self._current_chart_df is None:
            return
        total = len(self._current_chart_df)
        new_right = self._current_view_right_idx + delta
        new_right = max(SIGNAL_DATE_MARGIN_BEFORE + SIGNAL_DATE_MARGIN_AFTER, min(new_right, total - 1))
        if new_right == self._current_view_right_idx:
            return
        self._current_view_right_idx = new_right
        self._apply_view_range()
