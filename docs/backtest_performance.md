# 回测系统性能瓶颈分析与优化方案

## 瓶颈总览

按**耗时影响从大到小**排列：

| 排名 | 瓶颈环节 | 位置 | 复杂度 | 影响程度 |
|------|----------|------|--------|----------|
| 1 | 每日全量选股 | `engine.py` 步骤2 | O(天数 × 股票池 × 表达式) | **极高** |
| 2 | 表达式求值冗余计算 | `evaluator.py` | 每只股票每天全量计算 | **高** |
| 3 | DataFrame 多次 copy | `data_loader.py` + `repository.py` | 每次取数据 copy 两次 | **高** |
| 4 | 砖型图序列重复计算 | `sell_strategy.py` | 每只持仓每天重算全量 | **中** |
| 5 | 敏感性分析无选股复用 | `sensitivity.py` | 选股结果重复计算 N 次 | **极高**（组合多时） |
| 6 | 字段值重复类型转换 | `evaluator.py` | 每次 get_field_values 都转换 | **中** |
| 7 | 每日快照序列化开销 | `engine.py` 步骤4 | 每日生成 holdings_detail 字典 | **低** |

---

## 瓶颈 1：每日全量选股（最大瓶颈）

### 问题描述

`engine.py:234-260`，回测主循环的**步骤 2** 每个交易日都调用 `run_fast_for_backtest()`，遍历**整个股票池**执行表达式求值。

```
总计算量 = 交易天数 × 股票池大小 × 单次表达式求值耗时
```

假设回测 2 年（~488 个交易日），股票池 5000 只，总共要做 **244 万次**表达式求值。这是整个回测系统最大的时间消耗来源。

### 当前代码路径

```
engine.run() 主循环
  → 每个交易日
    → screening_engine.run_fast_for_backtest()
      → 遍历 pool.symbols（数千只）
        → 加载/缓存 DataFrame
        → locate_time_index_fast()  ← O(1)，已优化
        → evaluate_at_index()       ← 计算整个时间序列，只取一个值！
```

### 优化方案

#### 方案 A：预计算选股结果表（推荐）

**核心思路**：选股表达式在整个回测期间不变，可以一次性对每只股票计算出整个时间序列的结果，得到一张 `{symbol: {date: bool}}` 的命中表。主循环只需查表，O(1)。

```python
# 预计算阶段（回测开始前）
signal_table: dict[str, dict[str, bool]] = {}
for symbol in pool.symbols:
    df = load_data(symbol)
    result_array = evaluate_expression(expression, context)  # 一次计算整个序列
    # 将布尔数组映射到日期
    signal_table[symbol] = {
        date_str: bool(result_array[i])
        for i, date_str in enumerate(dates)
        if date_str in trading_days_set
    }

# 主循环（每个交易日）
for trade_date in trading_days:
    matched = [s for s in pool.symbols if signal_table.get(s, {}).get(trade_date)]
```

**收益**：将 `交易天数 × 股票池` 次求值降为 `股票池` 次求值（每只股票只算一次全量）。对于 2 年回测，计算量减少 ~488 倍。

**代价**：预计算阶段的内存消耗（`signal_table` 大小）。但每个 entry 只是 bool 值，5000 只 × 488 天 ≈ 2.44M 个 bool，内存开销约 2-3 MB，完全可以接受。

#### 方案 B：增量式表达式求值

对于某些表达式（如包含 EMA、SMA 等递推指标），可以设计增量求值器，每天只计算新增的一行数据。但这需要重构表达式求值引擎，改动较大，建议作为长期优化。

---

## 瓶颈 2：表达式求值冗余计算

### 问题描述

`evaluator.py:117-126`，`evaluate_at_index()` 的实现方式是：

```python
def evaluate_at_index(node, context, index=None):
    result = evaluate_expression(node, context)  # ← 计算整个数组
    target = context.target_index if index is None else index
    return result[target]                         # ← 只取一个值
```

每次调用都先计算整个 DataFrame 长度（可能数千行）的数组运算，再取单个索引。在 `run_fast_for_backtest` 的循环中，同一只股票的表达式会被反复全量计算。

### 具体浪费点

1. **`get_field_values()`**（`evaluator.py:31-49`）：每次调用都执行 `pd.to_numeric(df[col], errors="coerce").to_numpy()`，即使数据完全相同
2. **指标函数**（如 HHV/LLV/SMA/EMA）：每次都计算整个序列
3. **numpy 数组分配**：每次 MathNode/ComparisonNode 都创建新数组

### 优化方案

如果采用瓶颈 1 的方案 A（预计算信号表），此瓶颈自动解决——每只股票只做一次全量计算。

如果不采用方案 A，可以在 `EvaluationContext` 中增加字段值缓存：

```python
@dataclass
class EvaluationContext:
    df: pd.DataFrame
    target_index: int | None = None
    _field_cache: dict[str, np.ndarray] = field(default_factory=dict)

    def get_field_values(self, field_name: str, offset: int = 0) -> np.ndarray:
        cache_key = f"{field_name}:{offset}"
        if cache_key in self._field_cache:
            return self._field_cache[cache_key]
        # ... 计算 ...
        self._field_cache[cache_key] = values
        return values
```

---

## 瓶颈 3：DataFrame 多次 copy

### 问题描述

数据加载路径上存在**双重 copy**：

```
engine._get_daily_data(symbol)
  → repository.get_daily_frame(symbol)        # ← copy 1
    → load_daily_csv(dir, symbol).copy()       # repository.py:44
      → _daily_data_cache[key].copy()          # data_loader.py:110，缓存命中时 copy
```

每次获取一只股票的数据，会做 **2 次 DataFrame.copy()**。回测中数据被缓存在 `daily_data_cache` 后不再重复调用 repository，但首次加载每只股票数据时（可能 5000 只）的双重 copy 开销不可忽视。

### 量化影响

假设平均每只股票 CSV 有 2000 行 × 7 列，单只约 112KB。5000 只股票首次加载时，额外 copy 产生 ~560 MB 的临时内存分配和拷贝（即使很快释放，GC 压力和时间开销仍在）。

### 优化方案

**回测场景下取消 copy**：回测引擎对数据是只读的，不修改 DataFrame，完全可以安全地跳过 copy。

方案 1：为 `StockRepository` 增加一个 `get_daily_frame_readonly()` 方法，不做 copy，回测引擎专用。

方案 2：在回测引擎的 `_get_daily_data()` 中直接调用 `load_daily_csv()` 而非通过 repository，并跳过 copy。

方案 3（最小改动）：给 `load_daily_csv` 添加 `copy=True` 参数，回测时传 `copy=False`。

---

## 瓶颈 4：砖型图卖出策略重复计算

### 问题描述

`sell_strategy.py:129-152`，`BrickChartSellStrategy._calc_brick_series()` 每次调用都从头计算整个收盘价序列的砖型图值。

```python
def should_sell(self, holding, daily_data, current_index):
    brick_values = self._calc_brick_series(daily_data)  # ← 每次重算全量！
    current_brick = brick_values[current_index]
    prev_brick = brick_values[current_index - 1]
```

假设持有 10 只股票，回测 488 天，这个函数会被调用约 4880 次（实际更少因为有买卖），每次都重新计算整个序列的 EMA。

### 额外问题

EMA 计算使用 Python `for` 循环（`sell_strategy.py:149-150`），而非 numpy 向量化或 pandas ewm：

```python
for i in range(1, len(change_pct)):
    ema_values[i] = alpha * change_pct[i] + (1 - alpha) * ema_values[i - 1]
```

### 优化方案

**缓存砖型图序列**：在策略对象中增加缓存，同一只股票只计算一次。

```python
class BrickChartSellStrategy(SellStrategy):
    _brick_cache: dict[str, np.ndarray] = field(default_factory=dict)

    def should_sell(self, holding, daily_data, current_index):
        cache_key = holding.symbol
        if cache_key not in self._brick_cache:
            self._brick_cache[cache_key] = self._calc_brick_series(daily_data)
        brick_values = self._brick_cache[cache_key]
        ...
```

注意：如果 daily_data 的长度会变（数据更新），需要在缓存时记录长度，变化时重算。但回测中数据是静态的，不存在此问题。

---

## 瓶颈 5：敏感性分析无选股复用（组合多时为极高瓶颈）

### 问题描述

`sensitivity.py:97-103`，参数敏感性分析对每组参数都运行一次**完整回测**：

```python
for row_val in row_values:
    for col_val in col_values:
        config.sell_strategy_params = {row_param: row_val, col_param: col_val}
        result = engine.run(config)  # ← 完整回测，包括选股！
```

但敏感性分析改变的是**卖出策略参数**，选股条件完全不变。也就是说，每组参数的步骤 2（选股）结果完全相同，却被重复计算了 `row_values × col_values` 次。

### 量化影响

假设搜索 5×5=25 组参数，选股占回测 80% 的时间，则有 `25 × 80% = 20 倍`的选股计算是纯浪费。

### 优化方案

**两阶段执行**：

1. **第一阶段**：只运行一次选股，生成 `signal_table: dict[str, dict[str, bool]]`（每个交易日哪些股票被选中）
2. **第二阶段**：对每组卖出参数，只运行交易模拟（步骤 0/1/3/4），从 signal_table 读取买入信号

这需要重构 `BacktestEngine.run()` 将选股和交易模拟解耦，或新增一个 `run_with_signals()` 方法。此优化与瓶颈 1 的方案 A 天然兼容。

---

## 瓶颈 6：字段值重复类型转换

### 问题描述

`evaluator.py:31-49`，`EvaluationContext.get_field_values()` 每次调用都执行：

```python
values = pd.to_numeric(self.df[normalized], errors="coerce").to_numpy(dtype=float)
```

在表达式中如果多个节点引用了相同的字段（比如 `CLOSE > REF(CLOSE,1)` 引用了两次 CLOSE），每次都重新做 `pd.to_numeric` 转换。

### 优化方案

已在瓶颈 2 中给出——在 `EvaluationContext` 中加字段缓存。如果采用瓶颈 1 的预计算方案，此问题影响会降低但不会消失（每只股票仍然计算一次全量表达式）。

---

## 瓶颈 7：每日快照 holdings_detail 序列化

### 问题描述

`engine.py:304-314`，每个交易日都生成 `holdings_detail` 列表：

```python
holdings_detail=[
    {
        "symbol": h.symbol, "name": h.name,
        "quantity": h.quantity, "cost_price": h.cost_price,
        "current_price": h.current_price, "pnl_percent": h.pnl_percent,
    }
    for h in holdings.values()
]
```

488 天 × 平均 5 只持仓 = ~2440 个字典对象。虽然单次开销小，但累计起来也有一定影响，且大部分快照的 `holdings_detail` 在结果展示时并不会被使用。

### 优化方案

**懒序列化**：改为在需要时才生成 `holdings_detail`，或者只在最后一天生成。

```python
# 只保留必要的汇总数据
snapshot = DailySnapshot(
    date=trade_date,
    total_assets=total_assets,
    cash=cash,
    holdings_value=holdings_value,
    holdings_count=len(holdings),
    daily_return=daily_return,
    cumulative_return=cumulative_return,
    trades_today=today_trades,
    holdings_detail=[],  # 只在最后一天填充
)
```

---

## 优化优先级建议

按**投入产出比**排序：

| 优先级 | 优化项 | 预估加速倍数 | 实现难度 | 建议 |
|--------|--------|-------------|----------|------|
| P0 | 预计算选股信号表（瓶颈1+2） | **100~500x** | 中 | 必做。回测核心瓶颈 |
| P0 | 敏感性分析选股复用（瓶颈5） | **N倍**（N=参数组合数） | 中 | 必做。与P0联动 |
| P1 | 取消回测路径 DataFrame copy（瓶颈3） | 1.5~2x | 低 | 性价比高，改动小 |
| P1 | 砖型图序列缓存（瓶颈4） | 局部 2~3x | 低 | 改动极小 |
| P2 | 字段值缓存（瓶颈6） | 局部 1.3~1.5x | 低 | 如做了P0则优先级降低 |
| P3 | 快照懒序列化（瓶颈7） | 微量 | 极低 | 可选 |

## 整体优化架构（推荐）

将瓶颈 1、2、5 的优化方案整合为一个统一的**两阶段回测架构**：

```
阶段 1：信号预计算
  遍历股票池（每只股票一次）
    → 加载数据（无 copy）
    → evaluate_expression() 得到布尔数组
    → 映射到日期，写入 signal_table

阶段 2：交易模拟（可被敏感性分析复用）
  遍历交易日
    → 从 signal_table 查表获取买入信号      ← O(1)
    → 执行买卖、更新持仓、记录快照           ← 与现有逻辑相同
```

这一架构改动集中在 `engine.py`，不影响其他模块接口，且能同时解决最关键的三个性能瓶颈。
