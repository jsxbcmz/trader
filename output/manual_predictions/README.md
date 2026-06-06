# manual_predictions/ — 手动分析预测

## 用途
存放用户在**对话中手动发送股票代码**请求分析后生成的结构化预测结果。

## 与自动选股的区别

| | 自动选股（screening_*） | 手动分析（本目录） |
|--|------------------------|-------------------|
| 触发方式 | cron 定时任务 | 用户对话中发"分析 000767" |
| 存储位置 | `screening_raw/` + `screening_predictions/` | 本目录 |
| source | `"auto"` | `"manual"` |

## 文件格式
- 文件名：`YYYY-MM-DD.json`
- 内容：JSON，含 date / stock / stocks[]（每只股票含 symbol / name / pattern / score / grade / pred_direction / confidence / note）
- **每条股票记录必须含 `"source": "manual"` 字段**
- **顶层必须有 `"source": "manual"` 字段**

## 已有数据
覆盖日期：2026-05-22 至 2026-06-04
（5/29、6/5 无手动分析记录，属正常情况）

## 规则
- 只存放 `source=manual` 的 JSON 文件
- 文件名严格 `YYYY-MM-DD.json`
- 不要把自动选股的结果放到这里
- 如果将来自动分析流水线生成手动结果，归入此目录
- 如需调整文件格式，**先问下我再动手**
