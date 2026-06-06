# /output/weekly_review/ — 周度回顾分析目录

每周对砖形图自动选股评分系统和预测分析能力进行回顾总结，持续迭代优化。

## 文件说明

| 文件 | 用途 | 更新频率 |
|------|------|----------|
| `DESIGN.md` | 回顾分析系统的设计开发文档 | 按需更新 |
| `EVOLUTION.md` | 持续迭代日志，累积记录每周优化方向和反思 | 每周追加 |
| `YYYY-WXX.md` | 某周的详细回顾总结（如 `2026-W23.md`） | 每周一份 |

## 命名规范

- 周文件使用 ISO 周编号：`YYYY-WXX`（如 `2026-W23`）
- 与 output 其他目录保持一致，日期相关内容使用 `YYYY-MM-DD` 格式

## 数据来源

回顾分析基于以下 4 个数据源目录（只读，不修改原始数据）：

| 数据源 | 路径 | 校验要求 |
|--------|------|----------|
| 原始扫描结果 | `screening_raw/{date}.json` | 必须齐全 |
| 自动预测 | `screening_predictions/{date}.json` | 必须齐全 |
| 手动预测 | `manual_predictions/{date}.json` | 允许部分缺失 |
| 分析报告 | `screening_analysis/{date}.md` | 允许部分缺失 |

## 生成方式

通过 Copilot 对话生成，无中间脚本。详见 `DESIGN.md` 第六章。
