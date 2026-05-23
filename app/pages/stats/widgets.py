"""统计页：自定义 widget 组件。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .constants import (
    API_DISPLAY_NAMES,
    OPERATION_MAP,
    OPERATION_SORT_ORDER,
    _name_initials,
)


class ApiCard(QtWidgets.QFrame):
    """单个接口的进度卡片"""

    def __init__(self, api_id: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.api_id = api_id
        self.setObjectName(f"apiCard_{api_id}")
        self.setFixedHeight(42)
        self.setStyleSheet("""
            ApiCard {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            ApiCard QLabel {
                background: transparent;
                border: none;
            }
            ApiCard QProgressBar {
                background: transparent;
                border: none;
            }
        """)
        self._setup_ui()
        self.reset()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(8)

        self.titleLabel = QtWidgets.QLabel(API_DISPLAY_NAMES.get(self.api_id, self.api_id))
        self.titleLabel.setStyleSheet("font-size: 12px; font-weight: 600; color: #e2e8f0; background: transparent;")
        layout.addWidget(self.titleLabel)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFixedHeight(12)
        self.progressBar.setMinimumWidth(120)
        self.progressBar.setStyleSheet("""
            QProgressBar {
                background: #0f172a;
                border: none;
                border-radius: 4px;
                text-align: center;
                font-size: 10px;
                font-weight: 600;
                color: #e2e8f0;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3b82f6, stop:1 #8b5cf6);
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progressBar, 1)

        self.infoLabel = QtWidgets.QLabel("")
        self.infoLabel.setStyleSheet("font-size: 10px; color: #94a3b8; background: transparent;")
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
        base_style = "padding: 2px 8px; border-radius: 8px; font-size: 11px; font-weight: 600;"
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
    rateDetailRequested = QtCore.Signal(str, str, str)  # (code, op, name)

    COLUMNS = [
        ("code", "股票代码", "string"),
        ("name", "股票名称", "string"),
        ("op", "操作", "op"),
        ("_count", "持有人数", "number"),
        ("_rate_detail", "收益详情", "action"),
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
        self.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(4, 100)
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
            elif col_type == "action":
                return 0
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

            detail_button = QtWidgets.QPushButton("📊 收益详情")
            detail_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            detail_button.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #3b82f6, stop:1 #8b5cf6);
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 5px 12px;
                    font-size: 12px;
                    font-weight: 700;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #2563eb, stop:1 #7c3aed);
                }
            """)
            code = str(item.get("code", ""))
            op = str(item.get("op", "0"))
            name = str(item.get("name", ""))
            detail_button.clicked.connect(
                lambda _checked=False, c=code, o=op, n=name: self.rateDetailRequested.emit(c, o, n)
            )
            self.setCellWidget(row, 4, detail_button)

    def filter_by_ops(self, active_ops: set[str]):
        self._active_ops = active_ops
        self._apply_combined_filter()

    def filter_by_text(self, text: str):
        self._search_text = text.strip().lower()
        self._apply_combined_filter()

    def _apply_combined_filter(self):
        active_ops = getattr(self, "_active_ops", set())
        search_text = getattr(self, "_search_text", "")

        filtered = self._raw_data
        if active_ops:
            filtered = [
                item for item in filtered
                if str(item.get("op", "")) in active_ops
            ]
        if search_text:
            filtered = [
                item for item in filtered
                if self._match_search(item, search_text)
            ]
        self._display_data = filtered
        if self._sort_column >= 0:
            self._sort_and_refresh()
        else:
            self._refresh_table()

    @staticmethod
    def _match_search(item: dict, query: str) -> bool:
        code = str(item.get("code", "")).lower()
        name = str(item.get("name", "")).lower()
        initials = _name_initials(item.get("name", ""))
        return query in code or query in name or query in initials

    def _on_cell_double_clicked(self, row: int, _column: int):
        code_item = self.item(row, 0)
        name_item = self.item(row, 1)
        if code_item:
            code = code_item.text()
            name = name_item.text() if name_item else ""
            self.stockDoubleClicked.emit(code, name)


# ═══════════════════════════════════════════════════════════════════════════
#  收益详情弹窗（左表格 + 右正态分布图）
# ═══════════════════════════════════════════════════════════════════════════
