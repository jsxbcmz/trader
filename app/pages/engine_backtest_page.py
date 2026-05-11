"""策略回测引擎页面 — 可视化展示回测引擎能力。

支持选择策略、股票、回测参数，运行回测并展示净值曲线、交易统计等结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PySide6 import QtCore, QtGui, QtWidgets


class BacktestWorker(QtCore.QObject):
    """在子线程中运行回测"""

    finished = QtCore.Signal(object)
    error = QtCore.Signal(str)

    def __init__(self, root: Path, symbol: str, strategy_id: str, config_params: dict):
        super().__init__()
        self.root = root
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.config_params = config_params

    def run(self):
        try:
            from core.backtest.config import BacktestConfig
            from core.backtest.engine import BacktestEngine
            from core.data.repository import StockRepository
            from core.strategy.builtin.b1_strategy import B1Strategy
            from core.strategy.builtin.brick_pattern_strategy import BrickPatternStrategy

            config = BacktestConfig(
                initial_capital=self.config_params.get("capital", 100000),
                commission_rate=self.config_params.get("commission", 0.00025),
                slippage_rate=self.config_params.get("slippage", 0.001),
            )

            engine = BacktestEngine(config)

            if self.strategy_id == "B1":
                engine.add_strategy(B1Strategy())
            elif self.strategy_id == "BRICK":
                engine.add_strategy(BrickPatternStrategy(min_grade="B"))
            else:
                self.error.emit(f"未知策略: {self.strategy_id}")
                return

            repo = StockRepository(self.root)
            data = repo.get_daily_frame(self.symbol)
            if data is None or len(data) < 60:
                self.error.emit(f"股票 {self.symbol} 数据不足")
                return

            result = engine.run(self.symbol, data)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class EquityCurveWidget(QtWidgets.QWidget):
    """净值曲线图（使用 QPainter 绘制，避免额外依赖）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._equity_data: list[float] = []
        self._dates: list[str] = []
        self._initial_capital = 100000.0

    def set_data(self, equity_series: pd.Series, initial_capital: float):
        self._equity_data = (equity_series.values / initial_capital).tolist()
        self._dates = [str(d)[:10] for d in equity_series.index]
        self._initial_capital = initial_capital
        self.update()

    def clear_data(self):
        self._equity_data = []
        self._dates = []
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(50, 20, -20, -30)

        # 背景
        painter.fillRect(self.rect(), QtGui.QColor("#FAFAFA"))

        if len(self._equity_data) < 2:
            painter.setPen(QtGui.QColor("#999"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "运行回测后显示净值曲线")
            painter.end()
            return

        data = self._equity_data
        min_val = min(data)
        max_val = max(data)
        val_range = max_val - min_val
        if val_range < 1e-9:
            val_range = 1.0

        # 网格
        painter.setPen(QtGui.QPen(QtGui.QColor("#E8E8E8"), 1))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            val = max_val - val_range * i / 4
            painter.setPen(QtGui.QColor("#999"))
            painter.drawText(int(rect.left() - 48), int(y - 6), 45, 12,
                             QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                             f"{val:.3f}")
            painter.setPen(QtGui.QPen(QtGui.QColor("#E8E8E8"), 1))

        # 基准线 (1.0)
        if min_val <= 1.0 <= max_val:
            baseline_y = rect.top() + rect.height() * (max_val - 1.0) / val_range
            painter.setPen(QtGui.QPen(QtGui.QColor("#BDBDBD"), 1, QtCore.Qt.DashLine))
            painter.drawLine(int(rect.left()), int(baseline_y), int(rect.right()), int(baseline_y))

        # 净值曲线
        points = []
        for i, val in enumerate(data):
            x = rect.left() + rect.width() * i / (len(data) - 1)
            y = rect.top() + rect.height() * (max_val - val) / val_range
            points.append(QtCore.QPointF(x, y))

        # 填充渐变
        final_val = data[-1]
        color = QtGui.QColor("#4CAF50") if final_val >= 1.0 else QtGui.QColor("#F44336")
        fill_color = QtGui.QColor(color)
        fill_color.setAlpha(30)

        fill_path = QtGui.QPainterPath()
        fill_path.moveTo(points[0].x(), rect.bottom())
        for pt in points:
            fill_path.lineTo(pt)
        fill_path.lineTo(points[-1].x(), rect.bottom())
        fill_path.closeSubpath()
        painter.fillPath(fill_path, fill_color)

        # 曲线
        pen = QtGui.QPen(color, 2)
        painter.setPen(pen)
        path = QtGui.QPainterPath()
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        painter.drawPath(path)

        # 日期标签
        painter.setPen(QtGui.QColor("#999"))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        label_count = min(5, len(self._dates))
        for i in range(label_count):
            idx = int(i * (len(self._dates) - 1) / max(1, label_count - 1))
            x = rect.left() + rect.width() * idx / (len(data) - 1)
            painter.drawText(int(x - 30), int(rect.bottom() + 5), 60, 20,
                             QtCore.Qt.AlignCenter, self._dates[idx])

        painter.end()


class EngineBacktestPage(QtWidgets.QWidget):
    """策略回测引擎页面"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self._worker: BacktestWorker | None = None
        self._thread: QtCore.QThread | None = None
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # 标题
        title = QtWidgets.QLabel("策略回测引擎")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        main_layout.addWidget(title)

        subtitle = QtWidgets.QLabel("选择策略和股票，运行事件驱动回测，查看净值曲线与交易统计")
        subtitle.setStyleSheet("color: #666; margin-bottom: 8px;")
        main_layout.addWidget(subtitle)

        # 参数区
        params_group = QtWidgets.QGroupBox("回测参数")
        params_layout = QtWidgets.QGridLayout(params_group)
        params_layout.setSpacing(8)

        params_layout.addWidget(QtWidgets.QLabel("策略:"), 0, 0)
        self._strategy_combo = QtWidgets.QComboBox()
        self._strategy_combo.addItem("B1 量价共振", "B1")
        self._strategy_combo.addItem("砖形图定式", "BRICK")
        self._strategy_combo.setFixedWidth(200)
        params_layout.addWidget(self._strategy_combo, 0, 1)

        params_layout.addWidget(QtWidgets.QLabel("股票代码:"), 0, 2)
        self._symbol_input = QtWidgets.QLineEdit("000001")
        self._symbol_input.setFixedWidth(100)
        self._symbol_input.setPlaceholderText("6位代码")
        params_layout.addWidget(self._symbol_input, 0, 3)

        params_layout.addWidget(QtWidgets.QLabel("初始资金:"), 1, 0)
        self._capital_input = QtWidgets.QSpinBox()
        self._capital_input.setRange(10000, 10000000)
        self._capital_input.setValue(100000)
        self._capital_input.setSingleStep(10000)
        self._capital_input.setSuffix(" 元")
        self._capital_input.setFixedWidth(150)
        params_layout.addWidget(self._capital_input, 1, 1)

        params_layout.addWidget(QtWidgets.QLabel("佣金费率:"), 1, 2)
        self._commission_input = QtWidgets.QDoubleSpinBox()
        self._commission_input.setRange(0.0001, 0.003)
        self._commission_input.setValue(0.00025)
        self._commission_input.setDecimals(5)
        self._commission_input.setSingleStep(0.00005)
        self._commission_input.setFixedWidth(120)
        params_layout.addWidget(self._commission_input, 1, 3)

        params_layout.addWidget(QtWidgets.QLabel("滑点费率:"), 1, 4)
        self._slippage_input = QtWidgets.QDoubleSpinBox()
        self._slippage_input.setRange(0.0, 0.01)
        self._slippage_input.setValue(0.001)
        self._slippage_input.setDecimals(4)
        self._slippage_input.setSingleStep(0.0005)
        self._slippage_input.setFixedWidth(120)
        params_layout.addWidget(self._slippage_input, 1, 5)

        self._run_btn = QtWidgets.QPushButton("▶ 运行回测")
        self._run_btn.setFixedWidth(120)
        self._run_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; border-radius: 4px; "
            "padding: 6px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #BDBDBD; }"
        )
        params_layout.addWidget(self._run_btn, 0, 4, 1, 2)

        main_layout.addWidget(params_group)

        # 结果区 splitter
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        # 上部：净值曲线
        curve_group = QtWidgets.QGroupBox("净值曲线")
        curve_layout = QtWidgets.QVBoxLayout(curve_group)
        self._equity_widget = EquityCurveWidget()
        curve_layout.addWidget(self._equity_widget)
        splitter.addWidget(curve_group)

        # 下部：统计卡片 + 交易明细
        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)

        # 统计卡片
        stats_group = QtWidgets.QGroupBox("绩效指标")
        stats_layout = QtWidgets.QGridLayout(stats_group)
        stats_layout.setSpacing(6)
        self._stat_labels: dict[str, QtWidgets.QLabel] = {}
        stat_items = [
            ("total_return", "总收益率"),
            ("annualized_return", "年化收益"),
            ("max_drawdown", "最大回撤"),
            ("win_rate", "胜率"),
            ("profit_loss_ratio", "盈亏比"),
            ("sharpe_ratio", "夏普比率"),
            ("calmar_ratio", "卡玛比率"),
            ("total_trades", "交易次数"),
            ("avg_holding_days", "平均持仓天数"),
            ("final_capital", "最终资金"),
        ]
        for idx, (key, label_text) in enumerate(stat_items):
            row, col = idx // 2, (idx % 2) * 2
            name_label = QtWidgets.QLabel(f"{label_text}:")
            name_label.setStyleSheet("color: #555; font-size: 12px;")
            val_label = QtWidgets.QLabel("--")
            val_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            stats_layout.addWidget(name_label, row, col)
            stats_layout.addWidget(val_label, row, col + 1)
            self._stat_labels[key] = val_label

        stats_group.setFixedWidth(380)
        bottom_layout.addWidget(stats_group)

        # 交易明细表
        trades_group = QtWidgets.QGroupBox("交易明细")
        trades_layout = QtWidgets.QVBoxLayout(trades_group)
        self._trades_table = QtWidgets.QTableWidget()
        self._trades_table.setColumnCount(6)
        self._trades_table.setHorizontalHeaderLabels(
            ["方向", "价格", "数量", "佣金", "印花税", "原因"]
        )
        self._trades_table.horizontalHeader().setStretchLastSection(True)
        self._trades_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._trades_table.setAlternatingRowColors(True)
        self._trades_table.verticalHeader().setDefaultSectionSize(22)
        trades_layout.addWidget(self._trades_table)
        bottom_layout.addWidget(trades_group, 1)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter, 1)

        # 状态栏
        self._status_label = QtWidgets.QLabel("就绪")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        main_layout.addWidget(self._status_label)

    def _connect_signals(self):
        self._run_btn.clicked.connect(self._on_run_clicked)

    def _on_run_clicked(self):
        symbol = self._symbol_input.text().strip().zfill(6)
        strategy_id = self._strategy_combo.currentData()

        if not symbol or len(symbol) != 6:
            self.statusMessageRequested.emit("请输入6位股票代码", 3000)
            return

        self._run_btn.setEnabled(False)
        self._status_label.setText(f"正在回测 {symbol} ...")
        self._status_label.setStyleSheet("color: #1976D2; font-size: 11px;")

        config_params = {
            "capital": self._capital_input.value(),
            "commission": self._commission_input.value(),
            "slippage": self._slippage_input.value(),
        }

        self._thread = QtCore.QThread()
        self._worker = BacktestWorker(self.root, symbol, strategy_id, config_params)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_backtest_done)
        self._worker.error.connect(self._on_backtest_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_backtest_done(self, result):
        self._run_btn.setEnabled(True)
        self._display_result(result)
        self._status_label.setText(
            f"回测完成 | {result.symbol} | {result.start_date} ~ {result.end_date}"
        )
        self._status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
        self.statusMessageRequested.emit("回测完成", 3000)

    def _on_backtest_error(self, error_msg: str):
        self._run_btn.setEnabled(True)
        self._status_label.setText(f"回测失败: {error_msg}")
        self._status_label.setStyleSheet("color: #F44336; font-size: 11px;")
        self.statusMessageRequested.emit(f"回测失败: {error_msg}", 5000)

    def _display_result(self, result):
        """展示回测结果"""
        # 净值曲线
        if len(result.equity_curve) > 1:
            self._equity_widget.set_data(result.equity_curve, result.initial_capital)
        else:
            self._equity_widget.clear_data()

        # 统计指标
        self._update_stat("total_return", f"{result.total_return:+.2%}",
                          "#4CAF50" if result.total_return >= 0 else "#F44336")
        self._update_stat("annualized_return", f"{result.annualized_return:+.2%}",
                          "#4CAF50" if result.annualized_return >= 0 else "#F44336")
        self._update_stat("max_drawdown", f"{result.max_drawdown:.2%}", "#F44336")
        self._update_stat("win_rate", f"{result.win_rate:.1%}",
                          "#4CAF50" if result.win_rate >= 0.5 else "#FF9800")
        self._update_stat("profit_loss_ratio", f"{result.profit_loss_ratio:.2f}",
                          "#4CAF50" if result.profit_loss_ratio >= 1.5 else "#FF9800")
        self._update_stat("sharpe_ratio", f"{result.sharpe_ratio:.2f}",
                          "#4CAF50" if result.sharpe_ratio >= 1.0 else "#FF9800")
        self._update_stat("calmar_ratio", f"{result.calmar_ratio:.2f}",
                          "#4CAF50" if result.calmar_ratio >= 1.0 else "#FF9800")
        self._update_stat("total_trades", str(result.total_trades), "#333")
        self._update_stat("avg_holding_days", f"{result.avg_holding_days:.1f}", "#333")
        self._update_stat("final_capital", f"¥{result.final_capital:,.0f}",
                          "#4CAF50" if result.final_capital >= result.initial_capital else "#F44336")

        # 交易明细
        self._populate_trades(result.trade_log)

    def _update_stat(self, key: str, text: str, color: str = "#333"):
        label = self._stat_labels.get(key)
        if label:
            label.setText(text)
            label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color};")

    def _populate_trades(self, trade_log: list):
        self._trades_table.setRowCount(0)
        if not trade_log:
            return

        self._trades_table.setRowCount(len(trade_log))
        for row_idx, fill in enumerate(trade_log):
            direction = fill.order.direction.value
            dir_item = QtWidgets.QTableWidgetItem(direction)
            dir_item.setForeground(
                QtGui.QColor("#D32F2F") if direction == "BUY" else QtGui.QColor("#388E3C")
            )
            self._trades_table.setItem(row_idx, 0, dir_item)
            self._trades_table.setItem(row_idx, 1,
                                       QtWidgets.QTableWidgetItem(f"{fill.fill_price:.2f}"))
            self._trades_table.setItem(row_idx, 2,
                                       QtWidgets.QTableWidgetItem(str(fill.fill_quantity)))
            self._trades_table.setItem(row_idx, 3,
                                       QtWidgets.QTableWidgetItem(f"{fill.commission:.2f}"))
            self._trades_table.setItem(row_idx, 4,
                                       QtWidgets.QTableWidgetItem(f"{fill.stamp_tax:.2f}"))
            self._trades_table.setItem(row_idx, 5,
                                       QtWidgets.QTableWidgetItem(fill.order.reason))
