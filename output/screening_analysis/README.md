# screening_analysis/ — 选股分析报告

## 用途
存放 Agent 对 screening_raw 数据加工后生成的**人工可读分析报告** (.md)。

## 文件类型

### 每日分析报告
- 文件名：`YYYY-MM-DD.md`
- 内容：大盘温度、板块排名、匹配股票列表（含定式/评分/预测方向）、回顾上次预测准确率
- 标题下方必须有 `> 📌 来源: 🔄自动选股` 或 `📋混合` 标识行
- 这是给人看的最终报告

### 汇总报告
- `YYYY-MM-DD_summary.md` — 某日综合汇总（含额外分析维度）

### 回顾记录
- `manual_review.md` — 手动预测回顾总表
- `manual_review_YYYY-MM-DD.md` — 某日手动预测回顾

## 规则
- 只放 .md 文件
- 文件名严格 `YYYY-MM-DD.md`
- 不要放 JSON 原始数据（去 screening_raw/ 或 screening_predictions/）
- 不要放手动的个股分析（预测结果去 manual_predictions/，回顾可放这里）
