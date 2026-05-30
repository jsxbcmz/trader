# manual_predictions/ — 手动分析预测

## 用途
存放用户**在对话中手动发送股票代码**请求分析后生成的结构化预测结果。

## 与自动选股的区别
- **自动选股**（screening_*）：cron 任务定时运行 screen_stocks.py 全量扫描 → 输出到 `screening_raw/` / `screening_analysis/` / `screening_predictions/`
- **手动分析**（本目录）：用户在对话中直接发 "分析 000767 晋控电力" → Agent 实时分析 → 结果存这里

## 文件格式
- 文件名：`YYYY-MM-DD.json`
- 内容：JSON，每只股票含 symbol/name/pattern/score/grade/prediction 等字段
- **每条股票记录必须含 `"source": "manual"` 字段**，与自动选股严格区分

## 规则
- 只放 JSON 文件
- 文件名严格 `YYYY-MM-DD.json`
- 不要把自动选股的结果放到这里
