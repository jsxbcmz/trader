# screening_raw/ — 原始扫描结果

## 用途
存放 screen_stocks.py 全量扫描的**原始输出**。这是选股流水线的第一环，所有后续产物（预测、分析报告）都基于此。

## 文件命名
- 文件名：`YYYY-MM-DD.json`
- 禁止使用 `YYYYMMDD` 紧凑格式或其他命名方式

## 最新标准字段格式（v3，6/1 起）

### 顶层字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date` | string | ✅ | 日期，`YYYY-MM-DD` |
| `market_avg` | float | ✅ | 全市场均涨跌幅(%) |
| `is_bearish_day` | bool | ✅ | 是否普跌日 |
| `group_counts` | dict | ✅ | 分组统计：`{limit_up, strong_unsealed, normal}` |
| `results` | array | ✅ | 股票列表 |

### results 中每只股票的字段
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | ✅ | 6位代码 |
| `name` | string | ✅ | 股票名称 |
| `industry` | string | ✅ | 所属行业 |
| `pattern` | string | ✅ | 定式名称，如无则"未知" |
| `score` | float | ✅ | 综合评分 |
| `grade` | string | ✅ | 等级：S/A/B/C/D |
| `summary` | string |  | 定式摘要说明 |
| `group` | string | ✅ | 分组：`limit_up`/`strong_unsealed`/`normal` |
| `is_limit_up` | bool |  | 是否涨停 |
| `limit_up_quality` | string |  | 涨停质量：`strong`/`weak`/`null` |
| `close` | float | ✅ | 收盘价 |
| `day_change` | float | ✅ | 当日涨跌幅(%) |
| `vol` | float |  | 成交额(亿元) |
| `avg_vol_30` | float |  | 30日均成交额 |
| `vol_ratio` | float |  | 量比 |
| `turnover_rate` | float |  | 换手率(%) |
| `sector_penalty` | float |  | 板块扣分 |
| `sector_flags` | array |  | 板块标记 |
| `cum_chg_5d` | float |  | 5日累计涨幅(%) |
| `q_score` | int |  | 通用质量评分 |
| `q_items` | dict |  | 通用质量明细 |
| `m_score` | int |  | MACD环境评分 |
| `m_items` | dict |  | MACD环境明细 |
| `s_score` | float |  | 信号强度评分 |
| `s_items` | dict |  | 信号强度明细 |
| `p_score` | float |  | P3加成评分 |
| `p_items` | dict |  | P3加成明细 |
| `r_penalty` | int |  | 风险扣分 |
| `r_items` | dict |  | 风险扣分明细 |
| `raw_total` | float |  | 裸分(扣分前) |
| `diff` | float |  | MACD DIFF值 |
| `dea` | float |  | MACD DEA值 |
| `hist` | float |  | MACD柱值 |
| `brick_val` | float |  | 砖值 |
| `st_val` | float |  | 短趋值 |
| `ls_val` | float |  | 多空线值 |
| `open` | float |  | 开盘价 |
| `high` | float |  | 最高价 |
| `low` | float |  | 最低价 |
| `prev_close` | float |  | 前收盘价 |
| `source` | string | ✅ | 固定为 `"auto"` |

## 历史数据说明
旧版文件（5/25~5/29）字段少于 v3 标准，属当日脚本未采集，**不做补充**。
字段名差异已对齐：`avg_change→market_avg`，`is_bear_day→is_bearish_day`。

## 硬性规则
- ✅ 文件名严格 `YYYY-MM-DD.json`
- ✅ 只放每日扫描原始 JSON，不放其他类型文件
- ❌ 不放分析报告（.md → `screening_analysis/`）
- ❌ 不放预测结果（→ `screening_predictions/`）
- ❌ 不放手动分析（→ `manual_predictions/`）
- ❌ 不放辅助快照文件（旧版已清理，新版不再产出）
- ❌ 禁止添加/修改字段，除非经过我同意
- ❌ 禁止使用 `YYYYMMDD` 紧凑格式
