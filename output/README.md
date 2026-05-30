# /opt/data/output/ — 选股分析输出目录

> 所有选股流水线的输出产物统一存放于此。**根目录不放裸文件**，全部归入子目录。

## 子目录

| 目录 | 用途 | 来源 |
|------|------|------|
| `screening_raw/` | 原始扫描结果 + 市场上下文数据 | screen_stocks.py 全量扫描 |
| `screening_analysis/` | 人工可读的分析报告 .md | Agent 对 raw 数据加工后生成 |
| `screening_predictions/` | 结构化预测 JSON | Agent 生成，供回顾脚本消费 |
| `manual_predictions/` | 用户手动分析结果 | 用户对话中直接发股票代码请求分析 |

## 规则

- **禁止**在根目录放任何裸文件
- **禁止**把一次性脚本放在这里（脚本放 `/opt/data/scripts/`）
- **禁止**放与选股无关的文件
- 所有日期文件名统一使用 `YYYY-MM-DD` 格式
- 同一日期同一类型只保留一份文件（取内容最完整的）
