"""砖形图+MACD 回测分析页面。

读取 output/brick_macd_backtest.csv，左侧表格 + 筛选栏，右侧 K 线+指标图表。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets

from ..data_loader import load_daily_csv
from ..widgets import StockChartWidget

GRADE_COLORS = {
    "S": "#D5F5D5",
    "A": "#F6FFED",
    "B": "#FFFBE6",
    "C": "#FFF1E6",
    "D": "#FFF1F0",
}


class BacktestPage(QtWidgets.QWidget):
    """砖形图+MACD 回测结果浏览页面"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self._stock_daily_data_dir = root / "stock_daily_data"
        self._csv_path = root / "output" / "brick_macd_backtest.csv"
        self._df_all: pd.DataFrame = pd.DataFrame()
        self._df_filtered: pd.DataFrame = pd.DataFrame()
        self._loaded = False

        self._setup_ui()
        self._connect_signals()

    # ── UI ───────────────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 标题
        title = QtWidgets.QLabel("回测分析")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        main_layout.addWidget(title)

        # 筛选栏
        filter_row = QtWidgets.QHBoxLayout()

        filter_row.addWidget(QtWidgets.QLabel("定式:"))
        self._pattern_combo = QtWidgets.QComboBox()
        self._pattern_combo.addItem("全部", "")
        self._pattern_combo.setFixedWidth(120)
        filter_row.addWidget(self._pattern_combo)

        filter_row.addWidget(QtWidgets.QLabel("等级:"))
        self._grade_combo = QtWidgets.QComboBox()
        self._grade_combo.addItem("全部", "")
        for g in ("S", "A", "B", "C", "D"):
            self._grade_combo.addItem(g, g)
        self._grade_combo.setFixedWidth(70)
        filter_row.addWidget(self._grade_combo)

        filter_row.addWidget(QtWidgets.QLabel("MACD:"))
        self._macd_combo = QtWidgets.QComboBox()
        self._macd_combo.addItem("全部", "")
        self._macd_combo.addItem("DIFF>0", "diff_above_zero")
        self._macd_combo.addItem("DIFF>DEA", "diff_above_dea")
        self._macd_combo.addItem("MACD柱>0", "bar_positive")
        self._macd_combo.addItem("MACD柱翻红", "bar_turn_positive")
        self._macd_combo.addItem("零轴附近", "diff_near_zero")
        self._macd_combo.setFixedWidth(120)
        filter_row.addWidget(self._macd_combo)

        filter_row.addWidget(QtWidgets.QLabel("收益:"))
        self._return_combo = QtWidgets.QComboBox()
        self._return_combo.addItem("全部", "")
        self._return_combo.addItem("T3>0 盈利", "win")
        self._return_combo.addItem("T3<0 亏损", "lose")
        self._return_combo.setFixedWidth(100)
        filter_row.addWidget(self._return_combo)

        self._reload_btn = QtWidgets.QPushButton("重新加载")
        self._reload_btn.setFixedWidth(80)
        filter_row.addWidget(self._reload_btn)

        filter_row.addStretch()

        self._stats_label = QtWidgets.QLabel("")
        self._stats_label.setStyleSheet("color: #666; font-size: 12px;")
        filter_row.addWidget(self._stats_label)

        main_layout.addLayout(filter_row)

        # splitter: table | chart
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self._table = QtWidgets.QTableWidget()
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(24)

        self._headers = ["代码", "名称", "日期", "定式", "评分", "等级", "收盘", "T+1%", "T+2%", "T+3%", "DIFF>0", "DIFF>DEA", "柱翻红"]
        self._table.setColumnCount(len(self._headers))
        self._table.setHorizontalHeaderLabels(self._headers)
        self._table.horizontalHeader().setStretchLastSection(True)

        self._table.setColumnWidth(0, 65)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 85)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(4, 55)
        self._table.setColumnWidth(5, 42)
        self._table.setColumnWidth(6, 60)
        self._table.setColumnWidth(7, 60)
        self._table.setColumnWidth(8, 60)
        self._table.setColumnWidth(9, 60)
        self._table.setColumnWidth(10, 55)
        self._table.setColumnWidth(11, 65)
        self._table.setColumnWidth(12, 55)

        self._chart = StockChartWidget()

        splitter.addWidget(self._table)
        splitter.addWidget(self._chart)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([500, 800])

        main_layout.addWidget(splitter, 1)

    def _connect_signals(self):
        self._pattern_combo.currentIndexChanged.connect(self._apply_filter)
        self._grade_combo.currentIndexChanged.connect(self._apply_filter)
        self._macd_combo.currentIndexChanged.connect(self._apply_filter)
        self._return_combo.currentIndexChanged.connect(self._apply_filter)
        self._reload_btn.clicked.connect(self._load_csv)
        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            QtCore.QTimer.singleShot(0, self._load_csv)

    # ── 数据加载 ─────────────────────────────────────────────

    def _load_csv(self):
        if not self._csv_path.exists():
            self._stats_label.setText("未找到回测数据文件")
            self.statusMessageRequested.emit("未找到 output/brick_macd_backtest.csv", 3000)
            return

        self._df_all = pd.read_csv(self._csv_path, dtype={"symbol": str})
        self._df_all["symbol"] = self._df_all["symbol"].astype(str).str.zfill(6)

        # 填充定式下拉
        patterns = sorted(self._df_all["pattern"].dropna().unique().tolist())
        self._pattern_combo.blockSignals(True)
        self._pattern_combo.clear()
        self._pattern_combo.addItem("全部", "")
        for p in patterns:
            self._pattern_combo.addItem(p, p)
        self._pattern_combo.blockSignals(False)

        self._apply_filter()

    def _apply_filter(self):
        df = self._df_all.copy()
        if df.empty:
            self._populate_table(df)
            return

        pattern = self._pattern_combo.currentData()
        if pattern:
            df = df[df["pattern"] == pattern]

        grade = self._grade_combo.currentData()
        if grade:
            df = df[df["grade"] == grade]

        macd_cond = self._macd_combo.currentData()
        if macd_cond:
            if macd_cond in df.columns:
                df = df[df[macd_cond] == True]

        ret_filter = self._return_combo.currentData()
        if ret_filter == "win":
            df = df[df["ret_total"] > 0]
        elif ret_filter == "lose":
            df = df[df["ret_total"] < 0]

        self._df_filtered = df.reset_index(drop=True)
        self._populate_table(self._df_filtered)

        total = len(self._df_all)
        shown = len(self._df_filtered)
        if shown > 0 and "ret_total" in self._df_filtered.columns:
            valid = self._df_filtered["ret_total"].dropna()
            win_rate = (valid > 0).mean() * 100 if len(valid) > 0 else 0
            avg_ret = valid.mean() if len(valid) > 0 else 0
            self._stats_label.setText(
                f"共{total}条 | 显示{shown}条 | 胜率{win_rate:.1f}% | T3均值{avg_ret:+.2f}%"
            )
        else:
            self._stats_label.setText(f"共{total}条 | 显示{shown}条")

    _DF_INDEX_ROLE = QtCore.Qt.UserRole + 1

    def _populate_table(self, df: pd.DataFrame):
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        if df.empty:
            self._table.setSortingEnabled(True)
            return

        self._table.setRowCount(len(df))
        for row_idx, (df_idx, r) in enumerate(df.iterrows()):
            first_item = QtWidgets.QTableWidgetItem(str(r.get("symbol", "")))
            first_item.setData(self._DF_INDEX_ROLE, df_idx)
            self._table.setItem(row_idx, 0, first_item)
            self._table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(r.get("name", ""))))
            self._table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(str(r.get("date", ""))[:10]))
            self._table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(str(r.get("pattern", ""))))

            score_item = _num_item(r.get("final_score"), "{:.0f}")
            self._table.setItem(row_idx, 4, score_item)

            grade_val = str(r.get("grade", ""))
            grade_item = QtWidgets.QTableWidgetItem(grade_val)
            grade_item.setTextAlignment(QtCore.Qt.AlignCenter)
            bg = GRADE_COLORS.get(grade_val, "#FFF")
            grade_item.setBackground(QtGui.QColor(bg))
            self._table.setItem(row_idx, 5, grade_item)

            self._table.setItem(row_idx, 6, _num_item(r.get("close"), "{:.2f}"))
            self._table.setItem(row_idx, 7, _ret_item(r.get("ret_t1")))
            self._table.setItem(row_idx, 8, _ret_item(r.get("ret_t2")))
            self._table.setItem(row_idx, 9, _ret_item(r.get("ret_t3")))

            self._table.setItem(row_idx, 10, _bool_item(r.get("diff_above_zero")))
            self._table.setItem(row_idx, 11, _bool_item(r.get("diff_above_dea")))
            self._table.setItem(row_idx, 12, _bool_item(r.get("bar_turn_positive")))

        self._table.setSortingEnabled(True)

    # ── 图表联动 ─────────────────────────────────────────────

    def _on_row_changed(self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex):
        row = current.row()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        df_idx = item.data(self._DF_INDEX_ROLE)
        if df_idx is None or df_idx >= len(self._df_filtered):
            return
        r = self._df_filtered.iloc[df_idx]
        symbol = str(r["symbol"]).zfill(6)
        date_str = str(r["date"])[:10]
        self._load_chart(symbol, date_str)

    def _load_chart(self, symbol: str, target_date: str):
        try:
            df = load_daily_csv(self._stock_daily_data_dir, symbol)
        except FileNotFoundError:
            self.statusMessageRequested.emit(f"{symbol} 无本地数据", 3000)
            return
        if df.empty:
            return

        df_full = df.copy().reset_index(drop=True)
        target_index = None
        for i, row_data in df_full.iterrows():
            date_val = row_data["date"]
            ds = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)[:10]
            if ds <= target_date:
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


# ── 辅助函数 ─────────────────────────────────────────────────


class _SortableItem(QtWidgets.QTableWidgetItem):
    """数值排序友好的 TableWidgetItem"""

    def __init__(self, text: str, sort_value: float):
        super().__init__(text)
        self._sort_value = sort_value
        self.setTextAlignment(QtCore.Qt.AlignCenter)

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


def _num_item(val, fmt: str = "{:.2f}") -> _SortableItem:
    try:
        v = float(val)
        return _SortableItem(fmt.format(v), v)
    except (TypeError, ValueError):
        return _SortableItem("--", float("-inf"))


def _ret_item(val) -> _SortableItem:
    try:
        v = float(val)
        item = _SortableItem(f"{v:+.2f}", v)
        if v > 0:
            item.setForeground(QtGui.QColor("#CC3333"))
        elif v < 0:
            item.setForeground(QtGui.QColor("#33AA33"))
        return item
    except (TypeError, ValueError):
        return _SortableItem("--", float("-inf"))


def _bool_item(val) -> QtWidgets.QTableWidgetItem:
    if isinstance(val, bool):
        text = "Y" if val else ""
    elif isinstance(val, str):
        text = "Y" if val.lower() == "true" else ""
    else:
        text = ""
    item = QtWidgets.QTableWidgetItem(text)
    item.setTextAlignment(QtCore.Qt.AlignCenter)
    if text == "Y":
        item.setForeground(QtGui.QColor("#1890FF"))
    return item
