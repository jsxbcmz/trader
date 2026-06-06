# screening_analysis/ — 选股分析报告（人读）

## 用途
存放 Agent 对 `screening_raw/` 数据加工后生成的**人工可读分析报告**（.md 格式）。

## 文件类型

### 每日分析报告
- 文件名：`YYYY-MM-DD.md`
- 来源：Agent 分析 `screening_raw/` 数据后生成
- 内容：大盘温度、板块排名、匹配股票列表（含定式/评分/预测方向）、回顾上次预测准确率
- 标题下方必须有 `> 📌 来源:` 标识行（如 `🔄自动选股` 或 `📋混合`）

### 汇总/补充报告
- `YYYY-MM-DD_summary.md` — 某日综合汇总（含额外分析维度）
- `YYYY-MM-DD_v2.md` — 某日第二版分析（版本对比/补充分析）

### 回顾记录
- `manual_review.md` — 手动预测回顾总表（已合并多日回顾）
- `manual_review_YYYY-MM-DD.md` — 某日手动预测回顾

## 已有数据
- 每日报告：2026-05-25 ~ 2026-06-03
- 5/27 有汇总报告（`_summary`）
- 5/29 有 v2 补充对比报告（`_v2`）
- 6/4、6/5 及之后日期暂无分析报告（视 cron 任务是否生成）

## 规则
- 只放 .md 文件
- 文件名严格 `YYYY-MM-DD.md`（可带 `_summary`、`_v2` 等后缀）
- 不放 JSON 原始数据（去 `screening_raw/` 或 `screening_predictions/`）
- 不放自动运行的脚本
- 如需调整文件格式，**先问下我再动手**
