#!/bin/bash
# M1/M2 分钟级复盘 —— 每日收盘后定时任务
#
# ⚠️ 同花顺分时接口(last.js)只返回「最新交易日」，错过当天即永久缺失，
#    因此必须每个交易日收盘后当天跑（建议 15:30，确保收盘数据已稳定）。
#
# 流程：
#   1. review_intraday.py  —— 拉当日分时 → 算衍生指标 → 路径裁定 → 落 intraday_review 表 + 回写预测 json
#   2. intraday_accuracy.py —— 用 intraday_verdict 重算真实准确率 + 错误环节归因
#
# 部署（在生产服务器 /opt/data/workspace/trader 上）：
#   chmod +x scripts/daily_intraday_review.sh
#   crontab -e  后加入（周一~周五 15:30 执行，非交易日脚本内会自动跳过）：
#     30 15 * * 1-5 /opt/data/workspace/trader/scripts/daily_intraday_review.sh >> /opt/data/output/logs/intraday_review.log 2>&1

set -euo pipefail

PROJECT_DIR="/opt/data/workspace/trader"
LOG_DIR="/opt/data/output/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"
# 优先用项目 venv，无则回退系统 python3
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    PYTHON="python"
else
    PYTHON="python3"
fi

echo "===== 分钟级复盘开始 $(date '+%Y-%m-%d %H:%M:%S') ====="

# 交易日判断：若 market.db 最新日期不是今天，说明今天非交易日（数据未更新），跳过
TODAY=$(date '+%Y-%m-%d')
DB_MAX_DATE=$("$PYTHON" -c "import sqlite3; print(sqlite3.connect('$PROJECT_DIR/db/market.db').execute('SELECT MAX(date) FROM stock_daily').fetchone()[0])")
if [ "$DB_MAX_DATE" != "$TODAY" ]; then
    echo "[跳过] 今日 $TODAY 非交易日或日线数据未更新（market.db 最新=$DB_MAX_DATE）"
    echo "===== 结束 $(date '+%Y-%m-%d %H:%M:%S') ====="
    exit 0
fi

# 1. 复盘落库（默认取最近一个有次日数据的预测日）
echo "--- 步骤1: review_intraday ---"
"$PYTHON" scripts/review_intraday.py

# 2. 真实准确率统计
echo "--- 步骤2: intraday_accuracy ---"
"$PYTHON" scripts/intraday_accuracy.py

echo "===== 结束 $(date '+%Y-%m-%d %H:%M:%S') ====="
