## SQLite 改造设计方案

### 一、改造目标

将项目中**所有** CSV 文件读写操作统一迁移到 SQLite 数据库，实现：

1. 所有行情数据（个股日线、指数日线、行业日线、OAMV）统一存储在一个 `market.db` 中
2. 评分系统输出数据（截面分位、收益追踪）统一存储在一个 `scoring.db` 中
3. 股票列表元数据存储在 `market.db` 中
4. 保留 pandas DataFrame 作为上层接口的返回类型，**上层业务代码无需改动**

---

### 二、现状梳理：所有 CSV 读写点

#### 2.1 行情数据（`stock_daily_data/` 目录）

| 数据类型 | 当前存储 | 读取函数 | 写入函数 | 涉及文件 |
|---------|---------|---------|---------|---------|
| 个股日线 | `{symbol}.csv` × 3058 个 | `load_daily_csv()` / `load_raw_daily_csv()` | `save_daily_csv()` | `core/data/io.py` |
| 指数日线 | `index_{ts_code}.csv` | `load_index_csv()` | `history_updater.update_index()` 内直接写 | `core/data/io.py`, `app/history_updater.py` |
| OAMV 虚拟K线 | `oamv_930903_CSI.csv` | `load_oamv_csv()` | `history_updater._rebuild_oamv_csv()` 内直接写 | `core/data/io.py`, `app/history_updater.py` |
| 行业日线 | `industry_daily_data/{ts_code}.csv` | `load_industry_csv()` | `history_updater.update_industry()` 内直接写 | `core/data/io.py`, `app/history_updater.py` |

#### 2.2 元数据

| 数据类型 | 当前存储 | 读取函数 | 写入函数 | 涉及文件 |
|---------|---------|---------|---------|---------|
| 股票列表 | `stocklist.csv` | `load_stock_list()` | 爬虫脚本手动写 | `core/data/io.py`, `爬数据脚本/fetch_concepts.py`, `爬数据脚本/fetch_industry.py` |

#### 2.3 评分系统输出（`output/` 目录）

| 数据类型 | 当前存储 | 读取函数 | 写入函数 | 涉及文件 |
|---------|---------|---------|---------|---------|
| 截面分位 | `output/scoring_cross_section/{date}.csv` | `load_cross_section()` | `CrossSectionCalculator.save()` | `core/scoring/cross_section.py` |
| 收益追踪 | `output/scoring_outcomes/{date}.csv` | `_load_outcomes()` | `_save_outcomes()` | `core/scoring/outcomes.py` |

#### 2.4 上层调用链（无需改动，仅需确认）

以下模块通过上述函数间接读取数据，改造后**无需修改**：

| 模块 | 调用方式 |
|------|---------|
| `core/data/repository.py` | 通过 `load_daily_csv()` / `load_stock_list()` |
| `app/pages/market_page.py` | 通过 `load_daily_csv()` |
| `app/pages/screening_page.py` | 通过 `load_daily_csv()` |
| `app/pages/brick_pattern_page.py` | 通过 `StockRepository` |
| `app/data_loader.py` | 纯 shim，re-export `core/data/io` |
| `core/scoring/factor_health.py` | 通过 `StockRepository` / `load_daily_csv()` |
| `core/scoring/regime.py` | 通过 `load_oamv_csv()` |

---

### 三、数据库设计

#### 3.1 数据库文件规划

```
项目根目录/
├── db/
│   ├── market.db          # 行情 + 元数据（个股日线、指数、行业、OAMV、股票列表）
│   └── scoring.db         # 评分输出（截面分位、收益追踪）
```

分两个库的理由：
- `market.db` 是核心资产数据，需要稳定持久保存
- `scoring.db` 是可重算的衍生数据，可随时重建

#### 3.2 market.db 表结构

**表 1：stock_daily（个股日线）**

```sql
CREATE TABLE stock_daily (
    symbol        TEXT    NOT NULL,   -- 6位股票代码，如 '000001'
    date          TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    open          REAL    NOT NULL,
    close         REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    volume        REAL,               -- 成交额（万元）
    turnover_rate REAL,               -- 换手率
    PRIMARY KEY (symbol, date)
);

CREATE INDEX idx_stock_daily_date ON stock_daily(date);
CREATE INDEX idx_stock_daily_symbol ON stock_daily(symbol);
```

**表 2：index_daily（指数日线）**

```sql
CREATE TABLE index_daily (
    ts_code       TEXT    NOT NULL,   -- 如 '000001.SH', '930903.CSI'
    date          TEXT    NOT NULL,
    open          REAL    NOT NULL,
    close         REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    volume        REAL,
    turnover_rate REAL,
    PRIMARY KEY (ts_code, date)
);
```

**表 3：oamv_daily（OAMV 虚拟K线）**

```sql
CREATE TABLE oamv_daily (
    date          TEXT    PRIMARY KEY,
    open          REAL    NOT NULL,
    close         REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL
);
```

**表 4：industry_daily（行业日线）**

```sql
CREATE TABLE industry_daily (
    ts_code       TEXT    NOT NULL,
    date          TEXT    NOT NULL,
    open          REAL    NOT NULL,
    close         REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    volume        REAL,
    turnover_rate REAL,
    PRIMARY KEY (ts_code, date)
);
```

**表 5：stock_list（股票列表）**

```sql
CREATE TABLE stock_list (
    symbol        TEXT    PRIMARY KEY,
    ts_code       TEXT,
    name          TEXT,
    area          TEXT,
    industry      TEXT,
    market        TEXT,
    concepts      TEXT,    -- 涉及概念（原 '涉及概念' 列）
    ths_industry  TEXT     -- 涉及行业（原 '涉及行业' 列）
);
```

#### 3.3 scoring.db 表结构

**表 6：cross_section（截面分位）**

```sql
CREATE TABLE cross_section (
    date                  TEXT    NOT NULL,
    symbol                TEXT    NOT NULL,
    day_change            REAL,
    day_change_pct        REAL,
    force_ratio           REAL,
    force_ratio_pct       REAL,
    short_trend_slope     REAL,
    short_trend_slope_pct REAL,
    PRIMARY KEY (date, symbol)
);
```

**表 7：outcomes（收益追踪）**

```sql
CREATE TABLE outcomes (
    score_date    TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    t1_return     REAL,
    t1_is_green   INTEGER,   -- 0/1/NULL
    t2_return     REAL,
    t2_is_green   INTEGER,
    t3_return     REAL,
    t3_is_green   INTEGER,
    PRIMARY KEY (score_date, symbol)
);
```

---

### 四、架构设计

#### 4.1 新增模块

```
core/data/
├── io.py              # 现有 → 重写内部实现，函数签名保持不变
├── database.py        # 【新增】数据库连接管理 + 基础 CRUD
├── migration.py       # 【新增】CSV → SQLite 一次性迁移脚本
└── repository.py      # 现有 → 小幅改造，注入 db 连接
```

#### 4.2 database.py — 核心数据库管理层

```python
"""数据库连接管理与基础操作。"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
import pandas as pd

class DatabaseManager:
    """管理 SQLite 数据库连接，提供统一的读写接口。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """首次运行时创建表结构。"""
        with self.connect() as conn:
            self._create_tables(conn)

    @contextmanager
    def connect(self):
        """获取数据库连接的上下文管理器。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")      # 提升并发读性能
        conn.execute("PRAGMA synchronous=NORMAL")     # 平衡性能与安全
        conn.execute("PRAGMA cache_size=-64000")      # 64MB 缓存
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def read_df(self, sql: str, params=None) -> pd.DataFrame:
        """执行查询，返回 DataFrame。"""
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def write_df(self, df: pd.DataFrame, table: str,
                 if_exists: str = "append") -> int:
        """将 DataFrame 写入表。"""
        with self.connect() as conn:
            return df.to_sql(table, conn, if_exists=if_exists, index=False)

    def execute(self, sql: str, params=None):
        """执行单条 SQL。"""
        with self.connect() as conn:
            conn.execute(sql, params or [])

    def _create_tables(self, conn: sqlite3.Connection):
        """建表（IF NOT EXISTS）。"""
        ...  # 执行上述建表 SQL
```

#### 4.3 io.py 改造策略

**核心原则**：所有函数的**签名和返回类型保持不变**，内部实现从 CSV 读写切换为 SQL 操作。

改造前后对比：

| 函数 | 改造前 | 改造后 |
|------|-------|-------|
| `load_daily_csv(dir, symbol)` | `pd.read_csv(dir/symbol.csv)` | `SELECT * FROM stock_daily WHERE symbol=?` |
| `save_daily_csv(dir, symbol, df)` | 写临时文件 → rename | `INSERT OR REPLACE INTO stock_daily` |
| `load_raw_daily_csv(dir, symbol)` | `pd.read_csv(dir/symbol.csv)` | 同 `load_daily_csv`，跳过归一化 |
| `load_index_csv(dir, ts_code)` | `pd.read_csv(dir/index_xxx.csv)` | `SELECT * FROM index_daily WHERE ts_code=?` |
| `load_oamv_csv(dir)` | `pd.read_csv(dir/oamv_xxx.csv)` | `SELECT * FROM oamv_daily ORDER BY date` |
| `load_industry_csv(dir, ts_code)` | `pd.read_csv(dir/ts_code.csv)` | `SELECT * FROM industry_daily WHERE ts_code=?` |
| `load_stock_list(csv_path)` | `pd.read_csv(stocklist.csv)` | `SELECT * FROM stock_list` |
| `get_last_trade_date(dir, symbol)` | 读 CSV → 取 max(date) | `SELECT MAX(date) FROM stock_daily WHERE symbol=?` |

> 注意：`get_daily_csv_path()` / `get_index_csv_path()` 等路径生成函数将标记为 **deprecated**，迁移完成后移除。

#### 4.4 函数签名变化

当前函数签名中的 `stock_daily_data_dir: Path` 参数将逐步被 `DatabaseManager` 实例替代。但为了**兼容性**，采用以下过渡策略：

```python
# 模块级数据库实例（延迟初始化）
_market_db: DatabaseManager | None = None

def init_database(root: Path):
    """应用启动时调用一次，初始化数据库。"""
    global _market_db
    _market_db = DatabaseManager(root / "db" / "market.db")

def load_daily_csv(stock_daily_data_dir: Path, symbol: str) -> pd.DataFrame:
    """签名保持不变，内部切换到数据库读取。
    stock_daily_data_dir 参数保留但不再使用（兼容期）。
    """
    symbol = normalize_symbol(symbol)
    cache_key = f"db:{symbol}"

    if cache_key in _daily_data_cache:
        return _daily_data_cache[cache_key].copy()

    df = _market_db.read_df(
        "SELECT date, open, high, low, close, volume, turnover_rate "
        "FROM stock_daily WHERE symbol = ? ORDER BY date",
        params=[symbol],
    )

    if df.empty:
        raise FileNotFoundError(f"数据库中找不到 {symbol} 的日线数据")

    df["date"] = pd.to_datetime(df["date"])
    # ... 后续归一化逻辑不变 ...

    _daily_data_cache[cache_key] = df
    return df.copy()
```

---

### 五、改造任务清单

#### 阶段一：基础设施（预计 1 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 1.1 | 新建 `core/data/database.py`，实现 `DatabaseManager` 类 | 新增 |
| 1.2 | 实现 `market.db` 全部建表逻辑（stock_daily / index_daily / oamv_daily / industry_daily / stock_list） | `database.py` |
| 1.3 | 实现 `scoring.db` 全部建表逻辑（cross_section / outcomes） | `database.py` |
| 1.4 | 新建 `core/data/migration.py`，实现 CSV → SQLite 批量迁移脚本 | 新增 |

#### 阶段二：核心 IO 层改造（预计 1.5 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 2.1 | 改造 `load_daily_csv()` → 从 `stock_daily` 表读取 | `core/data/io.py` |
| 2.2 | 改造 `save_daily_csv()` → 写入 `stock_daily` 表 | `core/data/io.py` |
| 2.3 | 改造 `load_raw_daily_csv()` → 从 `stock_daily` 表读取（无归一化） | `core/data/io.py` |
| 2.4 | 改造 `load_index_csv()` → 从 `index_daily` 表读取 | `core/data/io.py` |
| 2.5 | 改造 `load_oamv_csv()` → 从 `oamv_daily` 表读取 | `core/data/io.py` |
| 2.6 | 改造 `load_industry_csv()` → 从 `industry_daily` 表读取 | `core/data/io.py` |
| 2.7 | 改造 `load_stock_list()` → 从 `stock_list` 表读取 | `core/data/io.py` |
| 2.8 | 改造 `get_last_trade_date()` → SQL 查 MAX(date) | `core/data/io.py` |
| 2.9 | 标记路径生成函数为 deprecated | `core/data/io.py` |

#### 阶段三：数据更新层改造（预计 1 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 3.1 | 改造 `update_symbol()` → INSERT OR REPLACE 到 `stock_daily` | `app/history_updater.py` |
| 3.2 | 改造 `update_index()` → INSERT OR REPLACE 到 `index_daily` | `app/history_updater.py` |
| 3.3 | 改造 `_rebuild_oamv_csv()` → 写入 `oamv_daily` 表 | `app/history_updater.py` |
| 3.4 | 改造 `update_industry()` → INSERT OR REPLACE 到 `industry_daily` | `app/history_updater.py` |
| 3.5 | 改造 `_backfill_turnover_rate()` → UPDATE stock_daily SET turnover_rate | `app/history_updater.py` |

#### 阶段四：评分系统输出改造（预计 0.5 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 4.1 | 改造 `CrossSectionCalculator.save()` → 写入 `cross_section` 表 | `core/scoring/cross_section.py` |
| 4.2 | 改造 `load_cross_section()` → 从 `cross_section` 表读取 | `core/scoring/cross_section.py` |
| 4.3 | 改造 `_save_outcomes()` → 写入 `outcomes` 表 | `core/scoring/outcomes.py` |
| 4.4 | 改造 `_load_outcomes()` → 从 `outcomes` 表读取 | `core/scoring/outcomes.py` |

#### 阶段五：爬虫脚本改造（预计 0.5 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 5.1 | 改造 `fetch_concepts.py` → 更新 `stock_list` 表的 concepts 字段 | `爬数据脚本/fetch_concepts.py` |
| 5.2 | 改造 `fetch_industry.py` → 更新 `stock_list` 表的 ths_industry 字段 | `爬数据脚本/fetch_industry.py` |

#### 阶段六：应用启动 + 仓库层适配（预计 0.5 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 6.1 | 应用启动时调用 `init_database(root)` 初始化数据库 | `app/main.py` 或 `run.py` |
| 6.2 | `StockRepository` 增加 `DatabaseManager` 引用 | `core/data/repository.py` |
| 6.3 | `app/data_loader.py` shim 层确认兼容 | `app/data_loader.py` |

#### 阶段七：测试 + 清理（预计 0.5 天）

| 序号 | 任务 | 文件 |
|-----|------|------|
| 7.1 | 更新 `tests/scoring/` 下相关测试用例 | `tests/` |
| 7.2 | 确认全链路可用：启动 → 查看日线 → 更新数据 → 选股评分 | 手动验证 |
| 7.3 | 移除旧 CSV 路径生成函数的 deprecated 标记 | `core/data/io.py` |
| 7.4 | 编写导出工具（可选）：从 SQLite 导出为 CSV 用于调试 | 新增 |

---

### 六、迁移脚本设计（migration.py）

```python
"""一次性迁移：将现有 CSV 数据导入 SQLite。"""

def migrate_all(root: Path):
    """主入口。"""
    migrate_stock_daily(root)       # 3058 个 CSV → stock_daily 表
    migrate_index_daily(root)       # index_*.csv → index_daily 表
    migrate_oamv(root)              # oamv_930903_CSI.csv → oamv_daily 表
    migrate_industry_daily(root)    # industry_daily_data/*.csv → industry_daily 表
    migrate_stock_list(root)        # stocklist.csv → stock_list 表

def migrate_stock_daily(root: Path):
    """批量导入个股日线。
    策略：遍历 stock_daily_data/*.csv，逐文件读取后批量 INSERT。
    预计处理 3058 文件、约 1200 万行，耗时约 2-3 分钟。
    """
    ...
```

迁移脚本需要做到：
1. **幂等**：重复运行不会产生重复数据（使用 INSERT OR REPLACE）
2. **进度反馈**：每 100 只股票打印一次进度
3. **数据校验**：迁移完成后对比行数，确保无遗漏
4. **保留原文件**：迁移后 CSV 不删除，待确认无误后手动清理

---

### 七、关键实现细节

#### 7.1 缓存策略调整

现有的 `_daily_data_cache` 字典缓存机制保留，但缓存键从 `{dir}:{symbol}` 改为 `db:{symbol}`。SQLite 本身有页面缓存，两层缓存可有效减少重复读取开销。

#### 7.2 写入性能优化

批量写入时使用事务包裹 + `executemany`：

```python
def bulk_upsert_daily(self, symbol: str, df: pd.DataFrame):
    with self.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_daily "
            "(symbol, date, open, close, high, low, volume, turnover_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(symbol, row.date, row.open, row.close, row.high,
              row.low, row.volume, row.turnover_rate)
             for row in df.itertuples(index=False)]
        )
```

#### 7.3 WAL 模式

启用 `PRAGMA journal_mode=WAL`，允许读写并发：
- UI 线程读取日线数据时，不会被后台更新线程阻塞
- 适合本项目"后台更新 + 前台浏览"的使用模式

#### 7.4 history_updater 改造要点

现有的 `update_index()` 和 `update_industry()` 内部有大量直接写 CSV 的代码（临时文件 → rename），改造后统一调用 `DatabaseManager.bulk_upsert_*()` 方法，代码量可减少约 40%。

改造前：
```python
# 5 行代码：临时文件 → to_csv → rename
with tempfile.NamedTemporaryFile(...) as tmp:
    normalized.to_csv(tmp.name, index=False)
    temp_path = Path(tmp.name)
temp_path.replace(csv_path)
```

改造后：
```python
# 1 行代码
market_db.bulk_upsert_index(ts_code, normalized)
```

---

### 八、风险与应对

| 风险 | 应对方案 |
|------|---------|
| 迁移过程中数据丢失 | 保留原始 CSV 文件不删除，迁移后对比行数校验 |
| 数据库文件损坏 | WAL 模式 + PRAGMA synchronous=NORMAL 保证崩溃安全；定期备份 db 文件 |
| 多线程并发写入冲突 | SQLite WAL 模式支持单写多读；更新线程内用事务串行写入 |
| 测试用例依赖 CSV | 测试中使用内存数据库 `sqlite3.connect(":memory:")` 替代 |
| 迁移耗时过长 | 使用批量 INSERT（每 500 行一批），预计 3000 只股票 2-3 分钟 |

---

### 九、改造前后对比总结

| 维度 | 改造前（CSV） | 改造后（SQLite） |
|------|-------------|-----------------|
| 文件数量 | 3058 + 若干指数/行业 CSV ≈ 3100+ 文件 | 2 个 db 文件 |
| 磁盘占用 | ~443MB | 预计 ~200-250MB |
| 全市场扫描 | 逐个读取 3000 文件 → 30-60s | 单条 SQL → 2-5s |
| 增量更新 | 读取整个 CSV → 追加 → 重写 | INSERT OR REPLACE → 毫秒级 |
| 数据一致性 | 无事务保护 | ACID 事务保证 |
| 跨票查询 | 需加载全部到内存 | SQL WHERE/JOIN |
| 可调试性 | Excel/文本编辑器直接看 | 需 DB Browser 等工具 |
| 改造工作量 | — | 预计 5-6 天 |
| 上层代码改动 | — | **零改动**（通过 io.py 抽象隔离） |
