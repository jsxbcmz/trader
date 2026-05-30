# screening_predictions/ — 结构化预测数据

## 用途
存放 Agent 生成的**结构化预测 JSON**，供回顾脚本（review_and_predict.py 等）消费。

## 文件类型

### 每日预测
- 文件名：`YYYY-MM-DD.json`
- 内容：JSON，包含 date/market_bg/stocks[]（每只股票含 symbol/name/score/grade/pattern/pred_direction/confidence/note）
- **每条股票记录必须含 `"source": "auto"` 字段**

### 回顾结果
- `review_YYYY-MM-DD.json` — 某日对前一日预测的回顾验证结果
- 内含 `screening_review`（source="auto"）和 `manual_review`（source="manual"）两组

## 与 screening_analysis 的关系
- `screening_analysis/` 的 .md 报告是人看的
- 本目录的 .json 是机器读的（同一天两者描述的是同一批股票，格式不同）

## 规则
- 只放 JSON 文件
- 文件名严格 `YYYY-MM-DD.json` 或 `review_YYYY-MM-DD.json`
- 不要放原始扫描数据（去 screening_raw/）
- 不要放 .md 分析报告（去 screening_analysis/）
