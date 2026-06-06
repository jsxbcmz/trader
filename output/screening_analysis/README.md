# screening_analysis/ — 选股分析报告（人读）

## 用途
存放 Agent 对 `screening_raw/` 数据加工后生成的**人工可读分析报告**（.md 格式）。

## 文件命名
- 每日报告：`YYYY-MM-DD.md`
- 汇总报告：`YYYY-MM-DD_summary.md`
- 补充报告：`YYYY-MM-DD_v2.md`
- 回顾文件：`manual_review.md` 或 `manual_review_YYYY-MM-DD.md`
- 禁止使用 `YYYYMMDD` 紧凑格式

## 最新标准格式要求

### 每日报告（YYYY-MM-DD.md）

#### 标题与来源
```
# 📊 YYYY-MM-DD 砖形图定式选股分析报告
> 📌 来源: 🔄自动选股 (cron定时任务)
```

#### 必须包含的章节

**1. 大盘背景**
表格含：日期、全市场均涨、涨跌比、OAMV数据、普跌日判定

**2. 选股结果**
表格含：排名、代码、名称、评分、等级、分组、涨跌幅、量比、MACD Diff、5日涨幅

**3. 分组统计**
涨停组 / 强势未封板组 / 普通组 数量

**4. 次日预测摘要**
表格含：股票、方向、置信度、逻辑简述

**5. 昨日回顾（如有）**
自动组 + 手动组的正确/错误统计

**6. 选股统计**
S/A/B/C/D 级分布、涨停数、总命中数

## 硬性规则
- ✅ 文件名严格 `YYYY-MM-DD.md`（可带 `_summary`、`_v2` 后缀）
- ✅ 只放 .md 文件
- ✅ 标题下必须有 `> 📌 来源:` 标识行
- ❌ 不放 JSON 数据（→ `screening_raw/` 或 `screening_predictions/`）
- ❌ 不放自动运行的脚本
- ❌ 禁止修改报告结构/章节要求，除非经过我同意
- ❌ 禁止使用 `YYYYMMDD` 紧凑格式
