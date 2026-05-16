"""通用进度对话框：数据更新进度 + 选股进度。

从原 app/widgets.py 抽离。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class UpdateProgressDialog(QtWidgets.QDialog):
    cancelRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("更新进度")
        self.resize(560, 380)
        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始更新...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.currentLabel = QtWidgets.QLabel("当前股票：-")
        self.detailLabel = QtWidgets.QLabel("阶段：等待开始")
        self.statsLabel = QtWidgets.QLabel("成功: 0  跳过: 0  失败: 0")
        self.logEdit = QtWidgets.QPlainTextEdit()
        self.logEdit.setReadOnly(True)
        self.cancelButton = QtWidgets.QPushButton("取消")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.addStretch(1)
        btnLayout.addWidget(self.cancelButton)
        btnLayout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.detailLabel)
        layout.addWidget(self.statsLabel)
        layout.addWidget(self.logEdit, 1)
        layout.addLayout(btnLayout)

        self.cancelButton.clicked.connect(self.cancelRequested.emit)
        self.closeButton.clicked.connect(self.accept)

    def update_progress(self, payload: dict):
        current = int(payload.get("current", 0) or 0)
        total = max(int(payload.get("total", 0) or 0), 1)
        symbol = payload.get("symbol", "")
        name = payload.get("name", "")
        stage = payload.get("stage", "")
        message = payload.get("message", "")
        success = int(payload.get("success", 0) or 0)
        skipped = int(payload.get("skipped", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        phase_text = str(payload.get("phase_text", "") or "").strip()
        stage_text = str(payload.get("stage_text", "") or "").strip() or stage or "处理中"

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))
        self.progressLabel.setText(f"进度：{current} / {total}")
        self.currentLabel.setText(f"当前股票：{symbol or '-'} {name}".rstrip())
        self.detailLabel.setText(f"阶段：{stage_text}")
        self.statsLabel.setText(f"成功: {success}  跳过: {skipped}  失败: {failed}")
        if symbol or message or phase_text:
            line = f"[{current}/{total}] {symbol} {name}"
            if phase_text:
                line += f" | {phase_text}"
            else:
                line += f" | {stage_text}"
            if message:
                line += f" - {message}"
            self.logEdit.appendPlainText(line.strip())

    def mark_finished(self):
        self.cancelButton.setEnabled(False)
        self.closeButton.setEnabled(True)

    def mark_cancel_requested(self):
        self.cancelButton.setEnabled(False)
        self.detailLabel.setText("阶段：正在请求取消，请稍候...")
        self.logEdit.appendPlainText("已请求取消，等待当前股票处理结束...")


class ScreeningProgressDialog(QtWidgets.QDialog):
    """选股进度弹窗"""

    stopRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选股进度")
        self.resize(480, 320)
        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始选股...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.currentLabel = QtWidgets.QLabel("当前股票：-")
        self.statsLabel = QtWidgets.QLabel("已处理: 0  命中: 0  错误: 0")

        self.stopButton = QtWidgets.QPushButton("停止")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.addStretch(1)
        btnLayout.addWidget(self.stopButton)
        btnLayout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.statsLabel)
        layout.addStretch(1)
        layout.addLayout(btnLayout)

        self.stopButton.clicked.connect(self._on_stop_clicked)
        self.closeButton.clicked.connect(self.accept)

    def _on_stop_clicked(self):
        self.stopButton.setEnabled(False)
        self.progressLabel.setText("正在停止，请稍候...")
        self.stopRequested.emit()

    def update_progress(self, payload: dict):
        current = int(payload.get("current", 0) or 0)
        total = max(int(payload.get("total", 0) or 0), 1)
        symbol = payload.get("symbol", "")
        matched = int(payload.get("matched", 0) or 0)
        errors = int(payload.get("errors", 0) or 0)

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))
        self.progressLabel.setText(f"选股进度：{current} / {total}")
        self.currentLabel.setText(f"当前股票：{symbol or '-'}")
        self.statsLabel.setText(f"已处理: {current}  命中: {matched}  错误: {errors}")

    def mark_finished(self, summary: str = ""):
        self.stopButton.setEnabled(False)
        self.closeButton.setEnabled(True)
        if summary:
            self.progressLabel.setText(summary)

