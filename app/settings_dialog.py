from __future__ import annotations

from PySide6 import QtWidgets


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, token: str, min_visible_days: int, max_visible_days: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(420, 240)

        layout = QtWidgets.QVBoxLayout(self)

        api_group = QtWidgets.QGroupBox("数据接口")
        api_form = QtWidgets.QFormLayout(api_group)
        self.tokenEdit = QtWidgets.QLineEdit(str(token or "").strip())
        self.tokenEdit.setPlaceholderText("请输入 Tushare Token")
        api_form.addRow("Tushare Token", self.tokenEdit)

        chart_group = QtWidgets.QGroupBox("图表缩放")
        chart_form = QtWidgets.QFormLayout(chart_group)
        self.minDaysSpin = QtWidgets.QSpinBox()
        self.minDaysSpin.setRange(1, 10000)
        self.minDaysSpin.setValue(max(int(min_visible_days), 1))
        self.maxDaysSpin = QtWidgets.QSpinBox()
        self.maxDaysSpin.setRange(2, 10000)
        self.maxDaysSpin.setValue(max(int(max_visible_days), self.minDaysSpin.value() + 1))
        chart_form.addRow("最小可见天数", self.minDaysSpin)
        chart_form.addRow("最大可见天数", self.maxDaysSpin)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttonBox.accepted.connect(self._validate_and_accept)
        self.buttonBox.rejected.connect(self.reject)

        layout.addWidget(api_group)
        layout.addWidget(chart_group)
        layout.addWidget(self.buttonBox)

    def _validate_and_accept(self):
        token = self.tokenEdit.text().strip()
        min_days = self.minDaysSpin.value()
        max_days = self.maxDaysSpin.value()

        if not token:
            QtWidgets.QMessageBox.warning(self, "配置无效", "Tushare Token 不能为空。")
            self.tokenEdit.setFocus()
            return

        if min_days < 1:
            QtWidgets.QMessageBox.warning(self, "配置无效", "最小可见天数必须大于等于 1。")
            self.minDaysSpin.setFocus()
            return

        if max_days <= min_days:
            QtWidgets.QMessageBox.warning(self, "配置无效", "最大可见天数必须大于最小可见天数。")
            self.maxDaysSpin.setFocus()
            return

        self.accept()

    def get_settings(self) -> dict:
        return {
            "tushare_token": self.tokenEdit.text().strip(),
            "min_visible_days": self.minDaysSpin.value(),
            "max_visible_days": self.maxDaysSpin.value(),
        }
