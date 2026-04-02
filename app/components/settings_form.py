from __future__ import annotations

from PySide6 import QtWidgets


class SettingsFormWidget(QtWidgets.QWidget):
    """可复用的设置表单组件：Token + 最小/最大可见天数。

    用于 MarketPage（折叠面板）、SettingsPage、SettingsDialog 等场景，
    消除重复的 UI 创建和取值逻辑。
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        api_group = QtWidgets.QGroupBox("数据接口")
        api_form = QtWidgets.QFormLayout(api_group)
        self.token_edit = QtWidgets.QLineEdit()
        self.token_edit.setPlaceholderText("请输入 Tushare Token")
        api_form.addRow("Tushare Token", self.token_edit)
        layout.addWidget(api_group)

        chart_group = QtWidgets.QGroupBox("图表显示")
        chart_form = QtWidgets.QFormLayout(chart_group)
        self.min_days_spin = QtWidgets.QSpinBox()
        self.min_days_spin.setRange(1, 10000)
        self.max_days_spin = QtWidgets.QSpinBox()
        self.max_days_spin.setRange(2, 10000)
        chart_form.addRow("最小可见天数", self.min_days_spin)
        chart_form.addRow("最大可见天数", self.max_days_spin)
        layout.addWidget(chart_group)

    def get_token(self) -> str:
        return self.token_edit.text().strip()

    def get_min_days(self) -> int:
        return self.min_days_spin.value()

    def get_max_days(self) -> int:
        return self.max_days_spin.value()

    def set_values(self, token: str, min_days: int, max_days: int) -> None:
        self.token_edit.setText(str(token or ""))
        self.min_days_spin.setValue(max(int(min_days), 1))
        self.max_days_spin.setValue(max(int(max_days), 2))
