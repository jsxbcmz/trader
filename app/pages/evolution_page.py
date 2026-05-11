"""策略进化页面 — LLM驱动的策略自动生成与迭代优化。

用户输入策略意图，系统调用LLM生成策略，自动回测评估，
根据绩效反馈迭代优化，最终保留最优策略。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from core.evolution.config import EvolutionConfig
from core.evolution.evaluator import EvalResult, StrategyEvaluator
from core.evolution.evolver import EvolutionResult, StrategyEvolver
from core.evolution.generator import GeneratedStrategy, StrategyGenerator
from core.evolution.memory import EvolutionMemory, EvolutionRecord


class EvolutionWorker(QtCore.QObject):
    """在子线程中运行策略进化"""

    round_finished = QtCore.Signal(int, object)  # round_num, EvolutionRecord
    all_finished = QtCore.Signal(object)  # EvolutionResult
    error = QtCore.Signal(str)
    log_message = QtCore.Signal(str)

    def __init__(self, root: Path, intent: str, config: EvolutionConfig, stock_pool: list[str]):
        super().__init__()
        self.root = root
        self.intent = intent
        self.config = config
        self.stock_pool = stock_pool

    def run(self):
        try:
            from core.backtest.config import BacktestConfig
            from core.data.repository import StockRepository

            repo = StockRepository(self.root)

            def data_loader(symbol: str):
                return repo.get_daily_frame(symbol)

            generator = StrategyGenerator(self.config)
            evaluator = StrategyEvaluator(data_loader, BacktestConfig())
            memory = EvolutionMemory()
            evolver = StrategyEvolver(generator, evaluator, memory, self.config)

            self.log_message.emit(f"开始策略进化: {self.intent}")
            self.log_message.emit(f"股票池: {len(self.stock_pool)} 只, 最大轮次: {self.config.max_rounds}")

            result = evolver.evolve(
                intent=self.intent,
                stock_pool=self.stock_pool,
            )

            # 逐轮发送信号以更新UI
            for record in result.history:
                self.round_finished.emit(record.round_num, record)

            self.all_finished.emit(result)

        except Exception as exc:
            self.error.emit(str(exc))


_INTENT_TEMPLATES: dict[str, str] = {
    "brick_n_shape": (
        "砖形图N型起跳策略。\n"
        "【必备前提】砖形图绿转红 + 翻红力度比≥0.3 + 短趋线>多空线。\n"
        "【选取逻辑】N型起跳：前段有明确上涨波段(涨幅≥10%、持续≥4天)，"
        "随后出现绿砖回调(最长连续绿砖段4-6天最佳)，回调期间KDJ的J值跌至40以下(越低越好，<10为深度超卖)，"
        "价格贴近短趋线(偏离-3%~+2%最佳)，今日绿转红形成N型第二笔启动。\n"
        "【评分体系】满分100分 = 定式专属30(超卖深度10+回调充分度10+价格与黄白线5+前段上涨基础5) "
        "+ 通用质量30(翻红力度比7+信号日涨幅6+短趋vs多空6+均线排列4+短趋斜率3+K线形态4) "
        "+ MACD环境25(DIFF/DEA位置8+金叉共振7+MACD柱5+底背离5) "
        "+ 信号强度15(量比7+换手率5+涨幅弹性3) - 风险扣分。\n"
        "【评级】S≥90, A≥75, B≥60, C≥45, D<45。B级以上买入。"
    ),
    "brick_sideways": (
        "砖形图横盘起跳策略。\n"
        "【必备前提】砖形图绿转红 + 翻红力度比≥0.3 + 短趋线>多空线。\n"
        "【选取逻辑】横盘起跳：近10日砖色频繁切换(红绿交替5-7次为最佳蓄势)，"
        "振幅收窄(<8%加分)，前一日为绿砖且前2日非绿砖(非连续下跌)，"
        "今日突然放量翻红(砖值跳升≥9，越大弹性越强)，KDJ的J值处于65-85区间动能最佳，"
        "价格在多空线上方5%-15%为强势区间。\n"
        "【评分体系】满分100分 = 定式专属30(蓄势充分度12+突破弹性8+KDJ动能5+价格强度5) "
        "+ 通用质量30 + MACD环境25 + 信号强度15 - 风险扣分。\n"
        "【评级】S≥90, A≥75, B≥60, C≥45, D<45。B级以上买入。"
    ),
    "brick_uptrend": (
        "砖形图上升波段延续策略。\n"
        "【必备前提】砖形图绿转红 + 翻红力度比≥0.3 + 短趋线>多空线。\n"
        "【选取逻辑】上升波段延续：前方连续红砖≥3根(≥7根为强势趋势)，"
        "中间仅出现极短绿砖回调(1-2根最佳)，砖值回落极小(<1最佳)，"
        "砖值绝对水平≥85(≥130为超强势)，KDJ的J值>80处于超买动能区(>95最强)，"
        "今日重新翻红延续上升波段。\n"
        "【评分体系】满分100分 = 定式专属30(趋势连续性12+回调极短性8+砖值绝对水平5+KDJ超买动能5) "
        "+ 通用质量30 + MACD环境25 + 信号强度15 - 风险扣分。\n"
        "【评级】S≥90, A≥75, B≥60, C≥45, D<45。B级以上买入。"
    ),
    "brick_full": (
        "砖形图综合三定式选股策略（含完整评分体系）。\n\n"
        "【必备前提（三项全部满足才进入定式检测）】\n"
        "1. 砖形图绿转红（前日绿砖，今日红砖）\n"
        "2. 翻红力度达标：今日砖值变化 / 昨日砖值变化 ≥ 0.3\n"
        "3. 短趋线 > 多空线（趋势向上）\n\n"
        "【三种交易定式】\n"
        "① N型起跳：前段上涨→绿砖回调(4-6天最佳)→KDJ J值<40超卖→今日翻红启动第二笔\n"
        "② 横盘起跳：近10日红绿频繁交替(5-7次蓄势)→振幅收窄→今日突破翻红(砖值跳升≥9)\n"
        "③ 上升波段延续：连续红砖≥3根→极短绿砖(1-2根)→砖值≥85→今日翻红延续\n\n"
        "【V3评分体系（满分100）】\n"
        "• 定式专属分(30)：每种定式有独立的4个评分维度\n"
        "• 通用质量分(30)：翻红力度比7+信号日涨幅6+短趋vs多空6+均线排列4+短趋斜率3+K线形态4\n"
        "• MACD环境分(25)：DIFF/DEA位置8+金叉共振7+MACD柱5+底背离5\n"
        "• 信号强度分(15)：量比7+换手率5+涨幅弹性3\n"
        "• 风险扣分：涨停封板-15、高位放量-10、连板过热-8等\n\n"
        "【评级标准】S≥90, A≥75, B≥60, C≥45, D<45\n"
        "【买入条件】综合评分达B级(≥60分)以上\n"
        "【卖出条件】砖形图转绿且力度达标，或跌破多空线，或评分降至D级"
    ),
}


class EvolutionPage(QtWidgets.QWidget):
    """策略进化页面"""

    statusMessageRequested = QtCore.Signal(str, int)

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.root = root
        self._worker: EvolutionWorker | None = None
        self._thread: QtCore.QThread | None = None
        self._evolution_records: list[EvolutionRecord] = []
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # 标题
        title = QtWidgets.QLabel("策略进化")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        main_layout.addWidget(title)

        subtitle = QtWidgets.QLabel("描述你的策略意图，AI 自动生成通达信公式策略，回测验证并迭代优化")
        subtitle.setStyleSheet("color: #666; margin-bottom: 8px;")
        main_layout.addWidget(subtitle)

        # 参数区
        params_group = QtWidgets.QGroupBox("进化参数")
        params_layout = QtWidgets.QGridLayout(params_group)
        params_layout.setSpacing(8)

        # 预设模板选择
        params_layout.addWidget(QtWidgets.QLabel("预设模板:"), 0, 0)
        self._template_combo = QtWidgets.QComboBox()
        self._template_combo.addItem("自定义（手动输入）", "")
        self._template_combo.addItem("砖形图 - N型起跳", "brick_n_shape")
        self._template_combo.addItem("砖形图 - 横盘起跳", "brick_sideways")
        self._template_combo.addItem("砖形图 - 上升波段延续", "brick_uptrend")
        self._template_combo.addItem("砖形图 - 综合三定式+评分", "brick_full")
        self._template_combo.setFixedWidth(260)
        params_layout.addWidget(self._template_combo, 0, 1)

        # 策略意图输入
        params_layout.addWidget(QtWidgets.QLabel("策略意图:"), 1, 0, QtCore.Qt.AlignTop)
        self._intent_input = QtWidgets.QTextEdit()
        self._intent_input.setPlaceholderText(
            "描述你想要的策略，或从上方预设模板选择填充。例如：\n"
            "• 找到放量突破20日均线的股票，回踩5日线时买入\n"
            "• MACD底背离配合KDJ金叉的抄底策略"
        )
        self._intent_input.setFixedHeight(100)
        params_layout.addWidget(self._intent_input, 1, 1, 1, 5)

        # LLM配置
        params_layout.addWidget(QtWidgets.QLabel("API地址:"), 2, 0)
        self._api_url_input = QtWidgets.QLineEdit()
        self._api_url_input.setPlaceholderText("https://idealab.alibaba-inc.com/api/anthropic")
        self._api_url_input.setText("https://idealab.alibaba-inc.com/api/anthropic")
        self._api_url_input.setFixedWidth(280)
        params_layout.addWidget(self._api_url_input, 2, 1)

        params_layout.addWidget(QtWidgets.QLabel("API Key:"), 2, 2)
        self._api_key_input = QtWidgets.QLineEdit()
        self._api_key_input.setText("1a2bf8f28a33992434aa6a5c95fea413")
        self._api_key_input.setPlaceholderText("sk-...")
        self._api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self._api_key_input.setFixedWidth(200)
        params_layout.addWidget(self._api_key_input, 2, 3)

        params_layout.addWidget(QtWidgets.QLabel("模型:"), 2, 4)
        self._model_input = QtWidgets.QComboBox()
        self._model_input.setEditable(True)
        self._model_input.addItems(["claude-opus-4-7", "Qwen/Qwen3-30B-A3B", "Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"])
        self._model_input.setFixedWidth(200)
        params_layout.addWidget(self._model_input, 2, 5)

        # 进化参数
        params_layout.addWidget(QtWidgets.QLabel("最大轮次:"), 3, 0)
        self._max_rounds_input = QtWidgets.QSpinBox()
        self._max_rounds_input.setRange(1, 20)
        self._max_rounds_input.setValue(5)
        self._max_rounds_input.setFixedWidth(80)
        params_layout.addWidget(self._max_rounds_input, 3, 1)

        params_layout.addWidget(QtWidgets.QLabel("最低胜率:"), 3, 2)
        self._min_winrate_input = QtWidgets.QDoubleSpinBox()
        self._min_winrate_input.setRange(0.1, 0.9)
        self._min_winrate_input.setValue(0.5)
        self._min_winrate_input.setSingleStep(0.05)
        self._min_winrate_input.setFixedWidth(80)
        params_layout.addWidget(self._min_winrate_input, 3, 3)

        params_layout.addWidget(QtWidgets.QLabel("最低盈亏比:"), 3, 4)
        self._min_plr_input = QtWidgets.QDoubleSpinBox()
        self._min_plr_input.setRange(0.5, 5.0)
        self._min_plr_input.setValue(1.5)
        self._min_plr_input.setSingleStep(0.1)
        self._min_plr_input.setFixedWidth(80)
        params_layout.addWidget(self._min_plr_input, 3, 5)

        # 股票池
        params_layout.addWidget(QtWidgets.QLabel("评估股票:"), 4, 0)
        self._stock_pool_input = QtWidgets.QLineEdit()
        self._stock_pool_input.setPlaceholderText("输入股票代码，逗号分隔，如: 000001,600519,300750")
        self._stock_pool_input.setText("000001,600519,000858,601318,300750")
        params_layout.addWidget(self._stock_pool_input, 4, 1, 1, 3)

        # 运行按钮
        self._run_btn = QtWidgets.QPushButton("🧬 开始进化")
        self._run_btn.setFixedWidth(130)
        self._run_btn.setStyleSheet(
            "QPushButton { background: #7B1FA2; color: white; border-radius: 4px; "
            "padding: 6px 12px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background: #6A1B9A; }"
            "QPushButton:disabled { background: #BDBDBD; }"
        )
        params_layout.addWidget(self._run_btn, 4, 4, 1, 2)

        main_layout.addWidget(params_group)

        # 结果区 splitter
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        # 上部：进化日志
        log_group = QtWidgets.QGroupBox("进化日志")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self._log_text = QtWidgets.QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(
            "QTextEdit { background: #1E1E1E; color: #D4D4D4; font-family: 'Courier New', monospace; "
            "font-size: 12px; border: 1px solid #333; }"
        )
        log_layout.addWidget(self._log_text)
        splitter.addWidget(log_group)

        # 下部：左侧轮次列表 + 右侧策略详情
        bottom_widget = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        # 轮次列表
        rounds_group = QtWidgets.QGroupBox("进化轮次")
        rounds_layout = QtWidgets.QVBoxLayout(rounds_group)
        self._rounds_table = QtWidgets.QTableWidget()
        self._rounds_table.setColumnCount(6)
        self._rounds_table.setHorizontalHeaderLabels(
            ["轮次", "策略名称", "胜率", "盈亏比", "最大回撤", "交易次数"]
        )
        self._rounds_table.horizontalHeader().setStretchLastSection(True)
        self._rounds_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._rounds_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._rounds_table.setAlternatingRowColors(True)
        self._rounds_table.verticalHeader().setVisible(False)
        self._rounds_table.setColumnWidth(0, 50)
        self._rounds_table.setColumnWidth(1, 160)
        self._rounds_table.setColumnWidth(2, 70)
        self._rounds_table.setColumnWidth(3, 70)
        self._rounds_table.setColumnWidth(4, 80)
        self._rounds_table.setColumnWidth(5, 70)
        rounds_layout.addWidget(self._rounds_table)
        rounds_group.setFixedWidth(560)
        bottom_layout.addWidget(rounds_group)

        # 策略详情
        detail_group = QtWidgets.QGroupBox("策略详情")
        detail_layout = QtWidgets.QVBoxLayout(detail_group)

        self._strategy_name_label = QtWidgets.QLabel("选择一轮查看策略详情")
        self._strategy_name_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        detail_layout.addWidget(self._strategy_name_label)

        self._desc_label = QtWidgets.QLabel("")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #666; margin-bottom: 6px;")
        detail_layout.addWidget(self._desc_label)

        buy_title = QtWidgets.QLabel("买入条件:")
        buy_title.setStyleSheet("font-weight: bold; color: #D32F2F; margin-top: 4px;")
        detail_layout.addWidget(buy_title)

        self._buy_expr_text = QtWidgets.QTextEdit()
        self._buy_expr_text.setReadOnly(True)
        self._buy_expr_text.setFixedHeight(50)
        self._buy_expr_text.setStyleSheet(
            "QTextEdit { background: #FFF3F3; border: 1px solid #FFCDD2; font-family: monospace; font-size: 12px; }"
        )
        detail_layout.addWidget(self._buy_expr_text)

        sell_title = QtWidgets.QLabel("卖出条件:")
        sell_title.setStyleSheet("font-weight: bold; color: #388E3C; margin-top: 4px;")
        detail_layout.addWidget(sell_title)

        self._sell_expr_text = QtWidgets.QTextEdit()
        self._sell_expr_text.setReadOnly(True)
        self._sell_expr_text.setFixedHeight(50)
        self._sell_expr_text.setStyleSheet(
            "QTextEdit { background: #F1F8E9; border: 1px solid #C5E1A5; font-family: monospace; font-size: 12px; }"
        )
        detail_layout.addWidget(self._sell_expr_text)

        # 绩效指标
        perf_title = QtWidgets.QLabel("回测绩效:")
        perf_title.setStyleSheet("font-weight: bold; margin-top: 6px;")
        detail_layout.addWidget(perf_title)

        self._perf_labels: dict[str, QtWidgets.QLabel] = {}
        perf_grid = QtWidgets.QGridLayout()
        perf_grid.setSpacing(4)
        perf_items = [
            ("total_return", "总收益率"),
            ("win_rate", "胜率"),
            ("profit_loss_ratio", "盈亏比"),
            ("max_drawdown", "最大回撤"),
            ("sharpe_ratio", "夏普比率"),
            ("total_trades", "交易次数"),
        ]
        for idx, (key, label_text) in enumerate(perf_items):
            row, col = idx // 2, (idx % 2) * 2
            name_label = QtWidgets.QLabel(f"{label_text}:")
            name_label.setStyleSheet("color: #555; font-size: 12px;")
            val_label = QtWidgets.QLabel("--")
            val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
            perf_grid.addWidget(name_label, row, col)
            perf_grid.addWidget(val_label, row, col + 1)
            self._perf_labels[key] = val_label
        detail_layout.addLayout(perf_grid)

        detail_layout.addStretch(1)
        bottom_layout.addWidget(detail_group, 1)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter, 1)

        # 状态栏
        self._status_label = QtWidgets.QLabel("就绪")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        main_layout.addWidget(self._status_label)

    def _connect_signals(self):
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._rounds_table.currentCellChanged.connect(self._on_round_selected)
        self._template_combo.currentIndexChanged.connect(self._on_template_selected)

    def _on_template_selected(self, index: int):
        template_key = self._template_combo.currentData()
        if not template_key:
            return
        template_text = _INTENT_TEMPLATES.get(template_key, "")
        if template_text:
            self._intent_input.setPlainText(template_text)

    def _on_run_clicked(self):
        intent = self._intent_input.toPlainText().strip()
        if not intent:
            self.statusMessageRequested.emit("请输入策略意图描述", 3000)
            return

        api_url = self._api_url_input.text().strip()
        api_key = self._api_key_input.text().strip()
        if not api_key:
            self.statusMessageRequested.emit("请输入 API Key", 3000)
            return

        stock_text = self._stock_pool_input.text().strip()
        if not stock_text:
            self.statusMessageRequested.emit("请输入评估股票代码", 3000)
            return

        stock_pool = [s.strip().zfill(6) for s in stock_text.split(",") if s.strip()]

        config = EvolutionConfig(
            max_rounds=self._max_rounds_input.value(),
            min_win_rate=self._min_winrate_input.value(),
            min_profit_loss_ratio=self._min_plr_input.value(),
            llm_model=self._model_input.currentText(),
            llm_base_url=api_url,
            llm_api_key=api_key,
        )

        self._run_btn.setEnabled(False)
        self._log_text.clear()
        self._rounds_table.setRowCount(0)
        self._clear_detail()
        self._evolution_records = []
        self._status_label.setText("正在进化中...")
        self._status_label.setStyleSheet("color: #7B1FA2; font-size: 11px;")

        self._thread = QtCore.QThread()
        self._worker = EvolutionWorker(self.root, intent, config, stock_pool)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.round_finished.connect(self._on_round_finished)
        self._worker.all_finished.connect(self._on_evolution_done)
        self._worker.error.connect(self._on_evolution_error)
        self._worker.log_message.connect(self._append_log)
        self._worker.all_finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _append_log(self, message: str):
        self._log_text.append(message)

    def _on_round_finished(self, round_num: int, record: EvolutionRecord):
        self._evolution_records.append(record)
        evaluation = record.eval_result
        strategy = record.strategy

        # 添加日志
        self._append_log(
            f"\n── 第 {round_num} 轮 ──\n"
            f"  策略: {strategy.name}\n"
            f"  买入: {strategy.buy_expr}\n"
            f"  卖出: {strategy.sell_expr}\n"
            f"  胜率: {evaluation.win_rate:.1%}  盈亏比: {evaluation.profit_loss_ratio:.2f}  "
            f"回撤: {evaluation.max_drawdown:.1%}  交易: {evaluation.total_trades}次"
        )

        # 添加到轮次表
        row = self._rounds_table.rowCount()
        self._rounds_table.insertRow(row)
        self._rounds_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(round_num)))
        self._rounds_table.setItem(row, 1, QtWidgets.QTableWidgetItem(strategy.name))

        winrate_item = QtWidgets.QTableWidgetItem(f"{evaluation.win_rate:.1%}")
        winrate_item.setForeground(
            QtGui.QColor("#4CAF50") if evaluation.win_rate >= 0.5 else QtGui.QColor("#F44336")
        )
        self._rounds_table.setItem(row, 2, winrate_item)

        plr_item = QtWidgets.QTableWidgetItem(f"{evaluation.profit_loss_ratio:.2f}")
        plr_item.setForeground(
            QtGui.QColor("#4CAF50") if evaluation.profit_loss_ratio >= 1.5 else QtGui.QColor("#FF9800")
        )
        self._rounds_table.setItem(row, 3, plr_item)

        dd_item = QtWidgets.QTableWidgetItem(f"{evaluation.max_drawdown:.1%}")
        dd_item.setForeground(QtGui.QColor("#F44336"))
        self._rounds_table.setItem(row, 4, dd_item)

        self._rounds_table.setItem(row, 5, QtWidgets.QTableWidgetItem(str(evaluation.total_trades)))

    def _on_evolution_done(self, result: EvolutionResult):
        self._run_btn.setEnabled(True)

        if result.success:
            self._append_log(f"\n✅ 进化成功! 共 {result.total_rounds} 轮")
            self._status_label.setText(f"进化成功 | 共 {result.total_rounds} 轮")
            self._status_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
            self.statusMessageRequested.emit("策略进化成功!", 3000)
        else:
            self._append_log(f"\n⚠️ 未达标，已完成 {result.total_rounds} 轮，返回最优策略")
            self._status_label.setText(f"进化完成（未达标）| 共 {result.total_rounds} 轮")
            self._status_label.setStyleSheet("color: #FF9800; font-size: 11px;")
            self.statusMessageRequested.emit("进化完成，未完全达标", 3000)

        if result.best_strategy:
            self._append_log(
                f"\n★ 最优策略: {result.best_strategy.name}\n"
                f"  买入: {result.best_strategy.buy_expr}\n"
                f"  卖出: {result.best_strategy.sell_expr}"
            )

        # 自动选中最优的一轮
        if self._evolution_records:
            best_idx = 0
            best_score = -1.0
            for idx, rec in enumerate(self._evolution_records):
                score = rec.eval_result.win_rate * 30 + min(rec.eval_result.profit_loss_ratio, 3) / 3 * 30
                if score > best_score:
                    best_score = score
                    best_idx = idx
            self._rounds_table.selectRow(best_idx)

    def _on_evolution_error(self, error_msg: str):
        self._run_btn.setEnabled(True)
        self._append_log(f"\n❌ 错误: {error_msg}")
        self._status_label.setText(f"进化失败: {error_msg}")
        self._status_label.setStyleSheet("color: #F44336; font-size: 11px;")
        self.statusMessageRequested.emit(f"进化失败: {error_msg}", 5000)

    def _on_round_selected(self, current_row: int, _col: int, _prev_row: int, _prev_col: int):
        if current_row < 0 or current_row >= len(self._evolution_records):
            return
        record = self._evolution_records[current_row]
        self._show_detail(record)

    def _show_detail(self, record: EvolutionRecord):
        strategy = record.strategy
        evaluation = record.eval_result

        self._strategy_name_label.setText(f"📋 {strategy.name}")
        self._desc_label.setText(strategy.description or "无描述")
        self._buy_expr_text.setPlainText(strategy.buy_expr)
        self._sell_expr_text.setPlainText(strategy.sell_expr)

        self._update_perf("total_return", f"{evaluation.total_return:+.2%}",
                          "#4CAF50" if evaluation.total_return >= 0 else "#F44336")
        self._update_perf("win_rate", f"{evaluation.win_rate:.1%}",
                          "#4CAF50" if evaluation.win_rate >= 0.5 else "#F44336")
        self._update_perf("profit_loss_ratio", f"{evaluation.profit_loss_ratio:.2f}",
                          "#4CAF50" if evaluation.profit_loss_ratio >= 1.5 else "#FF9800")
        self._update_perf("max_drawdown", f"{evaluation.max_drawdown:.1%}", "#F44336")
        self._update_perf("sharpe_ratio", f"{evaluation.sharpe_ratio:.2f}",
                          "#4CAF50" if evaluation.sharpe_ratio >= 1.0 else "#FF9800")
        self._update_perf("total_trades", str(evaluation.total_trades), "#333")

    def _update_perf(self, key: str, text: str, color: str = "#333"):
        label = self._perf_labels.get(key)
        if label:
            label.setText(text)
            label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")

    def _clear_detail(self):
        self._strategy_name_label.setText("选择一轮查看策略详情")
        self._desc_label.setText("")
        self._buy_expr_text.clear()
        self._sell_expr_text.clear()
        for label in self._perf_labels.values():
            label.setText("--")
            label.setStyleSheet("font-size: 13px; font-weight: bold;")
