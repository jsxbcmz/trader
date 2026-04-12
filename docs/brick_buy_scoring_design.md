# 砖形图买入评分系统 & 禁止规则 设计文档

## 一、概述

### 背景

当前回测引擎（`core/backtest/engine.py`）在同一交易日内多只股票同时触发选股信号时，按信号表的原始顺序依次买入，直至达到持仓上限（默认3只）。这种"先到先得"的方式没有考虑买入质量的差异。

### 目标

1. **评分系统**：对当日所有触发信号的股票计算买入优先级分数，按分数从高到低排序后再执行买入，确保优先买入质量最高的标的。
2. **禁止规则**：对存在明确风险特征的股票实施硬性否决（veto），即使触发了选股信号也不允许买入。

### 设计原则

- 评分系统**不改变**现有选股逻辑（通达信表达式），只影响同一天多个候选股的买入顺序
- 禁止规则作为**前置过滤器**，在评分之前剔除不合格的候选股
- 所有计算复用现有指标函数（`app/chart_indicators.py`），不引入新的计算公式

---

## 二、数据依赖

各评分项和禁止规则所需的数据列和指标：

| 数据 | 来源 | 字段/函数 |
|------|------|-----------|
| K线数据 | `daily_data` DataFrame | `open`, `high`, `low`, `close` |
| 成交额 | `daily_data` DataFrame | `volume`（单位：万元） |
| 砖形图值 | `compute_brick_indicator()` | `brick` 数组 |
| 短期趋势线 | `compute_zx_short_trend()` | `EMA(EMA(C,10),10)` |
| K线实体长度 | 计算得出 | `abs(close - open)` |

---

## 三、禁止规则（Veto Rules）

禁止规则为**硬性否决**，命中任何一条即直接排除该股票，不参与评分。

### 3.1 巨量绿柱出货（`veto_huge_green_volume`）

**交易逻辑**：当前日期之前10个交易日内，出现了成交量约为近期平均成交量2倍以上的绿色K线（收盘 < 开盘），说明有大资金在出货，短期内不宜入场。

**计算方法**：
```
回看窗口 = 10个交易日
avg_volume = mean(volume[i-30 : i-1])     # 前30日平均成交额（排除当日）
阈值 = avg_volume × 2.0

for j in range(i-10, i):                  # 回看最近10个交易日
    if volume[j] >= 阈值 and close[j] < open[j]:  # 巨量 + 绿柱
        → 触发否决
```

**参数**：
| 参数 | 默认值 | 含义 |
|------|--------|------|
| `veto_lookback` | 10 | 回看交易日数 |
| `veto_volume_ratio` | 2.0 | 巨量判定倍数（相对于30日均量） |
| `veto_avg_window` | 30 | 计算平均成交额的窗口长度 |

### 3.2 砖小柱长（`veto_small_brick_long_body`）

**交易逻辑**：砖形图值较小但K线实体很长，说明砖形指标尚未确认趋势反转，而价格波动已经很大，追入风险高。与评分系统中"砖大柱短"形成反面对照。

**计算方法**：
```
brick_value = brick[i]                                 # 当日砖形图值
body_length = abs(close[i] - open[i])                  # 当日K线实体长度
avg_brick = mean(brick[max(0,i-20) : i+1] 中 > 0 的值)  # 近20日有效砖均值
avg_body = mean(abs(close - open)[i-20 : i+1])          # 近20日实体均值

if brick_value < avg_brick × 0.5 and body_length > avg_body × 1.5:
    → 触发否决
```

**参数**：
| 参数 | 默认值 | 含义 |
|------|--------|------|
| `veto_brick_low_ratio` | 0.5 | 砖值低于均值的比例阈值 |
| `veto_body_high_ratio` | 1.5 | 实体高于均值的比例阈值 |

### 3.3 红绿交替无趋势（`veto_choppy_alternation`）

**交易逻辑**：近期砖形图红绿频繁交替，没有形成明确的趋势方向，当前翻红可能只是横盘震荡中的无效信号。

**计算方法**：
```
回看窗口 = 8个交易日
direction_changes = 0

for j in range(i-7, i+1):
    # 砖形图方向：当日砖值 > 前日砖值 为红，反之为绿
    curr_rising = brick[j] > brick[j-1]
    prev_rising = brick[j-1] > brick[j-2]
    if curr_rising != prev_rising:
        direction_changes += 1

if direction_changes >= 4:   # 8天内变向 ≥ 4次 → 震荡
    → 触发否决
```

**参数**：
| 参数 | 默认值 | 含义 |
|------|--------|------|
| `veto_choppy_window` | 8 | 回看窗口 |
| `veto_choppy_threshold` | 4 | 变向次数阈值 |

---

## 四、评分系统（Scoring System）

评分为加权求和制，各项独立计算后加权汇总。总分越高，买入优先级越高。

### 4.1 砖大柱短（`score_big_brick_small_body`）

**交易逻辑**：大砖块表示砖形指标确认了较强的趋势反转力度，而K线实体短说明当日价格波动不大、还有上涨空间，"转折动能大 + 价格空间足"。

**计算方法**：
```
brick_value = brick[i]
body_length = abs(close[i] - open[i])
price = close[i]

# 标准化：用近20日均值做基准
avg_brick = mean(brick[i-20:i+1] 中 > 0 的值)
avg_body = mean(abs(close - open)[i-20:i+1])

# 砖值相对强度（越大越好）
brick_score = clip((brick_value / avg_brick - 1.0), 0, 2.0)

# 实体相对短度（越短越好）
body_score = clip((1.0 - body_length / avg_body), 0, 1.0)

# 综合得分 = 砖强度 × 实体短度
score = brick_score × body_score × 权重
```

**权重**：30（满分 60）

### 4.2 价格贴近短期趋势线（`score_near_trend_line`）

**交易逻辑**：买入价格刚好在短期趋势线（EMA(EMA(C,10),10)）附近，说明市场平均成本与当前价格接近，盈亏比更好。价格从上方回落到趋势线附近比从下方反弹到趋势线更优。

**计算方法**：
```
trend = EMA(EMA(close, 10), 10)[i]   # 短期趋势线
price = close[i]
deviation = abs(price - trend) / trend   # 偏离度

# 偏离度越小分数越高，完全贴合时满分
if deviation <= 0.02:       # 偏离 ≤ 2%
    score = 1.0
elif deviation <= 0.05:     # 偏离 2%~5%
    score = (0.05 - deviation) / 0.03
else:                       # 偏离 > 5%
    score = 0.0

score = score × 权重
```

**权重**：25（满分 25）

### 4.3 连续绿砖后首根翻红（`score_first_red_after_greens`）

**交易逻辑**：连续多根绿砖（砖值下降）说明空头持续施压，而在此之后的第一根翻红（砖值上升）是趋势反转的最早信号，越早介入收益空间越大。连续绿砖数量越多，反转的意义越大。

**计算方法**：
```
# 当前必须是翻红（砖值上升）
if brick[i] <= brick[i-1]:
    score = 0
    return

# 向前数连续绿砖数量
green_count = 0
j = i - 1
while j >= 1 and brick[j] <= brick[j-1]:
    green_count += 1
    j -= 1

# 必须是首根翻红（前一根是绿的）
if green_count == 0:
    score = 0
    return

# 连续绿砖越多，反转信号越强
if green_count >= 5:
    score = 1.0
elif green_count >= 3:
    score = 0.7
elif green_count >= 2:
    score = 0.4
else:
    score = 0.2

score = score × 权重
```

**权重**：25（满分 25）

### 4.4 空头衰竭 + 巨量红砖反转（`score_bear_exhaustion_reversal`）

**交易逻辑**：前期绿砖不断加大（空头力度增强 → 充分释放），然后绿砖急剧缩小（做空动能衰竭），最后出现一根大红砖（多头接管），形成经典的"衰竭反转"形态。

**计算方法**：
```
# 第一阶段：检测绿砖加大（空头释放）
# 第二阶段：检测绿砖缩小（动能衰竭）
# 第三阶段：检测巨量红砖（反转确认）

# 当前必须是红砖且砖值较大
if brick[i] <= brick[i-1]:
    score = 0
    return

brick_increase = brick[i] - brick[i-1]
avg_brick = mean(brick[i-20:i+1] 中 > 0 的值)
if brick_increase < avg_brick × 0.5:   # 红砖增量不够大
    score = 0
    return

# 向前扫描，检测"先加大后缩小"的绿砖模式
# 找到绿砖缩小阶段（做空动能衰竭）
shrink_count = 0
j = i - 1
while j >= 1 and brick[j] < brick[j-1]:   # 绿砖阶段
    green_delta = brick[j-1] - brick[j]     # 本轮绿砖下跌量
    if j >= 2:
        prev_delta = brick[j-2] - brick[j-1]  # 上轮绿砖下跌量
        if green_delta < prev_delta:           # 下跌量在缩小 → 动能衰竭
            shrink_count += 1
    j -= 1

# 继续向前找绿砖加大阶段（空头释放）
expand_count = 0
while j >= 1 and brick[j] < brick[j-1]:
    green_delta = brick[j-1] - brick[j]
    if j >= 2:
        prev_delta = brick[j-2] - brick[j-1]
        if green_delta > prev_delta:
            expand_count += 1
    j -= 1

# 三个条件都满足才给分
if expand_count >= 1 and shrink_count >= 1:
    score = min(1.0, (expand_count + shrink_count) / 4.0)
else:
    score = 0

score = score × 权重
```

**权重**：20（满分 20）

### 评分汇总

| 评分项 | 默认权重 | 含义 |
|--------|----------|------|
| 砖大柱短 | 30 | 转折动能大 + 上涨空间 |
| 价格贴近趋势线 | 25 | 盈亏比优 |
| 连续绿砖后首根翻红 | 25 | 反转时机早 |
| 空头衰竭反转 | 20 | 形态完整 |

> **权重完全可调**：4项权重均为运行时参数，通过 `buy_scorer_params` 传入即可覆盖默认值。可以将某项权重设为 0 来禁用该评分项，也可以大幅提高某项权重来测试其单独效果。实际使用时按加权总分降序排列，分数相同时按成交额降序（流动性优先）。
>
> 建议的调参方式：配合敏感性分析模块，批量跑多组权重组合对比收益曲线（见第十节）。

---

## 五、架构设计

### 5.1 新增文件

```
core/backtest/buy_scorer.py      # 买入评分器 + 禁止规则
```

### 5.2 类结构

```python
@dataclass
class BuyScoreResult:
    """单只股票的评分结果"""
    symbol: str
    name: str
    total_score: float               # 总分
    vetoed: bool                     # 是否被禁止
    veto_reason: str                 # 禁止原因
    score_details: dict[str, float]  # 各项得分明细

@dataclass
class BrickBuyScorer:
    """砖形图买入评分器"""

    # ── 禁止规则参数 ──
    veto_lookback: int = 10
    veto_volume_ratio: float = 2.0
    veto_avg_window: int = 30
    veto_brick_low_ratio: float = 0.5
    veto_body_high_ratio: float = 1.5
    veto_choppy_window: int = 8
    veto_choppy_threshold: int = 4

    # ── 评分权重 ──
    weight_big_brick_small_body: float = 30.0
    weight_near_trend: float = 25.0
    weight_first_red: float = 25.0
    weight_bear_exhaustion: float = 20.0

    def score(
        self,
        symbol: str,
        name: str,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> BuyScoreResult:
        """计算买入评分（含禁止规则前置检查）"""
        ...

    # ── 禁止规则（私有方法）──
    def _check_veto_huge_green_volume(self, daily_data, index) -> str | None: ...
    def _check_veto_small_brick_long_body(self, daily_data, index, brick) -> str | None: ...
    def _check_veto_choppy_alternation(self, brick, index) -> str | None: ...

    # ── 评分项（私有方法）──
    def _score_big_brick_small_body(self, daily_data, index, brick) -> float: ...
    def _score_near_trend_line(self, daily_data, index) -> float: ...
    def _score_first_red_after_greens(self, brick, index) -> float: ...
    def _score_bear_exhaustion_reversal(self, brick, index) -> float: ...
```

### 5.3 集成到回测引擎

修改 `core/backtest/engine.py` 中步骤 2~3 的逻辑：

```
原流程：
  步骤2: matched_stocks = signal_table[trade_date]
  步骤3: for match in matched_stocks → 按顺序买入

新流程：
  步骤2: matched_stocks = signal_table[trade_date]
  步骤2.5: 对 matched_stocks 执行评分 + 禁止过滤
    ├─ 遍历候选股，调用 scorer.score()
    ├─ 剔除 vetoed = True 的股票
    └─ 按 total_score 降序排序
  步骤3: for match in scored_and_sorted_stocks → 按优先级买入
```

**关键改动点**（`engine.py`）：

```python
# 步骤 2：从信号表获取买入信号
matched_stocks = signal_table.get(trade_date, [])

# 步骤 2.5：评分排序 + 禁止过滤（仅当使用砖形图策略时）
if scorer is not None and matched_stocks:
    scored_results = []
    for match in matched_stocks:
        daily_df = self._get_daily_data(match["symbol"], ...)
        if daily_df is None:
            continue
        result = scorer.score(match["symbol"], match["name"],
                              daily_df, current_index)
        if not result.vetoed:
            scored_results.append((result.total_score, match))

    # 按分数降序排列
    scored_results.sort(key=lambda x: x[0], reverse=True)
    matched_stocks = [item[1] for item in scored_results]

# 步骤 3：执行买入（后续逻辑不变）
```

### 5.4 配置集成

在 `BacktestConfig` 中新增字段：

```python
# 买入评分器名称（空字符串 = 不使用评分）
buy_scorer_name: str = ""
buy_scorer_params: dict = field(default_factory=dict)
```

策略名称与评分器的映射：
- 当 `sell_strategy_name = "brick_chart"` 时，默认启用 `"brick"` 评分器
- 也可通过 `buy_scorer_name` 显式指定或禁用

### 5.5 交易记录扩展

在 `BacktestTradeRecord` 的 `reason` 字段中记录评分信息：

```
原：reason = "选股信号买入"
新：reason = "选股信号买入(评分:78.5 砖大柱短:25.0/趋势线:20.0/首根翻红:18.5/衰竭反转:15.0)"
```

被禁止的股票记录到日志但不生成交易记录。

---

## 六、数据流图

```
选股信号表 (signal_table)
    │
    ▼
当日候选股票列表 [A, B, C, D, E]
    │
    ▼ ─── 禁止规则前置过滤 ───
    │   ├─ A: 近10日巨量绿柱 → 剔除
    │   └─ D: 红绿交替无趋势 → 剔除
    │
    ▼
有效候选 [B, C, E]
    │
    ▼ ─── 评分排序 ───
    │   ├─ C: 总分 85.0 (砖大柱短30 + 趋势线25 + 首根翻红25 + 衰竭5)
    │   ├─ E: 总分 62.0 (砖大柱短20 + 趋势线17 + 首根翻红25 + 衰竭0)
    │   └─ B: 总分 45.0 (砖大柱短15 + 趋势线10 + 首根翻红20 + 衰竭0)
    │
    ▼
按优先级买入: C → E → B (受持仓上限约束)
```

---

## 七、性能考虑

1. **砖形图缓存**：`BrickBuyScorer` 内部维护 `_brick_cache`（与 `BrickChartSellStrategy` 类似），每只股票的砖形图序列只计算一次
2. **趋势线缓存**：同理缓存 `EMA(EMA(C,10),10)` 计算结果
3. **评分时机**：仅在当日有 ≥ 2 只候选股时才执行评分排序（单只候选无需排序，但仍需检查禁止规则）
4. **计算量**：每只候选股的评分是 O(1)（基于预计算的指标数组做索引查找），不增加回测总体复杂度

---

## 八、可调参数汇总

| 分类 | 参数名 | 默认值 | 说明 |
|------|--------|--------|------|
| 禁止 | `veto_lookback` | 10 | 巨量绿柱回看天数 |
| 禁止 | `veto_volume_ratio` | 2.0 | 巨量判定倍数 |
| 禁止 | `veto_avg_window` | 30 | 均量计算窗口 |
| 禁止 | `veto_brick_low_ratio` | 0.5 | 砖小判定阈值 |
| 禁止 | `veto_body_high_ratio` | 1.5 | 柱长判定阈值 |
| 禁止 | `veto_choppy_window` | 8 | 震荡检测窗口 |
| 禁止 | `veto_choppy_threshold` | 4 | 震荡变向次数阈值 |
| 评分 | `weight_big_brick_small_body` | 30.0 | 砖大柱短权重 |
| 评分 | `weight_near_trend` | 25.0 | 趋势线贴近权重 |
| 评分 | `weight_first_red` | 25.0 | 首根翻红权重 |
| 评分 | `weight_bear_exhaustion` | 20.0 | 衰竭反转权重 |

---

## 九、开发任务拆分与优先级

### 依赖关系

```
任务1 (buy_scorer.py) ──┐
                        ├──→ 任务3 (engine.py)
任务2 (models.py) ──────┘       │
                                ├──→ 任务4 (UI 开关+权重)
                                ├──→ 任务5 (结果展示)
                                └──→ 任务6 (敏感性分析)
```

### P0 — 核心逻辑（后续所有任务的基础）

| # | 任务 | 涉及文件 | 说明 | 状态 |
|---|------|----------|------|------|
| 1 | 新建评分器核心逻辑 | `core/backtest/buy_scorer.py`（新建） | `BrickBuyScorer` 类：3条禁止规则 + 4项评分 + 砖形图/趋势线缓存 + 注册表 | done |
| 2 | 配置层扩展 | `core/backtest/models.py` | `BacktestConfig` 新增 `buy_scorer_name` + `buy_scorer_params` | done |
| 3 | 引擎集成 | `core/backtest/engine.py` | `run()` 和 `run_with_signals()` 步骤2~3之间插入 `_score_and_filter()`；brick_chart 策略自动启用评分器；买入记录 reason 附带评分明细 | done |

### P1 — 界面 & 展示

| # | 任务 | 涉及文件 | 说明 | 状态 |
|---|------|----------|------|------|
| 4 | UI 评分开关 + 权重配置 | `app/pages/backtest_page.py` | 砖形图策略下显示评分区域：启用 CheckBox + 4个权重 SpinBox；`_build_config_from_form()` 组装 `buy_scorer_params` | done |
| 5 | 交易明细展示评分 | `app/pages/backtest_page.py` | 原因列自动展示评分明细，鼠标悬停 tooltip 显示完整信息 | done |

### P2 — 调参工具

| # | 任务 | 涉及文件 | 说明 | 状态 |
|---|------|----------|------|------|
| 6 | 敏感性分析支持权重维度 | `core/backtest/sensitivity.py` | 自动判断参数归属（sell_strategy_params vs buy_scorer_params），可将权重作为网格搜索维度 | done |

### 建议执行顺序

1. **任务1 + 任务2**（可并行）→ 核心评分逻辑 + 配置字段
2. **任务3** → 引擎集成，此时可通过代码直接传参验证效果
3. **任务4** → UI 可调权重，用户可在界面上切换权重跑回测
4. **任务5** → 结果中看到评分明细
5. **任务6** → 批量权重对比

### 验证方式

- 完成任务 1~3 后：用现有回测数据运行，对比开启/关闭评分系统的交易记录差异
- 完成任务 4 后：在回测页面界面上调整权重参数，验证 UI → Config → Engine 的完整链路
- 完成任务 6 后：用敏感性分析跑 2 个权重维度的网格，验证输出矩阵

---

## 十、权重调参指南

### 10.1 设计思路

4项评分权重没有"标准答案"，不同市场环境下最优权重组合不同。因此实现上将权重设计为**完全开放的运行时参数**，支持以下调参方式：

### 10.2 通过 `buy_scorer_params` 传入自定义权重

```python
config = BacktestConfig(
    ...
    buy_scorer_name="brick",
    buy_scorer_params={
        # 自定义权重：重点看趋势线和首根翻红，弱化其他两项
        "weight_big_brick_small_body": 10.0,
        "weight_near_trend": 40.0,
        "weight_first_red": 40.0,
        "weight_bear_exhaustion": 10.0,
    },
)
```

### 10.3 预设权重方案（供快速切换）

| 方案名 | 砖大柱短 | 趋势线 | 首根翻红 | 衰竭反转 | 适用场景 |
|--------|----------|--------|----------|----------|----------|
| 均衡（默认） | 30 | 25 | 25 | 20 | 通用 |
| 重动能 | 50 | 10 | 20 | 20 | 强势反弹行情 |
| 重盈亏比 | 15 | 45 | 25 | 15 | 震荡市精选 |
| 重形态 | 15 | 15 | 30 | 40 | 关注完整反转形态 |
| 单因子测试 | 100/0/0/0 | — | — | — | 验证单项有效性 |

### 10.4 配合敏感性分析批量对比

可复用现有的 `core/backtest/sensitivity.py` 敏感性分析框架，将权重组合作为变量维度，批量运行回测并对比：
- 总收益率
- 最大回撤
- 胜率
- 盈亏比

从而找到当前选股公式下的最优权重配置。
