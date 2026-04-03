# 选股系统

**目录：** `core/screening/`

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `engine.py` | ~206 | 选股引擎，进程池并行求值 |
| `service.py` | ~290 | 选股服务，缓存层+结果格式化 |
| `cache_models.py` | ~111 | 缓存数据模型 |
| `cache_repository.py` | ~61 | 缓存 JSON 持久化 |
| `error_policy.py` | - | 错误处理策略 |
| `result_models.py` | - | 结果模型 |
| `result_formatter.py` | - | 结果格式化 |

---

## engine.py — ScreeningEngine

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

## service.py — ScreeningService

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
