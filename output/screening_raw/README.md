# screening_raw/ — 原始扫描结果

## 用途
存放 screen_stocks.py 全量扫描的**原始输出**，以及市场上下文快照数据。

## 文件类型

### 每日扫描结果
- 文件名：`YYYY-MM-DD.json`
- 来源：screen_stocks.py 全量扫描 market.db 中所有股票
- 内容：每只匹配股票的 symbol/name/pattern/score/grade/close/day_change/vol/q_score/q_items 等完整字段
- 单文件约 10-60KB（取决于当日匹配数量）

### 市场上下文快照
- `market_temp_YYYY-MM-DD.json` — 大盘温度数据（涨跌比、均涨跌、板块排名）
- `market_context_YYYY-MM-DD.json` — 市场环境上下文
- `sectors_YYYY-MM-DD.json` — 板块涨跌统计

### 其他
- `screening_detail_YYYY-MM-DD.json` — 详细选股结果（含技术指标明细）

## 规则
- 文件名严格 `YYYY-MM-DD.json` 或 `类型_YYYY-MM-DD.json`
- 不要放分析报告（.md 去 screening_analysis/）
- 不要放预测结果（去 screening_predictions/）
- 不要放手动分析结果（去 manual_predictions/）
