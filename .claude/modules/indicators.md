# 指标系统

**目录：** `core/indicators/`

为选股引擎提供所有内置指标函数，输入/输出均为 numpy.ndarray。

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `builtin.py` | ~317 | 内置指标函数实现 |
| `registry.py` | ~75 | 函数注册表 |
| `tdx_compat.py` | ~37 | 通达信兼容适配层 |

---

## builtin.py — 内置指标

| 函数 | 通达信语法 | 说明 |
|------|-----------|------|
| `ma(values, period)` | `MA(X, N)` | 简单移动平均 |
| `ema(values, period)` | `EMA(X, N)` | 指数移动平均 |
| `sma(values, n, m=1)` | `SMA(X, N, M)` | 通达信加权移动平均 |
| `hhv(values, period)` | `HHV(X, N)` | N 周期最高值 |
| `llv(values, period)` | `LLV(X, N)` | N 周期最低值 |
| `ref(values, period=1)` | `REF(X, N)` | 向前引用（时间偏移） |
| `max_series(left, right)` | `MAX(A, B)` | 逐元素取最大值 |
| `min_series(left, right)` | `MIN(A, B)` | 逐元素取最小值 |
| `abs_series(values)` | `ABS(X)` | 绝对值 |
| `std_series(values, period)` | `STD(X, N)` | 滚动标准差 |
| `if_series(cond, true_val, false_val)` | `IF(C, T, F)` | 条件选择 |
| `cross(line1, line2)` | `CROSS(L1, L2)` | 上穿判断 |
| `count(condition, period)` | `COUNT(C, N)` | N 周期满足条件次数 |
| `sum_series(values, period)` | `SUM(X, N)` | N 周期求和 |
| `between(value, low, high)` | `BETWEEN(X, A, B)` | 闭区间 A<=X<=B |
| `range_series(value, low, high)` | `RANGE(X, A, B)` | 开区间 A<X<B |
| `every(condition, period)` | `EVERY(C, N)` | N 周期全部满足 |
| `exist(condition, period)` | `EXIST(C, N)` | N 周期存在满足 |
| `barslast(condition)` | `BARSLAST(C)` | 距上次满足条件周期数 |

---

## registry.py — FunctionSpec + 注册表

### FunctionSpec(frozen dataclass, slots=True)

| 字段 | 说明 |
|------|------|
| `name` | 规范函数名（大写） |
| `func` | 可调用实现 |
| `min_args / max_args` | 参数数量范围 |
| `return_kind` | `"series"` 或 `"multi_series"` |
| `aliases` | 别名 tuple |

### 全局注册表 `FUNCTION_REGISTRY`

静态构建，包含：MA, EMA, SMA, HHV, LLV, REF, MAX, MIN, ABS, STD, IF, CROSS, COUNT, SUM, BETWEEN, RANGE, EVERY, EXIST, BARSLAST, KDJ, ZX_SHORT_TREND, ZX_LONG_SHORT

| 工具函数 | 说明 |
|---------|------|
| `get_function_spec(name)` | 按名称查找，不存在抛 KeyError |
| `is_registered_function(name)` | 是否已注册 |

---

## tdx_compat.py — 兼容适配

将 `app.chart_indicators` 的图表指标函数包装为选股引擎可用形式：
- `kdj` → `chart_indicators.compute_kdj_indicator`
- `zx_short_trend` → `chart_indicators.compute_zx_short_trend`
- `zx_long_short` → `chart_indicators.compute_zx_long_short`
