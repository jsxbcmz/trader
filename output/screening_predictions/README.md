# screening_predictions/ — 结构化预测数据（自动选股）

## 用途
存放 Agent 对 `screening_raw/` 数据加工后生成的**结构化预测 JSON**，供回顾脚本（review 流水线）消费。

## 文件类型

### 每日预测（自动）
- 文件名：`YYYY-MM-DD.json`
- 来源：Agent 自动分析 `screening_raw/` 中的原始数据
- 内容：JSON，包含 date / stocks[]（每只股票含 symbol / name / score / grade / pattern / expected_direction / confidence / note 等）
- **顶层必须有 `"source": "auto"` 字段**
- **每条股票记录必须含 `"source": "auto"` 字段**

### 回顾结果
- 文件名：`review_YYYY-MM-DD.json`
- 内容：对某日预测的回顾验证结果，内含 `screening_review`（auto）和 `manual_review`（manual）两组

### 空预测
- 当日未匹配到股票时，文件仍保留但 `stocks: []`
- 属正常情况，不做特殊处理

## 命名规则
- 文件名严格 `YYYY-MM-DD.json` 或 `review_YYYY-MM-DD.json`
- 不加 `_manual`、`_v2` 等后缀名（手动分析的数据不在此目录存放）

## 文件格式说明
- 最新标准统一使用 `stocks` key（非 `predictions` key）
- 旧版文件如使用 `predictions` key 已统一迁移到 `stocks`

## 规则
- 只放 `source=auto` 的 JSON 文件
- 不放原始扫描数据（去 `screening_raw/`）
- 不放手动分析结果（去 `manual_predictions/`）
- 不放 .md 分析报告（去 `screening_analysis/`）
- 如需调整文件格式，**先问下我再动手**
