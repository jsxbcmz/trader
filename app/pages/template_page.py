from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from core.models.template import ScreeningTemplate
from core.templates import TemplateService

from ..dialogs import TemplateEditorDialog


class TemplatePage(QtWidgets.QWidget):
    statusMessageRequested = QtCore.Signal(str, int)
    templatesChanged = QtCore.Signal()

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self.template_service = TemplateService.from_root(root)
        self._templates: list[ScreeningTemplate] = []

        self._setup_ui()
        self._connect_signals()
        self.refresh_templates()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        action_layout = QtWidgets.QHBoxLayout()
        self.newBtn = QtWidgets.QPushButton("新建")
        self.editBtn = QtWidgets.QPushButton("编辑")
        self.duplicateBtn = QtWidgets.QPushButton("复制")
        self.deleteBtn = QtWidgets.QPushButton("删除")
        self.refreshBtn = QtWidgets.QPushButton("刷新")
        action_layout.addWidget(self.newBtn)
        action_layout.addWidget(self.editBtn)
        action_layout.addWidget(self.duplicateBtn)
        action_layout.addWidget(self.deleteBtn)
        action_layout.addStretch(1)
        action_layout.addWidget(self.refreshBtn)
        layout.addLayout(action_layout)

        splitter = QtWidgets.QSplitter()

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["名称", "更新时间"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        detail_widget = QtWidgets.QWidget()
        detail_layout = QtWidgets.QFormLayout(detail_widget)
        self.nameLabel = QtWidgets.QLabel("-")
        self.descriptionLabel = QtWidgets.QLabel("-")
        self.descriptionLabel.setWordWrap(True)
        self.tdxSourceEdit = QtWidgets.QPlainTextEdit()
        self.tdxSourceEdit.setReadOnly(True)
        detail_layout.addRow("名称", self.nameLabel)
        detail_layout.addRow("描述", self.descriptionLabel)
        detail_layout.addRow("条件代码", self.tdxSourceEdit)

        splitter.addWidget(self.table)
        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter, 1)
        self._update_action_states(None)

    def _connect_signals(self):
        self.newBtn.clicked.connect(self.open_create_dialog)
        self.editBtn.clicked.connect(self.open_edit_dialog)
        self.duplicateBtn.clicked.connect(self.duplicate_selected_template)
        self.deleteBtn.clicked.connect(self.delete_selected_template)
        self.refreshBtn.clicked.connect(self.refresh_templates)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_edit_dialog())

    def _show_status(self, message: str, timeout: int = 3000):
        self.statusMessageRequested.emit(message, timeout)

    def refresh_templates(self, selected_id: str | None = None):
        self._templates = self.template_service.list_templates()
        self.table.setRowCount(len(self._templates))
        selected_row = -1
        for row, template in enumerate(self._templates):
            values = [
                template.name,
                template.updated_at or template.created_at or "-",
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value or ""))
                if col == 0:
                    item.setData(QtCore.Qt.UserRole, template.id)
                self.table.setItem(row, col, item)
            if selected_id and template.id == selected_id:
                selected_row = row
        self.table.resizeColumnsToContents()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self._templates:
            self.table.selectRow(0)
        else:
            self._render_template_detail(None)
            self._update_action_states(None)

    def get_selected_template(self) -> ScreeningTemplate | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._templates):
            return None
        return self._templates[row]

    def open_create_dialog(self):
        dialog = TemplateEditorDialog(parent=self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        data = dialog.get_template_data()
        try:
            template = self.template_service.create_template(**data)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "新建失败", str(exc))
            return
        self.refresh_templates(selected_id=template.id)
        self.templatesChanged.emit()
        self._show_status(f"已新建模板：{template.name}")

    def open_edit_dialog(self):
        template = self.get_selected_template()
        if template is None:
            return
        dialog = TemplateEditorDialog(template=template, parent=self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        data = dialog.get_template_data()
        try:
            updated = self.template_service.update_template(template.id, **data)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "编辑失败", str(exc))
            return
        self.refresh_templates(selected_id=updated.id)
        self.templatesChanged.emit()
        self._show_status(f"已更新模板：{updated.name}")

    def duplicate_selected_template(self):
        template = self.get_selected_template()
        if template is None:
            return
        try:
            duplicated = self.template_service.duplicate_template(template.id)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "复制失败", str(exc))
            return
        self.refresh_templates(selected_id=duplicated.id)
        self.templatesChanged.emit()
        self._show_status(f"已复制模板：{duplicated.name}")

    def delete_selected_template(self):
        template = self.get_selected_template()
        if template is None:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认删除",
            f"确定删除模板“{template.name}”吗？删除后无法恢复。",
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.template_service.delete_template(template.id)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.refresh_templates()
        self.templatesChanged.emit()
        self._show_status(f"已删除模板：{template.name}")

    def _on_selection_changed(self):
        template = self.get_selected_template()
        self._render_template_detail(template)
        self._update_action_states(template)

    def _render_template_detail(self, template: ScreeningTemplate | None):
        if template is None:
            self.nameLabel.setText("-")
            self.descriptionLabel.setText("-")
            self.tdxSourceEdit.setPlainText("")
            return
        self.nameLabel.setText(template.name)
        self.descriptionLabel.setText(template.description or "-")
        self.tdxSourceEdit.setPlainText(template.tdx_source or "")

    def _make_separator(self) -> QtWidgets.QFrame:
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        return sep

    def _update_action_states(self, template: ScreeningTemplate | None):
        has_template = template is not None
        self.editBtn.setEnabled(has_template)
        self.deleteBtn.setEnabled(has_template)
        self.duplicateBtn.setEnabled(has_template)

