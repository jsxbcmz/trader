"""选股页模拟交易控制器：交易面板 UI + T+1/资金/标记/结算等编排逻辑。

抽离自 ``screening_page.py``，让选股页只负责"选股+图表"骨架，所有
模拟交易相关字段、按钮、槽函数都收敛到本控制器中。

控制器持有：
- ``TradeSimulator`` 引擎
- 资金账户（initial_capital / available_capital）
- 模拟日期与开盘/收盘阶段（current_sim_date / is_at_open）
- 交易面板上所有 widget（setup_widget、ops_widget、各按钮/标签/输入框）
- 图上 B/S 标记列表（trade_marker_items）

通过 ``host`` 反向引用访问选股页：
- ``host.chart``、``host.holding_table``
- ``host._current_symbol`` / ``_current_stock_name`` / ``_current_df``
- ``host._load_chart_for_current_symbol()`` / ``host.statusMessageRequested``
- ``host.stock_daily_data_dir``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from core.models.trade import TradeAction
from core.trade.simulator import TradeSimulator

from ..data_loader import load_daily_csv

if TYPE_CHECKING:
    from .screening_page import ScreeningPage


class ScreeningTradeController(QtCore.QObject):
    """选股页模拟交易编排控制器。

    生命周期：
    1. 与 ``ScreeningPage`` 一同创建（``__init__``）。
    2. ``build_panel()`` 由 page 在装配右侧操作面板时调用，返回完整面板 widget。
    3. 选股完成后 page 调用 ``start_session(target_date)`` 重置交易状态。
    4. page 加载图表后调用 ``on_chart_loaded()`` 让控制器刷新按钮可用性、标记。
    5. ``has_holdings`` / ``is_today_bought`` 供 page 在确认对话框/列表渲染时查询。
    """

    def __init__(self, host: "ScreeningPage") -> None:
        super().__init__(host)
        self.host = host

        self.simulator = TradeSimulator()
        self.initial_capital: int = 100_000
        self.available_capital: float = 100_000.0
        self.current_sim_date: str = ""
        self.is_at_open: bool = False
        self._trade_marker_items: list = []
        # 注：trade_setup_widget / trade_ops_widget / 各按钮、标签、输入框
        # 均在 build_panel() 中创建。此处不预声明，与 ScreeningPage 原风格一致。

    # ── 公共查询 ─────────────────────────────────────────────

    @property
    def has_holdings(self) -> bool:
        return bool(self.simulator.holdings)

    def is_today_bought(self, symbol: str) -> bool:
        """判断该股票是否在当天有买入记录（T+1 限制）"""
        return any(
            r.symbol == symbol
            and r.action == TradeAction.BUY
            and r.trade_date == self.current_sim_date
            for r in self.simulator.trade_records
        )

    # ── UI 构建 ──────────────────────────────────────────────

    def build_panel(self) -> QtWidgets.QWidget:
        """构建右侧操作面板：模拟交易控制区。"""
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(160)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QtWidgets.QLabel("📅 模拟交易")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        layout.addWidget(self._make_separator())

        # ── 初始配置区：金额输入 + 开始训练按钮 ──
        self.trade_setup_widget = QtWidgets.QWidget()
        setup_layout = QtWidgets.QVBoxLayout(self.trade_setup_widget)
        setup_layout.setContentsMargins(0, 0, 0, 0)

        setup_layout.addWidget(QtWidgets.QLabel("初始资金"))
        self.initial_capital_input = QtWidgets.QSpinBox()
        self.initial_capital_input.setRange(10_000, 100_000_000)
        self.initial_capital_input.setSingleStep(10_000)
        self.initial_capital_input.setValue(100_000)
        self.initial_capital_input.setPrefix("¥ ")
        self.initial_capital_input.setGroupSeparatorShown(True)
        setup_layout.addWidget(self.initial_capital_input)

        setup_layout.addSpacing(12)

        self.start_training_btn = QtWidgets.QPushButton("🚀 开始训练")
        self.start_training_btn.setMinimumHeight(36)
        self.start_training_btn.setStyleSheet(
            "background-color: #1890FF; color: white; font-weight: bold; font-size: 13px;"
        )
        self.start_training_btn.clicked.connect(self._on_start_training)
        setup_layout.addWidget(self.start_training_btn)

        layout.addWidget(self.trade_setup_widget)

        # ── 交易操作区：选股后点击"开始训练"才显示 ──
        self.trade_ops_widget = QtWidgets.QWidget()
        ops_layout = QtWidgets.QVBoxLayout(self.trade_ops_widget)
        ops_layout.setContentsMargins(0, 0, 0, 0)

        ops_layout.addWidget(QtWidgets.QLabel("可用资金"))
        self.available_capital_label = QtWidgets.QLabel("¥ 100,000")
        self.available_capital_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #1890FF;"
        )
        ops_layout.addWidget(self.available_capital_label)

        ops_layout.addWidget(self._make_separator())

        ops_layout.addWidget(QtWidgets.QLabel("当前日期"))
        self.sim_date_label = QtWidgets.QLabel("--")
        self.sim_date_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        ops_layout.addWidget(self.sim_date_label)

        ops_layout.addWidget(QtWidgets.QLabel("当前价格"))
        self.sim_price_label = QtWidgets.QLabel("--")
        self.sim_price_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        ops_layout.addWidget(self.sim_price_label)

        ops_layout.addSpacing(8)

        self.next_day_btn = QtWidgets.QPushButton("▶ 下一天")
        self.next_day_btn.setEnabled(False)
        self.next_day_btn.clicked.connect(self._on_advance_day)
        ops_layout.addWidget(self.next_day_btn)

        ops_layout.addWidget(self._make_separator())

        ops_layout.addWidget(QtWidgets.QLabel("买入数量(股)"))
        self.buy_quantity_input = QtWidgets.QSpinBox()
        self.buy_quantity_input.setRange(100, 1_000_000)
        self.buy_quantity_input.setSingleStep(100)
        self.buy_quantity_input.setValue(100)
        ops_layout.addWidget(self.buy_quantity_input)

        self.buy_open_btn = QtWidgets.QPushButton("🟢 开盘价买入")
        self.buy_open_btn.setStyleSheet(
            "background-color: #CC3333; color: white; font-weight: bold;"
        )
        self.buy_open_btn.setEnabled(False)
        self.buy_open_btn.clicked.connect(self._on_buy_open)
        ops_layout.addWidget(self.buy_open_btn)

        self.buy_close_btn = QtWidgets.QPushButton("🟢 收盘价买入")
        self.buy_close_btn.setStyleSheet(
            "background-color: #CC3333; color: white; font-weight: bold;"
        )
        self.buy_close_btn.setEnabled(False)
        self.buy_close_btn.clicked.connect(self._on_buy_close)
        ops_layout.addWidget(self.buy_close_btn)

        ops_layout.addWidget(self._make_separator())

        ops_layout.addWidget(QtWidgets.QLabel("卖出数量(股)"))
        self.sell_quantity_input = QtWidgets.QSpinBox()
        self.sell_quantity_input.setRange(100, 1_000_000)
        self.sell_quantity_input.setSingleStep(100)
        self.sell_quantity_input.setValue(100)
        ops_layout.addWidget(self.sell_quantity_input)

        self.sell_btn = QtWidgets.QPushButton("🔴 卖出")
        self.sell_btn.setStyleSheet(
            "background-color: #33AA33; color: white; font-weight: bold;"
        )
        self.sell_btn.setEnabled(False)
        self.sell_btn.clicked.connect(self._on_sell)
        ops_layout.addWidget(self.sell_btn)

        ops_layout.addWidget(self._make_separator())

        ops_layout.addWidget(QtWidgets.QLabel("总投入"))
        self.total_cost_label = QtWidgets.QLabel("¥ 0.00")
        ops_layout.addWidget(self.total_cost_label)

        ops_layout.addWidget(QtWidgets.QLabel("总市值"))
        self.total_value_label = QtWidgets.QLabel("¥ 0.00")
        ops_layout.addWidget(self.total_value_label)

        ops_layout.addWidget(QtWidgets.QLabel("总盈亏"))
        self.total_pnl_label = QtWidgets.QLabel("0.00%")
        ops_layout.addWidget(self.total_pnl_label)

        ops_layout.addSpacing(8)

        self.settle_btn = QtWidgets.QPushButton("💰 结算")
        self.settle_btn.clicked.connect(self._on_settle)
        ops_layout.addWidget(self.settle_btn)

        self.trade_ops_widget.setVisible(False)
        layout.addWidget(self.trade_ops_widget)

        layout.addStretch()
        return panel

    @staticmethod
    def _make_separator() -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    # ── 会话生命周期 ─────────────────────────────────────────

    def start_session(self, target_date: str) -> None:
        """选股完成后初始化交易会话状态。"""
        self.current_sim_date = target_date
        self.is_at_open = False
        self.simulator.reset()
        self._trade_marker_items.clear()
        self.host.holding_table.setRowCount(0)

    def reset_session(self, target_date: str) -> None:
        """重置到刚进入选股结果时的初始状态（_on_reset 用）。"""
        self.simulator.reset()
        self._clear_trade_markers()

        self.is_at_open = False
        self.current_sim_date = target_date
        self.available_capital = float(self.initial_capital)

        self.next_day_btn.setText("▶ 下一天")
        self.next_day_btn.setEnabled(False)
        self.host.holding_table.setRowCount(0)
        self.buy_open_btn.setEnabled(False)
        self.buy_close_btn.setEnabled(False)
        self.sell_btn.setEnabled(False)

        self.trade_setup_widget.setVisible(True)
        self.trade_ops_widget.setVisible(False)
        self.initial_capital_input.setValue(self.initial_capital)

        self.available_capital_label.setText(f"¥ {self.available_capital:,.2f}")
        self.total_cost_label.setText("¥ 0.00")
        self.total_value_label.setText("¥ 0.00")
        self.total_pnl_label.setText("0.00%")
        self.total_pnl_label.setStyleSheet("")
        self.sim_date_label.setText("--")
        self.sim_price_label.setText("--")

    def discard_session(self) -> None:
        """放弃当前会话（_on_back_to_config 用）。"""
        self.simulator.reset()
        self._clear_trade_markers()
        self.is_at_open = False
        self.current_sim_date = ""
        self.next_day_btn.setText("▶ 下一天")
        self.host.holding_table.setRowCount(0)
        self.trade_setup_widget.setVisible(True)
        self.trade_ops_widget.setVisible(False)

    # ── page 加载完图表后的回调 ───────────────────────────────

    def on_chart_loaded(self) -> None:
        """page 加载完图表后调用：刷新 B/S 标记 + 信息标签 + 按钮可用性。"""
        self._update_sim_info()
        self._redraw_trade_markers()

        self.next_day_btn.setEnabled(True)
        self.sell_btn.setEnabled(True)
        if self.is_at_open:
            self.buy_open_btn.setVisible(True)
            self.buy_open_btn.setEnabled(True)
            self.buy_close_btn.setVisible(False)
        else:
            self.buy_open_btn.setVisible(False)
            self.buy_close_btn.setVisible(True)
            self.buy_close_btn.setEnabled(True)

    # ── UI 槽函数 ────────────────────────────────────────────

    def _on_start_training(self) -> None:
        """点击开始训练：记录初始资金，切换到交易操作区。"""
        self.initial_capital = self.initial_capital_input.value()
        self.available_capital = float(self.initial_capital)
        self.available_capital_label.setText(f"¥ {self.available_capital:,.2f}")
        self.trade_setup_widget.setVisible(False)
        self.trade_ops_widget.setVisible(True)

    def _on_advance_day(self) -> None:
        """下一天/快进到收盘：根据当前阶段切换。"""
        if self.is_at_open:
            self._advance_to_close()
        else:
            self._advance_to_next_open()

    def _advance_to_next_open(self) -> None:
        """推进到下一个交易日的开盘阶段。"""
        host = self.host
        if not host._current_symbol or not self.current_sim_date:
            return

        try:
            df_full = load_daily_csv(
                host.stock_daily_data_dir, host._current_symbol
            )
        except FileNotFoundError:
            return

        current_mask = df_full["date"] <= self.current_sim_date
        current_count = current_mask.sum()
        if current_count >= len(df_full):
            self.next_day_btn.setEnabled(False)
            host.statusMessageRequested.emit("已无更多交易日数据", 3000)
            return

        next_row = df_full.iloc[current_count]
        next_date = next_row["date"]
        self.current_sim_date = (
            next_date.strftime("%Y-%m-%d")
            if hasattr(next_date, "strftime")
            else str(next_date)[:10]
        )

        self.is_at_open = True
        self.next_day_btn.setText("⏩ 快进到收盘")

        host._load_chart_for_current_symbol()
        self._update_holding_prices()
        self._refresh_trade_summary()

    def _advance_to_close(self) -> None:
        """快进到当天收盘，展示完整 K 线数据。"""
        self.is_at_open = False
        self.next_day_btn.setText("▶ 下一天")
        self.host._load_chart_for_current_symbol()
        self._update_holding_prices()
        self._refresh_trade_summary()

    def _on_buy_open(self) -> None:
        self._execute_buy(price_field="open", price_label="开盘价")

    def _on_buy_close(self) -> None:
        self._execute_buy(price_field="close", price_label="收盘价")

    def _execute_buy(self, price_field: str, price_label: str) -> None:
        host = self.host
        if not host._current_symbol or host._current_df is None:
            return

        quantity = self.buy_quantity_input.value()
        if quantity <= 0:
            host.statusMessageRequested.emit("请输入有效买入数量", 2000)
            return

        price = float(host._current_df.iloc[-1][price_field])
        buy_amount = price * quantity

        if buy_amount > self.available_capital:
            QtWidgets.QMessageBox.warning(
                host, "资金不足",
                f"买入需要 ¥{buy_amount:,.2f}，可用资金仅 ¥{self.available_capital:,.2f}",
            )
            return

        self.simulator.buy(
            host._current_symbol,
            host._current_stock_name,
            price,
            quantity,
            self.current_sim_date,
        )

        self.available_capital -= buy_amount
        self.available_capital_label.setText(f"¥ {self.available_capital:,.2f}")

        self._refresh_holding_table()
        self._refresh_trade_summary()
        self._redraw_trade_markers()
        host.statusMessageRequested.emit(
            f"{price_label}买入 {host._current_symbol} {quantity}股 @ ¥{price:.2f}",
            3000,
        )

    def _on_sell(self) -> None:
        host = self.host
        if not host._current_symbol or host._current_df is None:
            return

        # T+1 限制：当天买入的股票不能当天卖出
        today_buy_records = [
            r for r in self.simulator.trade_records
            if r.symbol == host._current_symbol
            and r.action == TradeAction.BUY
            and r.trade_date == self.current_sim_date
        ]
        if today_buy_records:
            QtWidgets.QMessageBox.warning(
                host, "T+1 限制",
                f"{host._current_symbol} 今日有买入，A股 T+1 规则不允许当天卖出",
            )
            return

        quantity = self.sell_quantity_input.value()
        if quantity <= 0:
            host.statusMessageRequested.emit("请输入有效卖出数量", 2000)
            return

        close_price = float(host._current_df.iloc[-1]["close"])

        try:
            self.simulator.sell(
                host._current_symbol,
                host._current_stock_name,
                close_price,
                quantity,
                self.current_sim_date,
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(host, "卖出失败", str(exc))
            return

        sell_amount = close_price * quantity
        self.available_capital += sell_amount
        self.available_capital_label.setText(f"¥ {self.available_capital:,.2f}")

        self._refresh_holding_table()
        self._refresh_trade_summary()
        self._redraw_trade_markers()
        host.statusMessageRequested.emit(
            f"卖出 {host._current_symbol} {quantity}股 @ ¥{close_price:.2f}",
            3000,
        )

    def _on_settle(self) -> None:
        host = self.host
        if not self.simulator.holdings:
            QtWidgets.QMessageBox.information(host, "提示", "当前无持仓")
            return

        result = self.simulator.settle()

        lines = [
            f"总投入：¥{result.total_cost:,.2f}",
            f"总市值：¥{result.total_value:,.2f}",
            f"总盈亏：¥{result.total_pnl_amount:,.2f}"
            f"（{result.total_pnl_percent:+.2f}%）",
            f"交易笔数：{result.trade_count}",
            "",
            "── 持仓明细 ──",
        ]
        for holding in result.holdings_at_settle:
            lines.append(
                f"  {holding.symbol} {holding.name}  "
                f"{holding.quantity}股  "
                f"成本¥{holding.average_cost:.2f}  "
                f"现价¥{holding.current_price:.2f}  "
                f"盈亏{holding.pnl_percent:+.2f}%"
            )

        reply = QtWidgets.QMessageBox.question(
            host,
            "结算确认",
            "\n".join(lines) + "\n\n确认结算并重置？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )

        if reply == QtWidgets.QMessageBox.Yes:
            self.simulator.reset()
            self._clear_trade_markers()
            self._refresh_holding_table()
            self._refresh_trade_summary()
            host.statusMessageRequested.emit("模拟交易已结算", 3000)

    # ── 内部辅助 ─────────────────────────────────────────────

    def _update_sim_info(self) -> None:
        """更新操作面板的日期和价格显示。"""
        host = self.host
        if host._current_df is not None and not host._current_df.empty:
            last_row = host._current_df.iloc[-1]
            date_val = last_row["date"]
            date_str = (
                date_val.strftime("%Y-%m-%d")
                if hasattr(date_val, "strftime")
                else str(date_val)[:10]
            )
            self.sim_date_label.setText(date_str)
            price_field = "open" if self.is_at_open else "close"
            price_label = "开盘" if self.is_at_open else "收盘"
            price = float(last_row[price_field])
            self.sim_price_label.setText(f"¥ {price:.2f}（{price_label}）")
        else:
            self.sim_date_label.setText("--")
            self.sim_price_label.setText("--")

    def _refresh_holding_table(self) -> None:
        """刷新左侧持有股票列表。"""
        holding_table = self.host.holding_table
        holdings = self.simulator.get_all_holdings()
        holding_table.setRowCount(len(holdings))

        for row, holding in enumerate(holdings):
            is_locked = self.is_today_bought(holding.symbol)

            symbol_text = holding.symbol
            if is_locked:
                symbol_text += " 🔒T+1"
            symbol_item = QtWidgets.QTableWidgetItem(symbol_text)
            if is_locked:
                symbol_item.setForeground(QtGui.QColor("#999999"))
            holding_table.setItem(row, 0, symbol_item)

            holding_table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(holding.name)
            )
            holding_table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(str(holding.quantity))
            )
            holding_table.setItem(
                row, 3, QtWidgets.QTableWidgetItem(f"{holding.average_cost:.2f}")
            )
            holding_table.setItem(
                row, 4, QtWidgets.QTableWidgetItem(f"{holding.current_price:.2f}")
            )

            pnl_item = QtWidgets.QTableWidgetItem(
                f"{holding.pnl_percent:+.2f}%"
            )
            pnl_color = "#FF4444" if holding.pnl_percent >= 0 else "#00CC00"
            pnl_item.setForeground(QtGui.QColor(pnl_color))
            holding_table.setItem(row, 5, pnl_item)

        holding_table.resizeColumnsToContents()

    def _refresh_trade_summary(self) -> None:
        """刷新操作面板的汇总信息。"""
        holdings = self.simulator.get_all_holdings()
        total_cost = sum(h.total_cost for h in holdings)
        total_value = sum(h.current_value for h in holdings)
        total_pnl_pct = (
            ((total_value - total_cost) / total_cost * 100)
            if total_cost > 0
            else 0.0
        )

        self.total_cost_label.setText(f"¥ {total_cost:,.2f}")
        self.total_value_label.setText(f"¥ {total_value:,.2f}")

        pnl_text = f"{total_pnl_pct:+.2f}%"
        pnl_color = "#FF4444" if total_pnl_pct >= 0 else "#00CC00"
        self.total_pnl_label.setText(pnl_text)
        self.total_pnl_label.setStyleSheet(
            f"color: {pnl_color}; font-weight: bold;"
        )

    def _update_holding_prices(self) -> None:
        """推进到新交易日后，更新所有持仓的当前价格。"""
        host = self.host
        price_field = "open" if self.is_at_open else "close"
        price_map: dict[str, float] = {}
        for symbol in self.simulator.holdings:
            try:
                df = load_daily_csv(host.stock_daily_data_dir, symbol)
                mask = df["date"] <= self.current_sim_date
                df_up = df[mask]
                if not df_up.empty:
                    price_map[symbol] = float(df_up.iloc[-1][price_field])
            except FileNotFoundError:
                pass

        self.simulator.update_all_prices(price_map)
        self._refresh_holding_table()

    def _redraw_trade_markers(self) -> None:
        """重绘当前股票的 B/S 标记。"""
        host = self.host
        self._clear_trade_markers()

        if host._current_df is None or host._current_df.empty:
            return

        records = [
            r
            for r in self.simulator.trade_records
            if r.symbol == host._current_symbol
        ]

        for record in records:
            trade_date_str = record.trade_date
            date_indices = host._current_df.index[
                host._current_df["date"].apply(
                    lambda d: (
                        d.strftime("%Y-%m-%d")
                        if hasattr(d, "strftime")
                        else str(d)[:10]
                    )
                )
                == trade_date_str
            ]
            if len(date_indices) == 0:
                continue

            x_pos = int(date_indices[0])
            row = host._current_df.iloc[x_pos]

            bar_high = float(row["high"])
            bar_low = float(row["low"])
            color = "#FF4444" if record.action == TradeAction.BUY else "#00CC00"
            letter = "B" if record.action == TradeAction.BUY else "S"

            local_top = bar_high
            local_bottom = bar_low
            short_trend = getattr(host.chart, "_short_trend_values", [])
            long_short = getattr(host.chart, "_long_short_values", [])
            if x_pos < len(short_trend):
                local_top = max(local_top, float(short_trend[x_pos]))
                local_bottom = min(local_bottom, float(short_trend[x_pos]))
            if x_pos < len(long_short):
                local_top = max(local_top, float(long_short[x_pos]))
                local_bottom = min(local_bottom, float(long_short[x_pos]))

            global_high = float(host._current_df["high"].max())
            global_low = float(host._current_df["low"].min())
            price_range = global_high - global_low if global_high > global_low else 1.0
            marker_offset = price_range * 0.02
            space_above = global_high - local_top
            space_below = local_bottom - global_low
            place_below = space_below >= space_above

            if place_below:
                text = f"▲\n{letter}"
                y_pos = local_bottom - marker_offset
                anchor = (0.5, 0)
            else:
                text = f"{letter}\n▼"
                y_pos = local_top + marker_offset
                anchor = (0.5, 1)

            marker = pg.TextItem(text=text, color=color, anchor=anchor)
            font = QtGui.QFont("Arial", 8, QtGui.QFont.Weight.Bold)
            marker.setFont(font)
            marker.setPos(x_pos, y_pos)
            host.chart.pricePlot.addItem(marker)
            self._trade_marker_items.append(marker)

    def _clear_trade_markers(self) -> None:
        """清除所有交易标记。"""
        for item in self._trade_marker_items:
            self.host.chart.pricePlot.removeItem(item)
        self._trade_marker_items.clear()
