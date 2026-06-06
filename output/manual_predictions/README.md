# manual_predictions/ — 手动分析预测

## 用途
存放用户在**对话中手动发送股票代码**请求分析后生成的结构化预测结果。

## 与自动选股的区别
| | 自动选股（screening_*） | 手动分析（本目录） |
|--|------------------------|-------------------|
| 触发方式 | cron 定时任务 | 用户对话中发"分析 000767" |
| 存储位置 | `screening_raw/` + `screening_predictions/` | 本目录 |
| `source` | `"auto"` | `"manual"` |

## 文件命名
- 文件名：`YYYY-MM-DD.json`
- 禁止使用 `YYYYMMDD` 紧凑格式
- 禁止使用 `_manual` 后缀

## 最新标准字段格式

### 顶层字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date` | string | ✅ | 分析日期，`YYYY-MM-DD` |
| `analyzed_at` | string |  | 分析时间 |
| `next_trading_day` | string | ✅ | 下一交易日 |
| `source` | string | ✅ | 固定为 `"manual"` |
| `stocks` | array | ✅ | 股票列表 |

### stocks 中每只股票的字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | ✅ | 6位代码 |
| `name` | string | ✅ | 股票名称 |
| `industry` | string |  | 所属行业 |
| `pattern` | string |  | 定式/形态描述 |
| `score` | float |  | 评分（如有） |
| `grade` | string |  | 等级 |
| `close` | float |  | 分析时收盘价 |
| `day_change` | float |  | 当日涨跌幅(%) |
| `vol_ratio` | float |  | 量比 |
| `macd_status` | string |  | MACD状态描述 |
| `ma_status` | string |  | 均线状态描述 |
| `pred_direction` | string | ✅ | 预测方向：`偏多`/`震荡偏多`/`中性`/`中性偏空`/`偏空` |
| `confidence` | string | ✅ | 置信度：`高`/`中高`/`中`/`中低`/`低` |
| `key_risks` | array |  | 风险列表 |
| `detailed_analysis` | string | ✅ | 完整分析文字 |
| `source` | string | ✅ | 固定为 `"manual"` |

## 硬性规则
- ✅ 文件名严格 `YYYY-MM-DD.json`
- ✅ 只放 `source=manual` 的 JSON 文件
- ✅ 每只股票必须有 `source` 字段（值为 `"manual"`）
- ✅ 每只股票必须有 `detailed_analysis` 字段
- ❌ 不要把自动选股的结果放这里
- ❌ 禁止添加/修改字段，除非经过我同意
- ❌ 禁止使用 `YYYYMMDD` 紧凑格式

## 已有数据
覆盖日期：2026-05-22 至 2026-06-04
（5/29、6/5 无手动分析记录）
