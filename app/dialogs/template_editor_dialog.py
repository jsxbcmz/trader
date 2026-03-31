from __future__ import annotations

import json

from PySide6 import QtWidgets

from core.data.time_index import TIME_MODE_EXACT, TIME_MODE_ON_OR_BEFORE
from core.expression.builder import build_expression
from core.expression.validator import validate_expression
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
        self.resize(640, 520)

        layout = QtWidgets.QVBoxLayout(self)

        form_group = QtWidgets.QGroupBox("模板信息")
        form_layout = QtWidgets.QFormLayout(form_group)
        self.nameEdit = QtWidgets.QLineEdit(template.name if template else "")
        self.descriptionEdit = QtWidgets.QPlainTextEdit(template.description if template else "")
        self.descriptionEdit.setMaximumHeight(100)
        self.timeModeBox = QtWidgets.QComboBox()
        self.timeModeBox.addItems([TIME_MODE_EXACT, TIME_MODE_ON_OR_BEFORE])
        if template:
            index = self.timeModeBox.findText(template.default_time_mode)
            if index >= 0:
                self.timeModeBox.setCurrentIndex(index)
        self.stockPoolEdit = QtWidgets.QLineEdit(template.stock_pool_name if template else "default")
        self.includeDebugCheck = QtWidgets.QCheckBox("执行时包含调试信息")
        self.includeDebugCheck.setChecked(template.include_debug if template else False)
        form_layout.addRow("名称", self.nameEdit)
        form_layout.addRow("描述", self.descriptionEdit)
        form_layout.addRow("默认时间模式", self.timeModeBox)
        form_layout.addRow("股票池名称", self.stockPoolEdit)
        form_layout.addRow("", self.includeDebugCheck)

        condition_group = QtWidgets.QGroupBox("条件 JSON")
        condition_layout = QtWidgets.QVBoxLayout(condition_group)
        self.conditionEdit = QtWidgets.QPlainTextEdit()
        self.conditionEdit.setPlaceholderText("请输入结构化条件 JSON")
        if template and template.condition:
            self.conditionEdit.setPlainText(json.dumps(template.condition, ensure_ascii=False, indent=2))
        condition_layout.addWidget(self.conditionEdit)

        self.buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttonBox.accepted.connect(self._validate_and_accept)
        self.buttonBox.rejected.connect(self.reject)

        layout.addWidget(form_group)
        layout.addWidget(condition_group, 1)
        layout.addWidget(self.buttonBox)

    def _validate_and_accept(self):
        name = self.nameEdit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "模板无效", "模板名称不能为空。")
            self.nameEdit.setFocus()
            return

        raw_condition = self.conditionEdit.toPlainText().strip()
        if not raw_condition:
            QtWidgets.QMessageBox.warning(self, "模板无效", "条件 JSON 不能为空。")
            self.conditionEdit.setFocus()
            return

        try:
            condition = json.loads(raw_condition)
        except json.JSONDecodeError as exc:
            QtWidgets.QMessageBox.warning(self, "模板无效", f"条件 JSON 格式错误：{exc}")
            self.conditionEdit.setFocus()
            return

        if not isinstance(condition, dict):
            QtWidgets.QMessageBox.warning(self, "模板无效", "条件 JSON 顶层必须是对象。")
            self.conditionEdit.setFocus()
            return

        try:
            expression = build_expression(condition)
            validate_expression(expression)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "模板无效", f"条件表达式校验失败：{exc}")
            self.conditionEdit.setFocus()
            return

        self.accept()

    def get_template_data(self) -> dict:
        condition = json.loads(self.conditionEdit.toPlainText().strip())
        return {
            "name": self.nameEdit.text().strip(),
            "description": self.descriptionEdit.toPlainText().strip(),
            "condition": condition,
            "default_time_mode": self.timeModeBox.currentText().strip() or TIME_MODE_EXACT,
            "stock_pool_name": self.stockPoolEdit.text().strip() or "default",
            "include_debug": self.includeDebugCheck.isChecked(),
        }
