# /opt/data/output/ — 选股分析输出目录

所有选股流水线的输出产物统一存放于此。**根目录不放任何裸文件**，全部归入子目录。

> **关联项目**：此目录是 `/opt/data/workspace/trader/`（StockViewer 选股项目）的正式 output 目录。
> 项目内脚本 `scripts/screen_full.py` 写 `screening_raw/`，`scripts/review_intraday.py` 读 `screening_predictions/`，
> 流水线 cron job 也统一指向此处。不属于 trader 选股体系的数据**严禁写入**。

## 子目录

| 目录 | 用途 | 数据来源 |
|------|------|----------|
| `screening_raw/` | 原始扫描结果（机器读） | screen_stocks.py 全量扫描 |
| `screening_predictions/` | 自动预测 JSON（机器读） | Agent 加工 raw 后生成，供回顾脚本消费 |
| `screening_analysis/` | 分析报告（人读） | Agent 生成的 .md 报告 |
| `manual_predictions/` | 用户手动分析结果 | 对话中发股票代码请求分析 |
| `weekly_review/` | 周度回顾分析 | Copilot 对话式生成分析报告 |

## 全局硬性规则

### 命名
- 所有日期文件名统一使用 `YYYY-MM-DD` 格式
- **禁止**使用 `YYYYMMDD` 紧凑格式

### 数据字段
- `source` 字段：`"auto"`（自动选股）或 `"manual"`（手动分析）
- 每只股票记录**必须**有 `source` 字段
- 每只股票**必须**有 `detailed_analysis` 字段（完整分析文字）

### 文件归属
| source 值 | 存放目录 |
|-----------|----------|
| `auto` | `screening_predictions/`（预测）或 `screening_raw/`（原始数据） |
| `manual` | `manual_predictions/` |
| review 结果 | `screening_predictions/`（JSON）或 `screening_analysis/`（.md） |

### 禁止行为
- **禁止**在根目录放任何裸文件
- **禁止**把一次性脚本放在这里（脚本放 `/opt/data/scripts/`）
- **禁止**放与选股无关的文件
- **禁止**混放 source=auto 和 source=manual 的数据
- **禁止**添加/修改任何文件的字段结构，除非经过我同意
- **禁止**使用 `YYYYMMDD` 紧凑格式

### 字段修改规则
如需新增、修改或删除任何目录下的字段定义，**先问过我**，确认后再改。不允许擅自加字段或改格式。
