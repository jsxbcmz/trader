from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .pages import MarketPage, ScreeningPage, SettingsPage, TemplatePage
from .services import AppSettings, SettingsService


class PlaceholderPage(QtWidgets.QWidget):
    def __init__(self, title: str, description: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel(title)
        font = QtGui.QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)

        body = QtWidgets.QLabel(description)
        body.setWordWrap(True)
        body.setStyleSheet("color: #666;")

        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch(1)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, root: Path):
        super().__init__()
        self.setWindowTitle("StockViewer")
        self.resize(1200, 800)
        self.root = root
        self.settingsService = SettingsService()

        self._setup_ui()
        self._setup_menu_bar()
        self._connect_signals()
        self.switch_page(0)

    def _setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.pageStack = QtWidgets.QStackedWidget()
        self.marketPage = MarketPage(self.root, settings_service=self.settingsService)
        self.templatePage = TemplatePage(self.root)
        self.settingsPage = SettingsPage(self.settingsService)
        self.screeningPage = ScreeningPage(self.root)

        self.pageStack.addWidget(self.marketPage)
        self.pageStack.addWidget(self.templatePage)
        self.pageStack.addWidget(self.settingsPage)
        self.pageStack.addWidget(self.screeningPage)

        layout.addWidget(self.pageStack, 1)

        self.statusBar().showMessage("就绪")

    def _setup_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("文件")
        self.exitAction = file_menu.addAction("退出")

        view_menu = menu_bar.addMenu("视图")
        self.openMarketAction = view_menu.addAction("打开看盘页")
        self.openTemplateAction = view_menu.addAction("打开模板页")
        self.openSettingsAction = view_menu.addAction("打开设置页")
        self.openScreeningAction = view_menu.addAction("打开选股页")

        data_menu = menu_bar.addMenu("数据")
        self.updateAllAction = data_menu.addAction("更新全部股票")

        tools_menu = menu_bar.addMenu("工具")
        self.newTemplateAction = tools_menu.addAction("新建模板")

        help_menu = menu_bar.addMenu("帮助")
        self.aboutAction = help_menu.addAction("关于")

    def _connect_signals(self):
        self.marketPage.statusMessageRequested.connect(self._show_status_message)
        self.marketPage.updateRunningChanged.connect(self._on_update_running_changed)
        self.templatePage.statusMessageRequested.connect(self._show_status_message)
        self.templatePage.templatesChanged.connect(self.marketPage.reload_templates)
        self.templatePage.templatesChanged.connect(self.screeningPage.reload_templates)
        self.screeningPage.statusMessageRequested.connect(self._show_status_message)
        self.settingsPage.settingsSaveRequested.connect(self._save_settings_from_page)
        self.settingsPage.updateAllRequested.connect(self._request_update_all)
        self.exitAction.triggered.connect(self.close)
        self.openMarketAction.triggered.connect(lambda: self.switch_page(0))
        self.openTemplateAction.triggered.connect(lambda: self.switch_page(1))
        self.openSettingsAction.triggered.connect(lambda: self.switch_page(2))
        self.openScreeningAction.triggered.connect(lambda: self.switch_page(3))
        self.updateAllAction.triggered.connect(self._request_update_all)
        self.newTemplateAction.triggered.connect(self._open_new_template_dialog)
        self.aboutAction.triggered.connect(self._show_about_dialog)

    @QtCore.Slot(int)
    def switch_page(self, index: int):
        if index < 0 or index >= self.pageStack.count():
            return
        self.pageStack.setCurrentIndex(index)

    @QtCore.Slot(str, int)
    def _show_status_message(self, message: str, timeout: int = 0):
        self.statusBar().showMessage(message, timeout)

    def _open_new_template_dialog(self):
        self.switch_page(1)
        self.templatePage.open_create_dialog()

    @QtCore.Slot(object)
    def _save_settings_from_page(self, app_settings: AppSettings):
        current_settings = self.settingsService.load()
        request = AppSettings(
            tushare_token=app_settings.tushare_token,
            min_visible_days=app_settings.min_visible_days,
            max_visible_days=app_settings.max_visible_days,
            last_selected_symbol=current_settings.last_selected_symbol,
        )
        saved_settings = self.settingsService.save(request)
        self.settingsPage.set_settings(saved_settings)
        self.marketPage.apply_settings(saved_settings)
        self._show_status_message("设置已保存", 3000)

    @QtCore.Slot()
    def _request_update_all(self):
        token = self.settingsService.get_tushare_token()
        self.marketPage.start_update_all(token=token)

    @QtCore.Slot(bool)
    def _on_update_running_changed(self, running: bool):
        self.updateAllAction.setEnabled(not running)
        self.settingsPage.set_update_enabled(not running)

    def _show_about_dialog(self):
        QtWidgets.QMessageBox.information(self, "关于", "StockViewer 页面布局重构阶段 2")

    def closeEvent(self, event):
        self.marketPage.persist_page_state()
        super().closeEvent(event)
