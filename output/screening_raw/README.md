# screening_raw/ — 原始扫描结果

## 用途
存放 screen_stocks.py 全量扫描的**原始输出**。这是选股流水线的第一环，所有后续产物（预测、分析报告）都基于此。

## 文件类型

### 每日扫描结果
- 文件名：`YYYY-MM-DD.json`
- 来源：screen_stocks.py 全量扫描 market.db 中所有股票
- 每条股票记录必须含 `"source": "auto"` 字段

### 文件格式说明

因扫描脚本版本升级，不同日期的文件格式略有差异：

| 日期 | 顶层结构 | 说明 |
|------|---------|------|
| 5/25 | `{date, results}` | 旧版（采集字段最少） |
| 5/26 | `{date, market_avg, is_bearish_day, results, ...}` | 旧版，有市场温度 |
| 5/27 | `{date, results, total_scanned, total_matched}` | 旧版，有扫描量统计 |
| 5/28 | `{date, results}` | 旧版最简 |
| 5/29 | `{date, market_avg, is_bearish_day, group_counts, results}` | 已对齐 v3 格式 |
| 6/1 起 | `{date, market_avg, is_bearish_day, group_counts, results}` | **当前标准（v3）** |

旧版文件缺失的字段（如 `sector_penalty`、`sector_flags` 等）属数据未采集，不做补充。

### results 通用字段（按出现顺序）
`symbol`, `name`, `industry`, `pattern`, `score`, `grade`, `close`, `day_change`, `vol`, `source`

### v3 格式新增字段
`sector_penalty`, `sector_flags`, `limit_up_quality`, `cum_chg_5d`, `group`, 及各维度评分明细

## 历史遗留（已清理）
- ~~`market_context_*`、`market_temp_*`、`sectors_*`、`screening_detail_*`~~ — 旧版辅助快照，已删除

## 规则
- 只存放每日扫描的原始 JSON 数据
- 文件名严格 `YYYY-MM-DD.json`
- 不放分析报告（.md 去 `screening_analysis/`）
- 不放预测结果（去 `screening_predictions/`）
- 不放手动分析结果（去 `manual_predictions/`）
- 不存放一次性辅助文件（旧版已清理，新版不再产出）
- 如需调整文件格式，**先问下我再动手**
