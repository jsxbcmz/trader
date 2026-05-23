# 主板评分系统

**目录：** `core/scoring/` + `app/pages/scoring_page.py`

V4 砖形图评分（`core/screening/brick_pattern/`）的全主板扩展。每日对主板所有股票跑三定式 + V4 评分，落盘 Top 20 候选 + T+1/T+2/T+3 实盘回填，供超短线选股决策。

> 详细设计与开发记录见 [scoring_system_design.md](../../scoring_system_design.md) 和 [scoring_system_dev_tasks.md](../../scoring_system_dev_tasks.md)。

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `core/scoring/__init__.py` | ~35 | 包入口，导出 API |
| `core/scoring/main_board_pool.py` | ~45 | 主板股票池过滤（剔创业/科创/北交/ST）|
| `core/scoring/cross_section.py` | ~160 | **P1-1** 全主板截面分位计算（CSV 缓存）|
| `core/scoring/engine.py` | ~210 | 主板评分调度（**P1-2** 集成截面分位 + 自有 worker）|
| `core/scoring/storage.py` | ~155 | scoring_daily + scoring_picks 读写（JSON，含 P3 bonus）|
| `core/scoring/outcomes.py` | ~170 | T+1/T+2/T+3 三窗口回填（CSV）|
| `core/scoring/factor_health.py` | ~270 | **P2-1/2/3** IC + 单调性 + 月报生成 |
| `core/scoring/regime.py` | ~170 | **P3-1** OAMV 阶段标签（多/空头二分 + 3 日平滑）|
| `app/pages/scoring_page.py` | ~700 | 评分诊断页 UI（含 ScoringWorker + ReportWorker + 因子健康度 tab + 阶段标签）|

## 数据流

```
[ScoringPage "运行今日评分" 按钮]
        ↓ 启动 ScoringWorker（后台线程）
MainBoardPool.list_active()        ← P0-1 主板池过滤
        ↓
MainBoardScoringEngine.score_date(date)  ← P0-2a 包装 BrickPatternEngine
        ↓ 内部对每只票：
        ↓   load 日线 → 计算指标 → check_prerequisites
        ↓   → 三定式检测（含 P0-2c N 型下跌段硬剔除）
        ↓   → V4 评分（specific + common + macd + signal - risk）
        ↓
save_scoring_daily(matches)        ← P0-3 写 JSON
save_scoring_picks(matches, k=20)  ← P0-4 写 JSON
        ↓
OutcomesFiller.fill_for_today(date)  ← P0-5 增量更新 T+1/T+2/T+3
        ↓ 扫 today-1/-2/-3 的 picks
        ↓ 对每只 pick 算 return + is_green
        ↓ 写 CSV（增量）
        ↓
ScoringPage 刷新展示
```

## 核心组件

### main_board_pool.py — MainBoardPool

| 项 | 内容 |
|----|------|
| 过滤规则 | `ts_code` 不以 `30`/`68`/`8`/`4` 开头 + `name` 不含 `ST`/`*ST` |
| 实际效果 | 当前 `stocklist.csv` 已预筛过主板，3055 行全过；保留作防御性过滤 |
| API | `MainBoardPool.from_root(root)`, `.list_active() -> list[StockInfo]` |

### engine.py — MainBoardScoringEngine

| 项 | 内容 |
|----|------|
| 调度模式 | P0 阶段复用 `BrickPatternEngine`；**P1-2** 加入截面分位后改为自有 worker（按 symbol 传 `cs_pcts`）|
| 核心 API | `from_root(root, use_cross_section=True)`；`score_date(target_date) -> BrickPatternResult` |
| 性能 | P0 模式（绝对阈值）~4.3 秒；P1 模式（截面分位）~7.8 秒（多了一次截面计算）|
| `use_cross_section` 开关 | `True`（默认）→ 先跑 `CrossSectionStats.compute_and_save` → 评分时按 symbol 传 `cs_pcts`；`False` → 走原 `BrickPatternEngine.run` 路径（用于 A/B 对照）|
| 数据有效性 | OHLC NaN 由 `load_daily_csv` 的 `dropna` 自然剔除；停牌由 `locate_time_index` 处理；无独立 fail-fast 层 |

### cross_section.py — CrossSectionStats（P1-1）

| 项 | 内容 |
|----|------|
| 目标 | 对全主板每只票算 3 个待归一化因子的原始值 + 分位（0~1）|
| 待归一化因子 | ① 信号日涨幅 `day_change` ② 翻红力度比 `force_ratio` ③ 短趋斜率 `short_trend_slope` |
| 实现 | 多进程并行（worker 内调 `_calc_indicators` 后提取 3 个原始值）；`pd.rank(pct=True)` 算分位 |
| 缓存 | `output/scoring_cross_section/{date}.csv`（扁平 schema，便于 P2 IC 计算多日 `pd.concat`）|
| API | `compute(date) -> DataFrame`，`compute_and_save(date) -> (DataFrame, Path)`；`load_cross_section(root, date)`；`get_symbol_pcts(df, symbol)` 取单只票 3 个分位值 |

### scoring.py P1-2 改造点

`compute_common_quality_score` 加 `cs_pcts: dict | None = None` 参数：

```python
if cs_pcts is not None and "day_change_pct" in cs_pcts:
    items["信号日涨幅"] = _pct_to_score(cs_pcts["day_change_pct"], 8)
else:
    # ... 原绝对阈值分支
```

3 处分支同样模式（信号日涨幅 / 翻红力度比 / 短趋斜率）。

**`_pct_to_score(pct, max_score)` 分箱**：

| 分位 | 分数 |
|------|------|
| ≥ 0.95 | max_score（满分）|
| ≥ 0.80 | 75% |
| ≥ 0.50 | 50% |
| ≥ 0.20 | 25% |
| < 0.20 | 0 |

`pipeline.py` 的 `screen_with_indicators` 也加 `cs_pcts` 透传参数。`detect_*` 函数**不变**。

### detectors.py — N 型下跌段硬剔除（P0-2c 修改点）

[core/screening/brick_pattern/detectors.py:60-62](../../core/screening/brick_pattern/detectors.py#L60)

```python
if _is_in_n_shape_decline(indicators, index):
    return PatternMatchDetail(matched=False,
                              description="处于N型下跌段(非真起跳)")
```

仅在 `detect_n_shape_jump` 内生效，**不影响横盘起跳/上升波段延续**——因为这两个定式的形态特征天然排除了下跌段票。

**回归案例**（已通过必备前提但应被剔除）：

| 票 | 日期 | 形态 |
|----|------|------|
| 600519 贵州茅台 | 2025-11-28 | 下跌中反弹小红砖 |
| 600519 贵州茅台 | 2025-09-29 | 同上 |
| 601318 中国平安 | 2025-09-05 | 同上 |
| 000858 五粮液 | 2025-09-08 | 同上 |
| 000002 万科A | 2025-09-08 | 同上 |

### storage.py — 评分明细 + TopK 存档

| API | 路径 | 格式 |
|-----|------|------|
| `save_scoring_daily / load_scoring_daily` | `output/scoring_daily/{date}.json` | JSON（含嵌套 `items` dict）|
| `save_scoring_picks / load_scoring_picks` | `output/scoring_picks/{date}.json` | JSON（含嵌套 `breakdown` dict + 中文键便于人类阅读）|

**ScoringRecord 字段**：`symbol, name, date, pattern, total_score, grade, specific_score, common_score, macd_score, signal_score, risk_penalty, items, regime`

### outcomes.py — 三窗口回填

| API | 路径 | 格式 |
|-----|------|------|
| `OutcomesFiller.fill_for_today(today)` | `output/scoring_outcomes/{score_date}.csv` | CSV（扁平时序，便于 P2 阶段 IC 计算多日 `pd.concat`）|
| `load_outcomes(root, date)` | 同上 | 返回 `list[OutcomeRecord]` |

**OutcomeRecord 字段**：`symbol, score_date, t1_return, t1_is_green, t2_return, t2_is_green, t3_return, t3_is_green`

**交易日历参考**：用 `000001` (平安银行) 的日线序列（主板长期未停牌）。

**增量更新逻辑**：
- T+1 日：调用 `fill_for_today(T+1)`，写 score_date=T 的 `t1_*` 列
- T+2 日：再次调用，补 `t2_*` 列
- T+3 日：再次调用，补 `t3_*` 列

### scoring_page.py — ScoringPage + ScoringWorker

| 组件 | 职责 |
|------|------|
| `ScoringWorker(QObject)` | 后台线程串联：评分 → 落盘 → 回填，三阶段 `progressChanged` 信号 |
| `ScoringPage(QWidget)` | 顶部工具栏 + Top 20 表格 + 双击弹 K 线 + 底部前 3 日 outcomes 表 |

**注册位置**：[app/main_window.py](../../app/main_window.py) 的 `pageStack` 索引 6，菜单"打开评分诊断页"。

**风格参考**：[app/pages/brick_pattern_page.py](../../app/pages/brick_pattern_page.py) 的双击行 → 弹 `StockChartWidget` 对话框模式。

## 数据落盘清单

| 路径 | 内容 | 频率 |
|------|------|------|
| `output/scoring_daily/{YYYY-MM-DD}.json` | 全部命中票评分明细（含 P3 bonus）| 每日 1 次 |
| `output/scoring_picks/{YYYY-MM-DD}.json` | Top 20 候选 + 中文 breakdown（含战法加分项）| 每日 1 次 |
| `output/scoring_outcomes/{YYYY-MM-DD}.csv` | T+1/T+2/T+3 收益 + 是否绿砖 | T+1 起逐日补 |
| `output/scoring_cross_section/{YYYY-MM-DD}.csv` | 全主板 3 个待归一化因子原始值 + 分位 | 每日 1 次（P1）|
| `output/scoring_regime/{YYYY-MM-DD}.json` | OAMV 阶段标签（bull/bear + tempo + 关键指标）| 每日 1 次（P3）|
| `output/scoring_factor_health/{YYYY-MM}.json` | 月度 IC + 单调性 + 异常告警 | 用户触发（P2-3）|

## P2 因子健康度（compute_ic / compute_monotonicity / generate_monthly_report）

- 数据源：`scoring_daily/*.json` + 实时从日线算 T+N 收益（不依赖 outcomes.csv，覆盖全部命中票而非 Top 20）
- 单日 IC：每个子项 Spearman 秩相关（用 `rank().corr()` 实现，避免 scipy 依赖）
- 跨日聚合：`ic_mean / ic_std / ic_ir / t_stat / n_days`
- 月报：含 topk_summary（三窗口）+ factor_ic（按 t1/t2/t3）+ monotonicity + alerts（无效/不稳因子）

## P3 OAMV 阶段化（regime.py）

| 字段 | 含义 |
|------|------|
| `raw_phase` | `bull` if close >= MA20 else `bear`（原始） |
| `smoothed_phase` | **连续 3 日 raw 同向才确认切换**（实际生效标签）|
| `tempo` | `fast` if slope5 顺方向更陡 else `slow`（四象限预留，MVP 不参与决策）|

ScoringWorker 内调 `RegimeAnalyzer.save_for_date()` 落盘当日 regime + 把 `bull-slow` 等字符串写入 picks 的 `regime` 字段。

## P3 战法加分（compute_p3_bonus）

通过 `ScoreBreakdown.bonus_score / bonus_items` 字段挂载，**不进 base_score**，独立加到 final_score：

| 因子 | 触发条件 | 加分 |
|------|----------|------|
| 红柱比 2/3 | 近 10 砖累计红柱长度 / 绿柱长度 ≥ 2/3 | +2 |
| 地量 | 信号日 volume 在近 60 日 20 分位以下 | +2 |
| DIFF/DEA 刚金叉 | 金叉发生在近 2 个交易日内 | +1 |

第三浪末端扣分 (`_check_third_wave` -8 分) 早已在 `scoring_risk.py` 实现，已被 `compute_risk_penalty` 调用。

## 关键决策记录

| 决策 | 结果 | 理由 |
|------|------|------|
| TopK 的 K | 20 | 从 20 张图挑 2 票（战法"最多持 2 票"），月度 IC 样本量 400~500 |
| 触发时机 | 仅手动按钮 | 不做定时调度，用户自己决定何时跑 |
| 数据有效性前置检查 | 省略（原 P0-2b）| 实测主板 113 万行 volume=0 频率为 0；OHLC NaN 已被 dropna 处理 |
| 硬过滤层 | 取消（原 P0-6）| 4 项全部找到归处：停牌归 P0-2、N 型下跌段下推 detect、黄线之下与必备前提冗余、老庄股推 P3 |
| 存储格式 | JSON + CSV 混合 | 避免引入 pyarrow；含嵌套用 JSON，扁平用 CSV |

## 后续阶段对接点

| 阶段 | 接入位置 |
|------|---------|
| **P1 截面归一化** | `scoring.py` 的"信号日涨幅 / 翻红力度比 / 短趋斜率"改为读 `cross_section.py` 的分位查表 |
| **P2 因子健康度报告** | 读 `scoring_daily/*.json` + `scoring_outcomes/*.csv` 算 IC；ScoringPage 加因子健康度 tab |
| **P3 OAMV 阶段化** | 新增 `regime.py` 写 `output/scoring_regime/{date}.json`；engine.py 按阶段加载不同权重；ScoringPage 顶部 `regime_label` 实际显示阶段 |
