"""评分诊断页（P0-7 + P0-8）。

新建的顶级页面，展示主板评分系统的：
1. 顶部工具栏：日期输入 + 当日 OAMV 阶段（P3 前显示"未启用"）+ "运行今日评分"按钮
2. 主表格：当日 Top 20 候选 + 每只票的评分子项
3. 双击行 → 弹出该股 K 线图
4. 底部：前 3 日 TopK 的 T+1/T+2/T+3 收益回填情况

"运行今日评分"按钮（P0-7）串联：
    MainBoardScoringEngine.score_date → save_scoring_daily → save_scoring_picks
    → OutcomesFiller.fill_for_today
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from app.data_loader import load_daily_csv
from app.utils.thread_manager import start_worker
from app.widgets import StockChartWidget
from core.scoring import (
    FactorHealth,
    MainBoardScoringEngine,
    OutcomesFiller,
    RegimeAnalyzer,
    load_monthly_report,
    load_outcomes,
    load_scoring_picks,
    save_scoring_daily,
    save_scoring_picks,
)


# ── Worker（P0-7）─────────────────────────────────────────


class ScoringWorker(QtCore.QObject):
    """串联评分 → 落盘 → 回填的后台 Worker。"""

    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, root: Path, target_date: str, k: int = 20):
        super().__init__()
        self.root = root
        self.target_date = target_date
        self.k = k

    # 实现见下方 run() 方法（保持原样）

    @QtCore.Slot()
    def run(self):
        try:
            self.progressChanged.emit({"stage": "regime", "message": "计算 OAMV 阶段标签..."})
            ra = RegimeAnalyzer.from_root(self.root)
            ra.save_for_date(self.target_date)
            rec = ra.get_regime(self.target_date)
            regime_str = f"{rec.smoothed_phase}-{rec.tempo}" if rec else ""

            self.progressChanged.emit({"stage": "scoring", "message": "评分中（主板 ~3000 只）..."})
            engine = MainBoardScoringEngine.from_root(self.root)
            result = engine.score_date(self.target_date)

            self.progressChanged.emit({"stage": "saving", "message": "评分明细落盘..."})
            daily_path = save_scoring_daily(self.root, self.target_date, result.matches)
            picks_path = save_scoring_picks(self.root, self.target_date, result.matches,
                                            k=self.k, regime=regime_str)

            self.progressChanged.emit({"stage": "outcomes", "message": "回填 T+1/T+2/T+3 实盘..."})
            filler = OutcomesFiller.from_root(self.root)
            filled = filler.fill_for_today(self.target_date)

            self.finished.emit({
                "target_date": self.target_date,
                "total": result.total,
                "matched_count": result.matched_count,
                "error_count": result.error_count,
                "regime": regime_str,
                "daily_path": str(daily_path),
                "picks_path": str(picks_path),
                "filled_dates": sorted(filled.keys()),
            })
        except Exception as e:
            self.errorOccurred.emit(f"{type(e).__name__}: {e}")


# ── 月报 Worker（P2-4）────────────────────────────────────


class ReportWorker(QtCore.QObject):
    """后台生成月度因子健康度报告。"""

    progressChanged = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    errorOccurred = QtCore.Signal(str)

    def __init__(self, root: Path, year_month: str, k: int = 20):
        super().__init__()
        self.root = root
        self.year_month = year_month
        self.k = k

    @QtCore.Slot()
    def run(self):
        try:
            self.progressChanged.emit({"message": f"生成 {self.year_month} 月度报告中..."})
            fh = FactorHealth.from_root(self.root)
            report, path = fh.generate_monthly_report(self.year_month, k=self.k)
            self.finished.emit({
                "year_month": self.year_month,
                "report": report,
                "path": str(path),
            })
        except Exception as e:
            self.errorOccurred.emit(f"{type(e).__name__}: {e}")


# ── 主页面（P0-8 + P2-4）──────────────────────────────────


class ScoringPage(QtWidgets.QWidget):
    """主板评分诊断页。"""

    statusMessageRequested = QtCore.Signal(str, int)

    # Top 20 表格列
    COL_RANK = 0
    COL_SYMBOL = 1
    COL_NAME = 2
    COL_TOTAL = 3
    COL_GRADE = 4
    COL_PATTERN = 5
    COL_SPECIFIC = 6
    COL_COMMON = 7
    COL_MACD = 8
    COL_SIGNAL = 9
    COL_RISK = 10
    TOP_HEADERS = ["#", "代码", "名称", "总分", "级", "定式", "定式分", "通用", "MACD", "信号", "风险"]

    # 回填表格列
    OC_SCORE_DATE = 0
    OC_SYMBOL = 1
    OC_NAME = 2
    OC_TOTAL = 3
    OC_T1_RETURN = 4
    OC_T2_RETURN = 5
    OC_T3_RETURN = 6
    OC_T1_GREEN = 7
    OC_T2_GREEN = 8
    OC_T3_GREEN = 9
    OC_HEADERS = ["评分日", "代码", "名称", "总分", "T+1 涨跌", "T+2 涨跌", "T+3 涨跌", "T+1 绿砖", "T+2 绿砖", "T+3 绿砖"]

    # 因子健康度 IC 表（P2-4）
    IC_FACTOR = 0
    IC_MEAN = 1
    IC_STD = 2
    IC_IR = 3
    IC_T = 4
    IC_N = 5
    IC_HEADERS = ["因子", "IC 均值", "IC 标准差", "IR", "t 统计", "样本天数"]

    def __init__(self, root: Path, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.root = root
        self._scoring_thread: Optional[QtCore.QThread] = None
        self._scoring_worker: Optional[ScoringWorker] = None
        self._report_thread: Optional[QtCore.QThread] = None
        self._report_worker: Optional[ReportWorker] = None
        self._setup_ui()
        self._connect_signals()
        # 初次加载：尝试展示最近一天的 picks
        QtCore.QTimer.singleShot(0, self._load_latest_picks)

    # ── UI 构建 ─────────────────────────────────────────────

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # ── 顶部工具栏 ──
        header = QtWidgets.QHBoxLayout()

        title = QtWidgets.QLabel("评分诊断")
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)

        header.addSpacing(20)
        header.addWidget(QtWidgets.QLabel("日期:"))
        self.date_input = QtWidgets.QLineEdit()
        self.date_input.setPlaceholderText("YYYY-MM-DD")
        self.date_input.setFixedWidth(110)
        self.date_input.setText(QtCore.QDate.currentDate().toString("yyyy-MM-dd"))
        header.addWidget(self.date_input)

        header.addSpacing(12)
        header.addWidget(QtWidgets.QLabel("OAMV 阶段:"))
        self.regime_label = QtWidgets.QLabel("未启用 (P3)")
        self.regime_label.setStyleSheet("color: #999;")
        header.addWidget(self.regime_label)

        header.addStretch()

        self.run_btn = QtWidgets.QPushButton("运行今日评分")
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setStyleSheet(
            "background-color: #1890FF; color: white; font-weight: bold; font-size: 13px;"
            "border-radius: 4px; padding: 4px 18px;"
        )
        header.addWidget(self.run_btn)

        self.reload_btn = QtWidgets.QPushButton("刷新展示")
        self.reload_btn.setMinimumHeight(34)
        header.addWidget(self.reload_btn)

        main_layout.addLayout(header)

        # ── 状态行 ──
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: #666;")
        main_layout.addWidget(self.status_label)

        # ── 主表格：Top 20 ──
        top_label = QtWidgets.QLabel("当日 Top 20 候选（双击行查看 K 线）")
        top_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        main_layout.addWidget(top_label)

        self.top_table = QtWidgets.QTableWidget()
        self.top_table.setColumnCount(len(self.TOP_HEADERS))
        self.top_table.setHorizontalHeaderLabels(self.TOP_HEADERS)
        self.top_table.horizontalHeader().setStretchLastSection(False)
        self.top_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.top_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.top_table.setAlternatingRowColors(True)
        self.top_table.verticalHeader().setDefaultSectionSize(26)
        self.top_table.verticalHeader().setVisible(False)
        widths = [40, 70, 110, 60, 40, 110, 70, 60, 60, 60, 60]
        for col, w in enumerate(widths):
            self.top_table.setColumnWidth(col, w)
        main_layout.addWidget(self.top_table, 3)

        # ── 底部：QTabWidget（实盘回填 + 因子健康度）──
        self.bottom_tabs = QtWidgets.QTabWidget()
        main_layout.addWidget(self.bottom_tabs, 2)

        # Tab 1：前 3 日 outcomes 回填
        oc_tab = QtWidgets.QWidget()
        oc_layout = QtWidgets.QVBoxLayout(oc_tab)
        oc_layout.setContentsMargins(4, 4, 4, 4)
        self.oc_table = QtWidgets.QTableWidget()
        self.oc_table.setColumnCount(len(self.OC_HEADERS))
        self.oc_table.setHorizontalHeaderLabels(self.OC_HEADERS)
        self.oc_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.oc_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.oc_table.setAlternatingRowColors(True)
        self.oc_table.verticalHeader().setDefaultSectionSize(24)
        self.oc_table.verticalHeader().setVisible(False)
        for col, w in enumerate([90, 70, 110, 60, 80, 80, 80, 70, 70, 70]):
            self.oc_table.setColumnWidth(col, w)
        oc_layout.addWidget(self.oc_table)
        self.bottom_tabs.addTab(oc_tab, "前 3 日实盘回填")

        # Tab 2：因子健康度（P2-4）
        fh_tab = QtWidgets.QWidget()
        fh_layout = QtWidgets.QVBoxLayout(fh_tab)
        fh_layout.setContentsMargins(4, 4, 4, 4)
        fh_layout.setSpacing(6)

        fh_toolbar = QtWidgets.QHBoxLayout()
        fh_toolbar.addWidget(QtWidgets.QLabel("月份:"))
        self.month_input = QtWidgets.QLineEdit()
        self.month_input.setPlaceholderText("YYYY-MM")
        self.month_input.setFixedWidth(90)
        self.month_input.setText(QtCore.QDate.currentDate().toString("yyyy-MM"))
        fh_toolbar.addWidget(self.month_input)

        fh_toolbar.addWidget(QtWidgets.QLabel("收益窗口:"))
        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItems(["t1", "t2", "t3"])
        self.target_combo.setFixedWidth(60)
        fh_toolbar.addWidget(self.target_combo)

        self.gen_report_btn = QtWidgets.QPushButton("生成月报")
        self.gen_report_btn.setMinimumHeight(28)
        fh_toolbar.addWidget(self.gen_report_btn)

        self.load_report_btn = QtWidgets.QPushButton("加载已有")
        self.load_report_btn.setMinimumHeight(28)
        fh_toolbar.addWidget(self.load_report_btn)

        fh_toolbar.addStretch()
        self.report_status_label = QtWidgets.QLabel("")
        self.report_status_label.setStyleSheet("color: #666;")
        fh_toolbar.addWidget(self.report_status_label)
        fh_layout.addLayout(fh_toolbar)

        # 左右分栏：IC 表 + 异常因子
        fh_split = QtWidgets.QHBoxLayout()
        fh_split.setSpacing(6)

        # 左：IC 排行榜
        left_box = QtWidgets.QVBoxLayout()
        left_box.addWidget(QtWidgets.QLabel("因子 IC 排行（|IC| 倒序）"))
        self.ic_table = QtWidgets.QTableWidget()
        self.ic_table.setColumnCount(len(self.IC_HEADERS))
        self.ic_table.setHorizontalHeaderLabels(self.IC_HEADERS)
        self.ic_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ic_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.ic_table.setAlternatingRowColors(True)
        self.ic_table.verticalHeader().setDefaultSectionSize(22)
        self.ic_table.verticalHeader().setVisible(False)
        for col, w in enumerate([130, 80, 80, 70, 70, 70]):
            self.ic_table.setColumnWidth(col, w)
        left_box.addWidget(self.ic_table)
        fh_split.addLayout(left_box, 3)

        # 右：异常因子告警 + Top K 汇总
        right_box = QtWidgets.QVBoxLayout()
        right_box.addWidget(QtWidgets.QLabel("Top K 实盘汇总"))
        self.topk_label = QtWidgets.QLabel("（点击'生成月报'或'加载已有'）")
        self.topk_label.setStyleSheet("background: #fafafa; padding: 6px; border: 1px solid #ddd;")
        self.topk_label.setWordWrap(True)
        right_box.addWidget(self.topk_label)

        right_box.addWidget(QtWidgets.QLabel("异常因子告警"))
        self.alerts_list = QtWidgets.QListWidget()
        right_box.addWidget(self.alerts_list, 1)
        fh_split.addLayout(right_box, 2)

        fh_layout.addLayout(fh_split, 1)
        self.bottom_tabs.addTab(fh_tab, "因子健康度（月报）")

    def _connect_signals(self):
        self.run_btn.clicked.connect(self._on_run_clicked)
        self.reload_btn.clicked.connect(self._reload_current_date)
        self.top_table.doubleClicked.connect(self._on_top_row_double_clicked)
        self.date_input.returnPressed.connect(self._reload_current_date)
        self.gen_report_btn.clicked.connect(self._on_gen_report_clicked)
        self.load_report_btn.clicked.connect(self._on_load_report_clicked)
        self.target_combo.currentTextChanged.connect(self._on_target_changed)
        self.oc_table.doubleClicked.connect(self._on_oc_row_double_clicked)

    # ── 数据加载 / 展示 ──────────────────────────────────────

    def _load_latest_picks(self):
        """启动时尝试加载最近一天有 picks 的数据。"""
        picks_dir = self.root / "output" / "scoring_picks"
        if not picks_dir.exists():
            return
        files = sorted(picks_dir.glob("*.json"), reverse=True)
        if not files:
            return
        date = files[0].stem
        self.date_input.setText(date)
        self._reload_current_date()

    @QtCore.Slot()
    def _reload_current_date(self):
        date = self.date_input.text().strip()
        if not date:
            return
        self._load_picks_to_table(date)
        self._load_outcomes_to_table(date)

    def _load_picks_to_table(self, date: str):
        data = load_scoring_picks(self.root, date)
        picks = data.get("picks", [])

        # P3-8: 阶段标签从 RegimeAnalyzer 实时计算（picks 文件里的 regime 字段是历史快照）
        regime_text = data.get("regime") or "—"
        try:
            ra = RegimeAnalyzer.from_root(self.root)
            rec = ra.get_regime(date)
            if rec is not None:
                tag = "多头" if rec.smoothed_phase == "bull" else "空头"
                color = "#27AE60" if rec.smoothed_phase == "bull" else "#C0392B"
                tempo = " · 快" if rec.tempo == "fast" else " · 缓"
                regime_text = f"{tag}{tempo}"
                self.regime_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        except Exception:
            self.regime_label.setStyleSheet("color: #999;")
        self.regime_label.setText(regime_text)

        self.top_table.setRowCount(len(picks))
        for r, p in enumerate(picks):
            bd = p.get("breakdown") or {}
            self._set_item(self.top_table, r, self.COL_RANK, str(p.get("rank", r + 1)))
            self._set_item(self.top_table, r, self.COL_SYMBOL, str(p.get("symbol", "")))
            self._set_item(self.top_table, r, self.COL_NAME, str(p.get("name", "")))
            self._set_item(self.top_table, r, self.COL_TOTAL, f"{p.get('total', 0):.0f}")
            self._set_item(self.top_table, r, self.COL_GRADE, str(p.get("grade", "")))
            self._set_item(self.top_table, r, self.COL_PATTERN, str(p.get("pattern", "")))
            self._set_item(self.top_table, r, self.COL_SPECIFIC, self._fmt_score(bd.get("定式专属")))
            self._set_item(self.top_table, r, self.COL_COMMON, self._fmt_score(bd.get("通用质量")))
            self._set_item(self.top_table, r, self.COL_MACD, self._fmt_score(bd.get("MACD辅助")))
            self._set_item(self.top_table, r, self.COL_SIGNAL, self._fmt_score(bd.get("信号强度")))
            risk = bd.get("风险扣分")
            self._set_item(self.top_table, r, self.COL_RISK,
                           "" if risk is None else f"{risk:+.0f}")

        self.status_label.setText(
            f"{date}：已加载 {len(picks)} 条 picks" if picks
            else f"{date}：未找到 picks 文件（先点'运行今日评分'）"
        )

    def _load_outcomes_to_table(self, today: str):
        """显示 today 之前 3 日的 outcomes（today-1 / today-2 / today-3）。"""
        picks_dir = self.root / "output" / "scoring_picks"
        all_dates = sorted([p.stem for p in picks_dir.glob("*.json")]) if picks_dir.exists() else []
        if today in all_dates:
            idx = all_dates.index(today)
            recent_dates = all_dates[max(0, idx - 3):idx]
        else:
            recent_dates = all_dates[-3:]

        # 从各日 picks 索引 (date, symbol) → {name, total}
        pick_map: dict[tuple[str, str], dict] = {}
        for d in recent_dates:
            for p in load_scoring_picks(self.root, d).get("picks", []):
                pick_map[(d, p["symbol"])] = {"name": p["name"], "total": p.get("total", 0.0)}

        rows = []
        for d in recent_dates:
            for rec in load_outcomes(self.root, d):
                rows.append((d, rec))

        self.oc_table.setRowCount(len(rows))
        for r, (score_date, rec) in enumerate(rows):
            info = pick_map.get((score_date, rec.symbol), {})
            self._set_item(self.oc_table, r, self.OC_SCORE_DATE, score_date)
            self._set_item(self.oc_table, r, self.OC_SYMBOL, rec.symbol)
            self._set_item(self.oc_table, r, self.OC_NAME, info.get("name", ""))
            self._set_item(self.oc_table, r, self.OC_TOTAL, f"{info.get('total', 0.0):.0f}")
            self._set_return_item(self.oc_table, r, self.OC_T1_RETURN, rec.t1_return)
            self._set_return_item(self.oc_table, r, self.OC_T2_RETURN, rec.t2_return)
            self._set_return_item(self.oc_table, r, self.OC_T3_RETURN, rec.t3_return)
            self._set_item(self.oc_table, r, self.OC_T1_GREEN, self._fmt_bool(rec.t1_is_green))
            self._set_item(self.oc_table, r, self.OC_T2_GREEN, self._fmt_bool(rec.t2_is_green))
            self._set_item(self.oc_table, r, self.OC_T3_GREEN, self._fmt_bool(rec.t3_is_green))

    # ── 运行按钮（P0-7 入口）────────────────────────────────

    @QtCore.Slot()
    def _on_run_clicked(self):
        if self._scoring_thread is not None and self._scoring_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "提示", "评分正在运行中，请等待完成")
            return

        date = self.date_input.text().strip()
        if not date:
            QtWidgets.QMessageBox.warning(self, "提示", "请输入日期（YYYY-MM-DD）")
            return

        self.run_btn.setEnabled(False)
        self.status_label.setText(f"启动评分流程：{date}")
        self.statusMessageRequested.emit(f"评分中：{date}", 0)

        self._scoring_worker = ScoringWorker(self.root, date, k=20)
        self._scoring_thread = start_worker(
            self,
            self._scoring_worker,
            on_progress=self._on_scoring_progress,
            on_finished=self._on_scoring_finished,
            on_error=self._on_scoring_error,
            on_cleanup=self._on_scoring_cleanup,
        )

    @QtCore.Slot(dict)
    def _on_scoring_progress(self, info: dict):
        msg = info.get("message", "")
        if msg:
            self.status_label.setText(msg)
            self.statusMessageRequested.emit(msg, 0)

    @QtCore.Slot(dict)
    def _on_scoring_finished(self, info: dict):
        date = info.get("target_date", "")
        total = info.get("total", 0)
        matched = info.get("matched_count", 0)
        errors = info.get("error_count", 0)
        filled = info.get("filled_dates", [])
        summary = (
            f"{date} 完成：候选 {total} / 命中 {matched} / 错误 {errors}"
            + (f"；回填 {', '.join(filled)}" if filled else "")
        )
        self.status_label.setText(summary)
        self.statusMessageRequested.emit(summary, 5000)
        self._reload_current_date()

    @QtCore.Slot(str)
    def _on_scoring_error(self, message: str):
        self.status_label.setText(f"评分失败：{message}")
        self.statusMessageRequested.emit(f"评分失败：{message}", 8000)
        QtWidgets.QMessageBox.critical(self, "评分失败", message)

    @QtCore.Slot()
    def _on_scoring_cleanup(self):
        self.run_btn.setEnabled(True)
        self._scoring_thread = None
        self._scoring_worker = None

    # ── 因子健康度（P2-4）─────────────────────────────────

    @QtCore.Slot()
    def _on_gen_report_clicked(self):
        if self._report_thread is not None and self._report_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "提示", "月报生成中，请稍候")
            return
        ym = self.month_input.text().strip()
        if len(ym) != 7 or "-" not in ym:
            QtWidgets.QMessageBox.warning(self, "提示", "月份格式应为 YYYY-MM")
            return

        self.gen_report_btn.setEnabled(False)
        self.report_status_label.setText(f"生成 {ym} 月报中…")
        self._report_worker = ReportWorker(self.root, ym, k=20)
        self._report_thread = start_worker(
            self,
            self._report_worker,
            on_progress=self._on_report_progress,
            on_finished=self._on_report_finished,
            on_error=self._on_report_error,
            on_cleanup=self._on_report_cleanup,
        )

    @QtCore.Slot()
    def _on_load_report_clicked(self):
        ym = self.month_input.text().strip()
        if not ym:
            return
        report = load_monthly_report(self.root, ym)
        if not report:
            self.report_status_label.setText(f"未找到 {ym} 月报，先点'生成月报'")
            self._render_report(None)
            return
        self.report_status_label.setText(f"已加载 {ym} 月报（{report.get('n_days', 0)} 天）")
        self._render_report(report)

    @QtCore.Slot(str)
    def _on_target_changed(self, _target: str):
        ym = self.month_input.text().strip()
        report = load_monthly_report(self.root, ym)
        if report:
            self._render_report(report)

    @QtCore.Slot(dict)
    def _on_report_progress(self, info: dict):
        msg = info.get("message", "")
        if msg:
            self.report_status_label.setText(msg)

    @QtCore.Slot(dict)
    def _on_report_finished(self, info: dict):
        ym = info.get("year_month", "")
        report = info.get("report", {})
        self.report_status_label.setText(
            f"{ym} 月报已生成（{report.get('n_days', 0)} 天，{len(report.get('alerts', []))} 条告警）"
        )
        self._render_report(report)

    @QtCore.Slot(str)
    def _on_report_error(self, message: str):
        self.report_status_label.setText(f"月报失败：{message}")
        QtWidgets.QMessageBox.critical(self, "月报生成失败", message)

    @QtCore.Slot()
    def _on_report_cleanup(self):
        self.gen_report_btn.setEnabled(True)
        self._report_thread = None
        self._report_worker = None

    def _render_report(self, report: Optional[dict]):
        """把 report 渲染到 IC 表 + 异常列表 + Top K 汇总。"""
        target = self.target_combo.currentText()
        if report is None or not report.get("factor_ic", {}).get(target):
            self.ic_table.setRowCount(0)
            self.alerts_list.clear()
            self.topk_label.setText("（无数据）")
            return

        ic_rows = report["factor_ic"][target]
        self.ic_table.setRowCount(len(ic_rows))
        for r, row in enumerate(ic_rows):
            self._set_item(self.ic_table, r, self.IC_FACTOR, str(row.get("factor", "")))
            self._set_ic_value(self.ic_table, r, self.IC_MEAN, row.get("ic_mean", 0.0), color=True)
            self._set_item(self.ic_table, r, self.IC_STD, f"{row.get('ic_std', 0.0):.4f}")
            self._set_ic_value(self.ic_table, r, self.IC_IR, row.get("ic_ir", 0.0), color=True)
            self._set_ic_value(self.ic_table, r, self.IC_T, row.get("t_stat", 0.0), color=False)
            self._set_item(self.ic_table, r, self.IC_N, str(int(row.get("n_days", 0))))

        # 异常因子
        self.alerts_list.clear()
        for alert in report.get("alerts", []):
            text = f"[{alert.get('type', '')}] {alert.get('factor', '')}: {alert.get('detail', '')}"
            item = QtWidgets.QListWidgetItem(text)
            if alert.get("type") == "无效因子":
                item.setForeground(QtGui.QColor("#C0392B"))
            else:
                item.setForeground(QtGui.QColor("#E67E22"))
            self.alerts_list.addItem(item)

        # Top K 汇总
        summary = report.get("topk_summary", {})
        parts = []
        for tgt in ("t1", "t2", "t3"):
            s = summary.get(tgt, {})
            if s.get("n_samples", 0) > 0:
                parts.append(
                    f"{tgt.upper()}: 收益 {s['avg_return']*100:+.2f}%  胜率 {s['win_rate']*100:.1f}%  "
                    f"({s['n_samples']} 样本)"
                )
        self.topk_label.setText("\n".join(parts) if parts else "（无样本）")

    # ── 双击行：弹 K 线 ─────────────────────────────────────

    @QtCore.Slot(QtCore.QModelIndex)
    def _on_top_row_double_clicked(self, index: QtCore.QModelIndex):
        row = index.row()
        sym_item = self.top_table.item(row, self.COL_SYMBOL)
        name_item = self.top_table.item(row, self.COL_NAME)
        if not sym_item:
            return
        symbol = sym_item.text().strip().zfill(6)
        name = name_item.text().strip() if name_item else ""
        target_date = self.date_input.text().strip()
        self._show_chart_dialog(symbol, name, target_date)

    @QtCore.Slot(QtCore.QModelIndex)
    def _on_oc_row_double_clicked(self, index: QtCore.QModelIndex):
        """outcomes 表双击：弹该股在评分日的 K 线。"""
        row = index.row()
        sym_item = self.oc_table.item(row, self.OC_SYMBOL)
        name_item = self.oc_table.item(row, self.OC_NAME)
        date_item = self.oc_table.item(row, self.OC_SCORE_DATE)
        if not sym_item or not date_item:
            return
        symbol = sym_item.text().strip().zfill(6)
        name = name_item.text().strip() if name_item else ""
        score_date = date_item.text().strip()
        self._show_chart_dialog(symbol, name, score_date)

    def _show_chart_dialog(self, symbol: str, name: str, target_date: str):
        """通用 K 线弹窗（聚焦 target_date，左侧最多 120 日）。"""
        try:
            df = load_daily_csv(self.root / "stock_daily_data", symbol)
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(self, "提示", f"未找到 {symbol} 的日线数据")
            return
        if df.empty:
            QtWidgets.QMessageBox.warning(self, "提示", f"{symbol} 数据为空")
            return

        df_full = df.copy().reset_index(drop=True)
        target_index = self._locate_target_index(df_full, target_date)

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"{symbol} {name} @ {target_date}")
        dialog.resize(1100, 700)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)

        chart = StockChartWidget()
        chart.set_daily(df_full)

        if target_index is not None:
            half_width = chart._item_half_width
            right_padding = chart._right_view_padding
            x_right = target_index + half_width + right_padding
            visible_days = min(target_index + 1, 120)
            x_left = target_index - visible_days + 1 - half_width
            chart.pricePlot.setXRange(x_left, x_right, padding=0)

        layout.addWidget(chart)
        dialog.exec()

    @staticmethod
    def _locate_target_index(df, target_date: str) -> Optional[int]:
        for i, row in df.iterrows():
            d = row["date"]
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            if ds == target_date:
                return int(i)
        return None

    # ── 辅助 ────────────────────────────────────────────────

    @staticmethod
    def _set_item(table: QtWidgets.QTableWidget, row: int, col: int, text: str):
        item = QtWidgets.QTableWidgetItem(text)
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(row, col, item)

    @staticmethod
    def _set_return_item(table: QtWidgets.QTableWidget, row: int, col: int, value):
        if value is None:
            text = ""
            color = None
        else:
            text = f"{value*100:+.2f}%"
            color = QtGui.QColor("#E74C3C") if value > 0 else QtGui.QColor("#27AE60") if value < 0 else None
        item = QtWidgets.QTableWidgetItem(text)
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        if color is not None:
            item.setForeground(color)
        table.setItem(row, col, item)

    @staticmethod
    def _fmt_score(value) -> str:
        return "" if value is None else f"{value:.0f}"

    @staticmethod
    def _fmt_bool(value) -> str:
        if value is None:
            return ""
        return "✓" if value else "—"

    @staticmethod
    def _set_ic_value(table: QtWidgets.QTableWidget, row: int, col: int, value: float, color: bool):
        """P2-4 IC 表带颜色的数值单元格。color=True 时正绿负红。"""
        text = f"{value:+.4f}"
        item = QtWidgets.QTableWidgetItem(text)
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        if color:
            if value > 0.01:
                item.setForeground(QtGui.QColor("#27AE60"))
            elif value < -0.01:
                item.setForeground(QtGui.QColor("#C0392B"))
        table.setItem(row, col, item)
