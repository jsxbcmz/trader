from __future__ import annotations

from PySide6 import QtWidgets

from core.models.template import ScreeningTemplate


class TemplateEditorDialog(QtWidgets.QDialog):
    def __init__(
        self,
        template: ScreeningTemplate | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._template = template
        self.setWindowTitle("编辑模板" if self._template is not None else "新建模板")
        self.resize(640, 480)

        layout = QtWidgets.QVBoxLayout(self)

        form_group = QtWidgets.QGroupBox("模板信息")
        form_layout = QtWidgets.QFormLayout(form_group)
        self.nameEdit = QtWidgets.QLineEdit(template.name if template else "")
        self.descriptionEdit = QtWidgets.QPlainTextEdit(template.description if template else "")
        self.descriptionEdit.setMaximumHeight(100)
        self.stockPoolEdit = QtWidgets.QLineEdit(template.stock_pool_name if template else "default")
        self.includeDebugCheck = QtWidgets.QCheckBox("执行时包含调试信息")
        self.includeDebugCheck.setChecked(template.include_debug if template else False)
        form_layout.addRow("名称", self.nameEdit)
        form_layout.addRow("描述", self.descriptionEdit)
        form_layout.addRow("股票池名称", self.stockPoolEdit)
        form_layout.addRow("", self.includeDebugCheck)

        tdx_group = QtWidgets.QGroupBox("通达信条件代码")
        tdx_layout = QtWidgets.QVBoxLayout(tdx_group)
        self.tdxSourceEdit = QtWidgets.QPlainTextEdit()
        self.tdxSourceEdit.setPlaceholderText("请输入通达信选股条件代码")
        self.tdxSourceEdit.setFont(QtWidgets.QApplication.font())  # 使用等宽字体更好
        if template and template.tdx_source:
            self.tdxSourceEdit.setPlainText(template.tdx_source)
        tdx_layout.addWidget(self.tdxSourceEdit)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttonBox.accepted.connect(self._validate_and_accept)
        self.buttonBox.rejected.connect(self.reject)

        layout.addWidget(form_group)
        layout.addWidget(tdx_group, 1)
        layout.addWidget(self.buttonBox)

    def _validate_and_accept(self):
        name = self.nameEdit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "模板无效", "模板名称不能为空。")
            self.nameEdit.setFocus()
            return

        tdx_source = self.tdxSourceEdit.toPlainText().strip()
        if not tdx_source:
            QtWidgets.QMessageBox.warning(self, "模板无效", "通达信条件代码不能为空。")
            self.tdxSourceEdit.setFocus()
            return

        self.accept()

    def get_template_data(self) -> dict:
        return {
            "name": self.nameEdit.text().strip(),
            "description": self.descriptionEdit.toPlainText().strip(),
            "tdx_source": self.tdxSourceEdit.toPlainText().strip(),
            "stock_pool_name": self.stockPoolEdit.text().strip() or "default",
            "include_debug": self.includeDebugCheck.isChecked(),
        }
