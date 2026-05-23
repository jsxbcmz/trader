from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.services import AppSettings, SettingsService


class SettingsPage(QtWidgets.QWidget):
    statusMessageRequested = QtCore.Signal(str, int)
    settingsSaveRequested = QtCore.Signal(object)
    updateAllRequested = QtCore.Signal()

    def __init__(self, settings_service: SettingsService, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.settings_service = settings_service
        self._setup_ui()
        self._connect_signals()
        self.set_settings(self.settings_service.load())

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        description = QtWidgets.QLabel("在这里维护接口 Token、图表显示参数，并触发批量更新股票数据。")
        description.setWordWrap(True)
        description.setStyleSheet("color: #666;")
        layout.addWidget(description)

        api_group = QtWidgets.QGroupBox("数据接口")
        api_form = QtWidgets.QFormLayout(api_group)
        self.tokenEdit = QtWidgets.QLineEdit()
        self.tokenEdit.setPlaceholderText("请输入 Tushare Token")
        api_form.addRow("Tushare Token", self.tokenEdit)
        layout.addWidget(api_group)

        chart_group = QtWidgets.QGroupBox("图表显示")
        chart_form = QtWidgets.QFormLayout(chart_group)
        self.minDaysSpin = QtWidgets.QSpinBox()
        self.minDaysSpin.setRange(1, 10000)
        self.maxDaysSpin = QtWidgets.QSpinBox()
        self.maxDaysSpin.setRange(2, 10000)
        chart_form.addRow("最小可见天数", self.minDaysSpin)
        chart_form.addRow("最大可见天数", self.maxDaysSpin)
        layout.addWidget(chart_group)

        update_group = QtWidgets.QGroupBox("数据更新")
        update_layout = QtWidgets.QVBoxLayout(update_group)
        self.updateHintLabel = QtWidgets.QLabel("批量更新将复用现有更新线程与进度弹窗，执行期间会自动禁用重复触发入口。")
        self.updateHintLabel.setWordWrap(True)
        self.updateHintLabel.setStyleSheet("color: #666;")
        self.updateAllBtn = QtWidgets.QPushButton("更新全部股票")
        update_layout.addWidget(self.updateHintLabel)
        update_layout.addWidget(self.updateAllBtn, 0, QtCore.Qt.AlignLeft)
        layout.addWidget(update_group)

        self.saveBtn = QtWidgets.QPushButton("保存设置")
        layout.addWidget(self.saveBtn, 0, QtCore.Qt.AlignLeft)
        layout.addStretch(1)

    def _connect_signals(self):
        self.saveBtn.clicked.connect(self._emit_save_request)
        self.updateAllBtn.clicked.connect(self.updateAllRequested.emit)

    def set_settings(self, app_settings: AppSettings):
        self.tokenEdit.setText(app_settings.tushare_token)
        self.minDaysSpin.setValue(app_settings.min_visible_days)
        self.maxDaysSpin.setValue(app_settings.max_visible_days)

    def set_update_enabled(self, enabled: bool):
        self.updateAllBtn.setEnabled(enabled)

    def _emit_save_request(self):
        try:
            app_settings = self.settings_service.normalize_settings(
                token=self.tokenEdit.text().strip(),
                min_days=self.minDaysSpin.value(),
                max_days=self.maxDaysSpin.value(),
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "配置无效", str(exc))
            return
        self.settingsSaveRequested.emit(app_settings)
