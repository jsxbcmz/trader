# /opt/data/output/ — 选股分析输出目录

所有选股流水线的输出产物统一存放于此。**根目录不放任何裸文件**，全部归入子目录。

## 子目录

| 目录 | 用途 | 数据来源 |
|------|------|----------|
| `screening_raw/` | 原始扫描结果（机器读） | screen_stocks.py 全量扫描 |
| `screening_predictions/` | 自动预测 JSON（机器读） | Agent 加工 raw 后生成，供回顾脚本消费 |
| `screening_analysis/` | 分析报告（人读） | Agent 生成的 .md 报告 |
| `manual_predictions/` | 用户手动分析结果 | 对话中发股票代码请求分析 |

## 命名规则

- 所有日期文件名统一使用 `YYYY-MM-DD` 格式
- 同一日期同一类型只保留一份文件（取内容最完整的）
- 文件名不含紧凑格式（`YYYYMMDD` 是错误格式）或中文字符

## 数据字段规则

- 每条股票记录必须含 `source` 字段：`"auto"`（自动选股）或 `"manual"`（手动分析）
- 自动选股预测 JSON 的顶层必须有 `"source": "auto"`
- 手动预测 JSON 的顶层必须有 `"source": "manual"`
- `.md` 报告文件标题下方必须有 `> 📌 来源:` 标识行

## 文件归属规则

| source 值 | 存放目录 |
|-----------|----------|
| `auto` | `screening_predictions/`（预测）或 `screening_raw/`（原始数据） |
| `manual` | `manual_predictions/` |
| review 结果 | `screening_predictions/`（JSON）或 `screening_analysis/`（.md） |

## 禁止事项

- **禁止**在根目录放任何裸文件
- **禁止**把一次性脚本放在这里（脚本放 `/opt/data/scripts/`）
- **禁止**放与选股无关的文件
- **禁止**把 `source=manual` 的数据放到 `screening_predictions/` 下
- **禁止**把 `source=auto` 的数据放到 `manual_predictions/` 下

## 更新文件格式

如需调整文件格式、字段结构或命名规则，**先问下我再动手**，确认后再改。
