# 选股系统

**目录：** `core/screening/`

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `engine.py` | ~209 | 通达信表达式选股引擎，进程池并行求值 |
| `service.py` | ~290 | 选股服务，缓存层+结果格式化 |
| `brick_pattern_engine.py` | ~926 | 砖形图定式选股引擎（独立于 TDX 表达式） |
| `brick_pattern_service.py` | ~85 | 砖形图定式选股服务 |
| `cache_models.py` | ~111 | 缓存数据模型 |
| `cache_repository.py` | ~61 | 缓存 JSON 持久化 |
| `error_policy.py` | ~23 | 错误处理策略（ignore/raise） |
| `result_models.py` | ~59 | 单次运行结果模型 + 调试信息构建 |
| `result_formatter.py` | ~31 | 结果格式化（摘要/命中列表/错误列表） |

---

## engine.py — ScreeningEngine（通达信表达式）

### 模块级函数
`_screen_single_stock(args: tuple) -> dict` — 进程池工作函数，在子进程内独立创建 `StockRepository` 并求值

### ScreeningEngine(dataclass, slots=True)

| 字段 | 说明 |
|------|------|
| `repository` | StockRepository |
| `stock_pool_manager` | StockPoolManager |
| `error_policy` | `"ignore"` 或 `"raise"` |
| `progress_interval` | 进度回调间隔，默认 20 |
| `max_workers` | 进程数，默认 CPU-1 |

| 方法 | 说明 |
|------|------|
| `from_root(root)` | 类方法工厂 |
| `run(request, progress_callback, cancelled_fn)` | 解析条件 → pickle → ProcessPoolExecutor 并行 → 聚合 ScreeningResult |

### 关键设计
- 表达式只解析一次（主进程），序列化后通过 pickle 传入子进程
- 支持中途取消（`cancelled_fn`）

---

## brick_pattern_engine.py — BrickPatternEngine（砖形图定式）

独立于 TDX 表达式系统，直接用 Python/NumPy 实现砖形图交易定式规则。

### 三种交易定式

| 定式 | 函数 | 说明 |
|------|------|------|
| N型起跳 | `detect_n_shape_jump()` | 近30日N型结构（上涨→回调→再上涨），配合 KDJ |
| 横盘起跳 | `detect_sideways_jump()` | 3~10天横盘后突破，涨幅 ≥ 横盘期间2倍 |
| 上升波段延续 | `detect_uptrend_continue()` | 连续≥3红砖后1~2绿砖再翻红 |

### 必备前提 (`check_prerequisites`)
1. 砖形图绿转红（REF(AA,1)=0 AND AA=1）
2. 差值>0 且力度达标（变化量绝对值 / 前日 ≥ 0.5）
3. 短趋线 > 多空线

### 风险过滤规则（3条）

| 规则 | 函数 | 说明 |
|------|------|------|
| 一字板跌停 | `filter_limit_down()` | 前10日内不能有一字板跌停 |
| 横盘时间过长 | `filter_long_sideways()` | 仅对横盘起跳，横盘≤10天 |
| 放量大阴线 | `filter_heavy_volume_drop()` | 前10日不能有放量大阴线（含豁免条件） |

### 核心函数

| 函数 | 说明 |
|------|------|
| `_calc_indicators(df)` | 一次性计算所有指标序列 |
| `screen_single_stock(df, index, ...)` | 单只股票完整检测（前提→定式→风险） |
| `screen_with_indicators(indicators, index, ...)` | 基于预算指标检测（回测优化，避免重复计算） |

### BrickPatternEngine(dataclass)

| 字段 | 说明 |
|------|------|
| `repository` | StockRepository |
| `stock_pool_manager` | StockPoolManager |
| `progress_interval` | 进度回调间隔 |
| `max_workers` | 进程数 |

| 方法 | 说明 |
|------|------|
| `from_root(root)` | 类方法工厂 |
| `run(request, progress_callback, cancelled_fn)` | ProcessPoolExecutor 并行选股 → BrickPatternResult |

---

## brick_pattern_service.py — BrickPatternService

| 方法 | 说明 |
|------|------|
| `from_root(root)` | 类方法工厂 |
| `screen(request, ...)` | 执行选股 |
| `screen_with_summary(request, ...)` | 执行+格式化结果 |

辅助函数：`format_result_summary()`, `format_match_lines()`, `format_filtered_lines()`

---

## 砖形图数据模型 (`core/models/brick_pattern.py`)

| 类 | 说明 |
|----|------|
| `PatternType(Enum)` | N_SHAPE_JUMP / SIDEWAYS_JUMP / UPTREND_CONTINUE |
| `RiskFilterType(Enum)` | LIMIT_DOWN / LONG_SIDEWAYS / HEAVY_VOLUME_DROP |
| `PatternMatchDetail` | 单个定式匹配详情（类型/是否匹配/评分/描述） |
| `RiskFilterDetail` | 单条风险过滤结果（类型/是否触发/描述） |
| `BrickPatternMatch` | 单只股票完整匹配结果 |
| `BrickPatternRequest` | 选股请求（日期/股票池/启用定式/价格限制） |
| `BrickPatternResult` | 选股结果汇总（命中数/风险过滤数/错误数） |

---

## service.py — ScreeningService（通达信表达式）

### ScreeningService(dataclass, slots=True)

| 方法 | 说明 |
|------|------|
| `from_root(root)` | 类方法工厂 |
| `screen(request, ...)` | 直接调用引擎，无缓存 |
| `screen_with_summary(request, ...)` | 执行+格式化结果 |
| `screen_with_cache(request, ...)` | 带缓存的完整流程 |

### screen_with_cache 流程

```
1. 计算 tdx_source SHA-256 哈希
2. 查缓存（target_date + template_id + hash）
3. 缓存命中且已完成 → 直接重建返回（cache_hit: True）
4. 缓存命中但已中断 → 计算剩余股票 → 增量运行 → 合并 → 更新缓存
5. 无缓存 → 全量执行 → 写入缓存
```

---

## cache_models.py — ScreeningCacheEntry

| 字段 | 说明 |
|------|------|
| `target_date / template_id / tdx_source_hash` | 缓存三键 |
| `status` | `"completed"` 或 `"interrupted"` |
| `total / processed_count` | 总数/已处理（断点偏移量） |
| `matched_symbols` | `[{symbol, name}]` 仅存命中 |
| `error_count` | 错误数量 |

**工具函数：** `compute_tdx_source_hash(tdx_source)` — SHA-256

---

## cache_repository.py — ScreeningCacheRepository

继承 `BaseJsonRepository`，存储路径 `{root}/screening_cache/screening_cache.json`

| 方法 | 说明 |
|------|------|
| `find(target_date, template_id, tdx_source_hash)` | 按三键查找 |
| `upsert(entry)` | 更新或追加 |
