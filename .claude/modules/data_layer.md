# 数据层

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `core/data/repository.py` | ~50 | 股票数据访问（StockRepository） |
| `core/data/base_json_repository.py` | ~40 | JSON 文件读写基类 |
| `core/data/time_index.py` | ~51 | 时间索引定位 |
| `app/data_loader.py` | ~142 | CSV 读写和规范化 |
| `app/history_updater.py` | ~208 | 历史数据增量更新 |

---

## repository.py — StockRepository(dataclass, slots=True)

文件布局约定：
- `{root}/stocklist.csv` — 股票列表
- `{root}/stock_daily_data/{symbol}.csv` — 日线数据

| 方法 | 说明 |
|------|------|
| `get_stock_list_frame()` | 返回股票列表 DataFrame 副本 |
| `get_stock_infos()` | 转为 `list[StockInfo]`，symbol 补零6位 |
| `get_daily_frame(symbol)` | 读取日线数据（含缓存），返回副本 |
| `normalize_daily_frame(df)` | 标准化日线 DataFrame |
| `get_daily_path(symbol)` | 返回日线 CSV Path |

---

## base_json_repository.py — BaseJsonRepository

| 方法 | 说明 |
|------|------|
| `_read_json()` | 读取 JSON，不存在返回 None |
| `_write_json(data)` | 原子写入（先写 .tmp 再 rename） |

被 `TemplateRepository` 和 `ScreeningCacheRepository` 继承。

---

## time_index.py — 时间索引

**TimeIndexResult(frozen dataclass)：**
- `requested_date` / `actual_date` / `index` / `matched` / `reason`

**`locate_time_index(df, target_date)`** — 精确匹配日期，非交易日 `matched=False`

---

## data_loader.py — CSV 读写

**模块级缓存：** `_daily_data_cache`，最多 200 只股票，超限时清除前 100 条
**常量：** `DAILY_COLUMNS = ["date", "open", "close", "high", "low", "volume"]`

| 函数 | 说明 |
|------|------|
| `normalize_symbol(symbol)` | 补零至 6 位 |
| `load_stock_list(stocklist_csv)` | 读取股票列表 CSV |
| `load_raw_daily_csv(dir, symbol)` | 读取原始日线 CSV |
| `normalize_daily_dataframe(df)` | 标准化（选列→类型→去重→排序） |
| `get_last_trade_date(dir, symbol)` | 本地最新交易日 |
| `save_daily_csv(dir, symbol, df)` | 规范化+原子写入 |
| `load_daily_csv(dir, symbol)` | 读取日线（带缓存） |
| `clear_daily_data_cache()` | 清除内存缓存 |

**注意：** `volume` 字段实际为成交额（万元），展示时 `/1e4` 转亿。

---

## history_updater.py — 历史数据更新

### 数据模型
- `UpdateResult` — 单只更新结果
- `BatchUpdateSummary` — 批量更新汇总

### HistoryUpdater

| 方法 | 说明 |
|------|------|
| `_map_remote_to_local(df_remote)` | AKShare 中文列名 → 本地标准列名 |
| `update_symbol(symbol, end_date, full_refresh)` | 增量更新单只股票 |
| `update_all_symbols(progress_callback, stop_checker)` | 顺序遍历全部股票，逐只更新 |

**依赖：** `app.akshare_client.AkshareClient`, `app.data_loader`

---

## 数据源约束
**唯一数据源：Tushare。** 不得擅自引入 AKShare、baostock、yfinance 等其他数据源，除非用户明确要求更换。

## 数据文件

| 路径 | 说明 |
|------|------|
| `stocklist.csv` | 股票列表（ts_code, symbol, name, area, industry） |
| `stock_daily_data/{symbol}.csv` | 个股日线（date, open, high, low, close, volume） |
| `templates.json` | 选股模板存储 |
| `screening_cache/screening_cache.json` | 选股缓存 |
