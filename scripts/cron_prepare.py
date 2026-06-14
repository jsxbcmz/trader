#!/usr/bin/env python3
"""
cron_prepare.py — 每日选股流水线数据准备脚本

在 cron agent 启动前运行，负责任重耗时的工作：
1. 判断是否为交易日
2. 更新日线数据（update_daily.py）
3. 全市场选股（screen_full.py）
4. 读取 OAMV 活跃市值
5. 输出进度标记 + JSON 摘要给 agent（stdout）
6. 向用户实时推送阶段播报（hermes send）

进度标记格式：=== PROGRESS: {阶段名} [start|done] {详情?} ===
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path("/opt/data/workspace/trader")
DB_PATH = PROJECT / "db" / "market.db"
RAW_DIR = Path("/opt/data/output/screening_raw")
PREDICTIONS_DIR = Path("/opt/data/output/screening_predictions")

# 微信推送目标（用户 DM）
WECHAT_TARGET = "weixin:o9cq804FQN5DsNiL5SUaawQO51AA@im.wechat"

os.chdir(str(PROJECT))


def notify(msg: str):
    """向用户推送实时进度通知"""
    try:
        subprocess.run(
            ["hermes", "send", "--to", WECHAT_TARGET, msg],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass  # 不阻塞流水线

def ts() -> str:
    """返回 HH:MM:SS 时间戳"""
    return datetime.now().strftime("%H:%M:%S")


def get_today() -> str:
    """返回今天日期 YYYY-MM-DD"""
    return datetime.today().strftime("%Y-%m-%d")


def is_weekday() -> bool:
    """判断是否为交易日（周一到周五，不考虑法定假日）"""
    return datetime.today().weekday() < 5


def run_update() -> dict:
    """运行日线数据更新"""
    t0 = time.perf_counter()
    result = {"status": "ok", "updated": 0, "skipped": 0, "errors": 0, "elapsed_seconds": 0}
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "scripts/update_daily.py"],
            capture_output=True, text=True, timeout=3600,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        elapsed = time.perf_counter() - t0
        result["elapsed_seconds"] = round(elapsed, 1)

        output = proc.stdout + proc.stderr
        # Parse summary lines (跨行格式): "已更新:  N 只\n  已跳过:  N 只\n  失败:    N 只"
        import re
        m = re.search(
            r"已更新\s*[:：]?\s*(\d+)\s*只[\s\S]*?跳过\s*[:：]?\s*(\d+)\s*只[\s\S]*?失败\s*[:：]?\s*(\d+)\s*只",
            output, re.DOTALL,
        )
        if m:
            result["updated"] = int(m.group(1))
            result["skipped"] = int(m.group(2))
            result["errors"] = int(m.group(3))
        result["rc"] = proc.returncode
        if proc.returncode != 0:
            result["status"] = "error"
            result["stderr_tail"] = proc.stderr[-500:] if proc.stderr else ""
            print(f"=== PROGRESS: 数据更新 [done] {ts()} ({elapsed:.0f}s) 异常(rc={proc.returncode}) ===")
        else:
            detail = f"已更新{result['updated']}只，跳过{result['skipped']}只，失败{result['errors']}只"
            print(f"=== PROGRESS: 数据更新 [done] {ts()} ({elapsed:.0f}s) {detail} ===")
            notify(f"✅ 数据更新完成（{elapsed:.0f}秒）\n更新{result['updated']}只，跳过{result['skipped']}只，失败{result['errors']}只\n\n🔍 下一步：全市场砖型图选股（约 20-30 分钟）")
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - t0
        result["status"] = "timeout"
        result["elapsed_seconds"] = round(elapsed, 1)
        print(f"=== PROGRESS: 数据更新 [timeout] {ts()} ({elapsed:.0f}s) ===")
        notify(f"⚠️ 数据更新超时（{elapsed:.0f}秒），继续执行后续流程")
    except Exception as e:
        result["status"] = "exception"
        result["error"] = str(e)
        print(f"=== PROGRESS: 数据更新 [error] {ts()} {e} ===")
    return result


def run_screening(date_str: str) -> dict:
    """运行全市场选股"""
    t0 = time.perf_counter()
    result = {"status": "ok", "stock_count": 0, "elapsed_seconds": 0}
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "scripts/screen_full.py", "--date", date_str],
            capture_output=True, text=True, timeout=1200,
        )
        elapsed = time.perf_counter() - t0
        result["elapsed_seconds"] = round(elapsed, 1)
        result["rc"] = proc.returncode

        if proc.returncode != 0:
            result["status"] = "error"
            result["stderr_tail"] = proc.stderr[-500:] if proc.stderr else ""
            print(f"=== PROGRESS: 全市场选股 [error] {ts()} ({elapsed:.0f}s) ===")
            notify(f"❌ 全市场选股失败（返回码 {proc.returncode}）")
            return result

        # Read the raw output file
        raw_file = RAW_DIR / f"{date_str}.json"
        if raw_file.exists():
            with open(raw_file) as f:
                raw_data = json.load(f)
            result["stock_count"] = len(raw_data.get("results", []))
            result["market_avg"] = raw_data.get("market_avg")
            result["is_bearish_day"] = raw_data.get("is_bearish_day")
            result["group_counts"] = raw_data.get("group_counts")
            # Only keep summary of each stock, not full details
            stocks_summary = []
            for s in raw_data.get("results", []):
                stocks_summary.append({
                    "symbol": s["symbol"],
                    "name": s.get("name", ""),
                    "score": s.get("score"),
                    "grade": s.get("grade"),
                    "group": s.get("group"),
                    "pattern": s.get("pattern"),
                    "day_change": s.get("day_change"),
                    "is_limit_up": s.get("is_limit_up"),
                })
            result["stocks"] = stocks_summary
        else:
            result["status"] = "no_output_file"
        detail = f"命中{result['stock_count']}只"
        print(f"=== PROGRESS: 全市场选股 [done] {ts()} ({elapsed:.0f}s) {detail} ===")
        grp = result.get("group_counts", {})
        group_detail = f"涨停{grp.get('limit_up', 0)}只 / 强势封板{grp.get('strong_limit', 0)}只 / 普通{grp.get('normal', 0)}只"
        notify(f"✅ 全市场选股完成（{elapsed:.0f}秒）\n命中 {result['stock_count']} 只\n{group_detail}\n\n📊 市场均涨跌：{result.get('market_avg', 'N/A')}%\n\n🔄 下一步：读取 OAMV + 生成分析报告")
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - t0
        result["status"] = "timeout"
        result["elapsed_seconds"] = round(elapsed, 1)
        print(f"=== PROGRESS: 全市场选股 [timeout] {ts()} ({elapsed:.0f}s) ===")
        notify(f"⚠️ 全市场选股超时（{elapsed:.0f}秒），使用已有结果")
    except Exception as e:
        result["status"] = "exception"
        result["error"] = str(e)
        print(f"=== PROGRESS: 全市场选股 [error] {ts()} {e} ===")
    return result


def get_oamv() -> dict:
    """读取 OAMV 活跃市值"""
    result = {"status": "ok"}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute(
            "SELECT date, close FROM oamv_daily ORDER BY date DESC LIMIT 2"
        )
        rows = cur.fetchall()
        conn.close()

        if len(rows) >= 2:
            result["today_oamv"] = rows[0][1]
            result["yesterday_oamv"] = rows[1][1]
            result["oamv_change_pct"] = round(
                (rows[0][1] - rows[1][1]) / rows[1][1] * 100, 2
            )
        elif len(rows) == 1:
            result["today_oamv"] = rows[0][1]
            result["oamv_change_pct"] = 0
        else:
            result["status"] = "no_data"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


def main():
    print(f"=== PROGRESS: 数据准备阶段 [start] {ts()} ===")
    today = get_today()
    print(f"=== CRON_PREPARE {today} ===\n")

    # Step 0: Date check
    if not is_weekday():
        print(f"=== PROGRESS: 数据准备阶段 [skip] {ts()} 非交易日（周末） ===\n")
        print(json.dumps({
            "status": "skip",
            "reason": "非交易日（周末）",
            "date": today,
        }, ensure_ascii=False))
        return

    # Step 1: Data update
    update_result = run_update()

    # If update failed catastrophically, abort
    if update_result.get("updated", 0) == 0 and update_result.get("errors", 0) > 0:
        print(f"=== PROGRESS: 数据准备阶段 [error] {ts()} 数据更新失败，终止 ===\n")
        print(json.dumps({
            "status": "error",
            "reason": f"数据更新失败",
            "date": today,
            "error": update_result.get("stderr_tail", update_result.get("error", "")),
        }, ensure_ascii=False))
        return

    # Step 2: OAMV
    oamv = get_oamv()
    oamv_msg = ""
    if oamv.get("today_oamv"):
        pct = oamv.get("oamv_change_pct", 0)
        direction = "流入📈" if pct > 0 else "流出📉"
        oamv_msg = f"OAMV 今日{oamv['today_oamv']:.0f}，{direction}（{pct:+.2f}%）"
        print(f"=== PROGRESS: OAMV [{ts()}] 今日{oamv['today_oamv']:.0f} 涨跌{oamv.get('oamv_change_pct', 0):+.2f}% ===\n")

    # Step 3: Screening
    screen_result = run_screening(today)

    # Final summary (single notification to avoid rate limiting)
    update_info = f"数据更新{update_result.get('updated', 0)}只 / 选股{ screen_result.get('stock_count', 0)}只"
    print(f"=== PROGRESS: 数据准备阶段 [done] {ts()} {update_info} ===")

    # Step 4: Output final summary JSON for agent
    output = {
        "status": "ok",
        "date": today,
        "update": {
            "updated": update_result.get("updated", 0),
            "skipped": update_result.get("skipped", 0),
            "errors": update_result.get("errors", 0),
            "elapsed_seconds": update_result.get("elapsed_seconds", 0),
        },
        "oamv": oamv,
        "screening": {
            "market_avg": screen_result.get("market_avg"),
            "is_bearish_day": screen_result.get("is_bearish_day"),
            "group_counts": screen_result.get("group_counts", {}),
            "stock_count": screen_result.get("stock_count", 0),
            "elapsed_seconds": screen_result.get("elapsed_seconds", 0),
            "stocks": screen_result.get("stocks", []),
        },
    }
    print("=== AGENT_INPUT ===")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
