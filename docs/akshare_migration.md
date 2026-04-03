# Tushare → AKShare 数据源迁移开发文档

## 1. 迁移概述

本次改造将项目的股票数据源从 **Tushare** 切换为 **AKShare**，主要变更：

- 移除 Tushare 依赖及其 Token 配置体系
- 新增 AKShare 数据客户端，支持日线和分钟级数据获取
- 简化设置界面（不再需要配置 API Token）

### 为什么选择 AKShare

| 对比项 | Tushare | AKShare |
|--------|---------|---------|
| 费用 | 免费额度有限，分钟线需高积分 | 完全免费 |
| 注册 | 需要注册获取 Token | 无需注册 |
| 分钟线支持 | 需 5000+ 积分 | 免费，支持 1/5/15/30/60 分钟 |
| 维护活跃度 | 活跃 | 非常活跃，GitHub 高星标 |
| 频率限制 | 较严格（450次/分钟） | 较宽松 |

---

## 2. 文件变更清单

### 删除的文件
| 文件 | 说明 |
|------|------|
| `app/tushare_client.py` | 原 Tushare 客户端，已完全移除 |

### 新增的文件
| 文件 | 说明 |
|------|------|
| `app/akshare_client.py` | AKShare 数据客户端 |
| `docs/akshare_migration.md` | 本文档 |

### 修改的文件
| 文件 | 变更内容 |
|------|----------|
| `app/history_updater.py` | 替换 TushareClient → AkshareClient，调整列名映射，移除 RateLimiter |
| `app/services/settings_service.py` | 移除 `tushare_token` 字段和相关方法 |
| `app/components/settings_form.py` | 移除 Token 输入框 |
| `app/pages/market_page.py` | UpdateWorker 不再需要 token 参数，简化更新流程 |
| `app/pages/settings_page.py` | 移除 Token 显示，调整描述文案 |
| `app/main_window.py` | `_request_update_all` 不再传递 token |
| `requirements.txt` | `tushare` → `akshare` |
| `tests/unit/test_settings_service.py` | 移除 tushare_token 相关测试 |

---

## 3. 核心模块说明

### 3.1 AkshareClient (`app/akshare_client.py`)

提供三个主要方法：

#### `fetch_daily(symbol, start_date, end_date)`
- 获取日线数据（前复权）
- 底层调用 `ak.stock_zh_a_hist()`
- 参数 `symbol`：6 位股票代码（如 `"600519"`）
- 参数 `start_date`/`end_date`：格式 `"YYYYMMDD"`
- 返回 DataFrame，列名为中文：`日期, 开盘, 收盘, 最高, 最低, 成交额` 等

#### `fetch_minute(symbol, period)`
- 获取分钟级数据
- 底层调用 `ak.stock_zh_a_hist_min_em()`
- 参数 `period`：可选 `"1"`, `"5"`, `"15"`, `"30"`, `"60"`
- 返回近期分钟 K 线数据

#### `fetch_stock_list()`
- 获取 A 股全量股票列表
- 底层调用 `ak.stock_zh_a_spot_em()`

#### 错误处理
- 所有方法在失败时抛出 `AkshareClientError`
- 若未安装 akshare 包，调用时会提示安装

### 3.2 HistoryUpdater 变更 (`app/history_updater.py`)

主要调整：

1. **客户端替换**：`TushareClient` → `AkshareClient`，默认自动创建实例，无需 Token
2. **列名映射**：原方法 `_map_tushare_daily_to_local` 重命名为 `_map_remote_to_local`
   ```
   AKShare 返回列          →  本地列名
   ─────────────────────────────────
   日期                    →  date
   开盘                    →  open
   收盘                    →  close
   最高                    →  high
   最低                    →  low
   成交额                  →  volume
   ```
3. **移除 RateLimiter**：AKShare 频率限制宽松，无需客户端侧限流
4. **移除 ts_code 依赖**：AKShare 直接使用 6 位 symbol，无需 `ts_code` 转换

### 3.3 AppSettings 变更 (`app/services/settings_service.py`)

`AppSettings` 数据类移除了 `tushare_token` 字段：

```python
# 改造前
@dataclass(frozen=True, slots=True)
class AppSettings:
    tushare_token: str
    min_visible_days: int
    max_visible_days: int
    last_selected_symbol: str = ""

# 改造后
@dataclass(frozen=True, slots=True)
class AppSettings:
    min_visible_days: int
    max_visible_days: int
    last_selected_symbol: str = ""
```

同步移除的方法：
- `get_tushare_token()`
- `validate_settings` 中的 token 校验逻辑

### 3.4 UI 变更

- **设置表单**（`settings_form.py`）：移除 "Tushare Token" 输入框和 `get_token()` / `set_values()` 中的 token 参数
- **看盘页**（`market_page.py`）：`UpdateWorker` 和 `start_update_all()` 不再需要 token 参数
- **设置页**（`settings_page.py`）：描述文案从"维护接口 Token"改为"维护图表显示参数"

---

## 4. AKShare 数据接口参考

### 日线数据
```python
import akshare as ak

# 获取日线数据（前复权）
df = ak.stock_zh_a_hist(
    symbol="600519",
    period="daily",
    start_date="20240101",
    end_date="20240401",
    adjust="qfq",
)
```

返回列：`日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率`

### 分钟数据
```python
# 获取 5 分钟 K 线
df = ak.stock_zh_a_hist_min_em(symbol="600519", period="5")
```

返回列：`时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 最新价`

### 股票列表
```python
# 获取 A 股全量实时行情列表
df = ak.stock_zh_a_spot_em()
```

---

## 5. 依赖安装

```bash
# 移除旧依赖
pip uninstall tushare

# 安装新依赖
pip install akshare
```

或直接：
```bash
pip install -r requirements.txt
```

---

## 6. 注意事项

1. **stocklist.csv 兼容性**：原 CSV 中的 `ts_code` 列仍然保留，用于搜索过滤功能，但不再用于数据拉取
2. **数据目录不变**：日线数据仍存储在 `stock_daily_data/{symbol}.csv`，格式完全兼容
3. **分钟线为新增能力**：`AkshareClient.fetch_minute()` 已实现但尚未集成到 UI 中，后续可按需扩展
4. **网络要求**：AKShare 通过东方财富等公开接口获取数据，需确保网络可访问这些站点
5. **历史数据**：已有的本地日线 CSV 数据完全兼容，无需重新拉取
