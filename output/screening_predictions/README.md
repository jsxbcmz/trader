# screening_predictions/ — 结构化预测数据（自动选股）

## 用途
存放 Agent 对 `screening_raw/` 数据加工后生成的**结构化预测 JSON**，供回顾脚本（review 流水线）消费。

## 文件命名
- 每日预测：`YYYY-MM-DD.json`
- 回顾文件：`review_YYYY-MM-DD.json`
- 禁止使用 `YYYYMMDD` 紧凑格式或其他命名方式
- 禁止使用 `_manual`、`_v2` 等后缀（手动分析不在此目录）

## 最新标准字段格式

### 顶层字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date` | string | ✅ | 预测日期，`YYYY-MM-DD` |
| `next_trading_day` | string | ✅ | 下一交易日 |
| `source` | string | ✅ | 固定为 `"auto"` |
| `stocks` | array | ✅ | 股票列表（**必须用此 key 名**） |

### stocks 中每只股票的字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | ✅ | 6位代码 |
| `name` | string | ✅ | 股票名称 |
| `industry` | string |  | 所属行业 |
| `pattern` | string |  | 定式名称 |
| `score` | float | ✅ | 评分 |
| `grade` | string |  | 等级：S/A/B/C/D |
| `group` | string | ✅ | 分组：`limit_up`/`strong_unsealed`/`normal` |
| `is_limit_up` | bool |  | 是否涨停 |
| `limit_up_quality` | string |  | 涨停质量：`strong`/`weak` |
| `close` | float |  | 收盘价 |
| `day_change` | float |  | 当日涨跌幅(%) |
| `vol_ratio` | float |  | 量比 |
| `macd_diff` | float |  | MACD DIFF值 |
| `macd_hist` | float |  | MACD柱值 |
| `brick_val` | float |  | 砖值 |
| `cum_chg_5d` | float |  | 5日累计涨幅(%) |
| `pred_direction` | string | ✅ | 预测方向：`偏多`/`震荡偏多`/`中性`/`中性偏空`/`偏空` |
| `confidence` | string | ✅ | 置信度：`高`/`中高`/`中`/`中低`/`低` |
| `key_risks` | array |  | 风险列表 |
| `detailed_analysis` | string | ✅ | **完整分析文字**：含形态/量价/MACD/支撑阻力/次日方向预判，供回顾使用 |
| `source` | string | ✅ | `"auto"`（自动）或 `"manual"`（手动合并） |

### 回顾文件字段（review_YYYY-MM-DD.json）
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `review_date` | string | ✅ | 回顾执行日期 |
| `prediction_date` | string | ✅ | 被回顾的预测日期 |
| `screening_review` | array | ✅ | 自动选股回顾结果 |
| `manual_review` | array |  | 手动分析回顾结果 |
| `screening_stats` | dict |  | 自动选股统计 |
| `manual_stats` | dict |  | 手动分析统计 |
| `error_analysis` | dict |  | 错误分析 |

## 硬性规则
- ✅ 必须使用 `stocks` key（严禁使用 `predictions`、`results` 等）
- ✅ 每只股票必须有 `source` 字段
- ✅ 每只股票必须有 `detailed_analysis` 字段（重要回顾依据）
- ✅ 必须覆盖 screening_raw 中**全部**股票，禁止加分数门槛过滤
- ✅ 顶层必须有 `"source": "auto"`
- ❌ 不放原始扫描数据（→ `screening_raw/`）
- ❌ 不放手动分析结果（→ `manual_predictions/`）
- ❌ 不放 .md 分析报告（→ `screening_analysis/`）
- ❌ 禁止添加/修改字段，除非经过我同意
- ❌ 禁止使用 `YYYYMMDD` 紧凑格式
