"""B1 战法回测脚本

扫描 stocklist.csv 中所有股票，在指定区间内检测 B1 信号，
记录信号日后 T+5 / T+10 的收益率，统计胜率与盈亏比。

注意：市值过滤（MVOK）已由 stocklist.csv 股票池管控，公式内不再重复过滤。
B1 信号计算逻辑统一来自 ``core.strategy.builtin.b1_strategy.compute_b1_signals``。
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.data.repository import StockRepository
from core.strategy.builtin.b1_strategy import MIN_HISTORY_BARS, compute_b1_signals

# ============================================================
# 回测参数
# ============================================================
START_DATE = "2025-01-01"
END_DATE = "2026-04-30"


# ============================================================
# 单只股票处理（供并行调用）
# ============================================================

def _process_single_stock(args: tuple) -> dict:
    """并行 worker：对单只股票计算 B1 信号并收集命中记录。"""
    root_str, symbol, name, start_date, end_date = args

    try:
        repo = StockRepository(Path(root_str))
        df = repo.get_daily_frame(symbol)
    except Exception as exc:
        return {"records": [], "error": True}

    if df is None or len(df) < MIN_HISTORY_BARS:
        return {"records": [], "error": False}

    try:
        signals = compute_b1_signals(df)
    except Exception:
        return {"records": [], "error": True}

    date_strs = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").values
    close_arr = df["close"].values.astype(float)
    high_arr = df["high"].values.astype(float)
    low_arr = df["low"].values.astype(float)
    n_rows = len(df)

    records = []
    for row_idx in range(n_rows):
        if not signals[row_idx]:
            continue
        date_str = date_strs[row_idx]
        if date_str < start_date or date_str > end_date:
            continue

        close_t = close_arr[row_idx]

        ret_t5 = np.nan
        ret_t10 = np.nan
        max_gain_10 = np.nan
        max_loss_10 = np.nan

        if row_idx + 5 < n_rows:
            ret_t5 = (close_arr[row_idx + 5] - close_t) / close_t * 100

        if row_idx + 10 < n_rows:
            ret_t10 = (close_arr[row_idx + 10] - close_t) / close_t * 100
            future_highs = high_arr[row_idx + 1:row_idx + 11]
            future_lows = low_arr[row_idx + 1:row_idx + 11]
            max_gain_10 = (np.max(future_highs) - close_t) / close_t * 100
            max_loss_10 = (np.min(future_lows) - close_t) / close_t * 100

        records.append({
            "symbol": symbol,
            "name": name,
            "date": date_str,
            "close": round(close_t, 2),
            "ret_t5": round(ret_t5, 2) if not np.isnan(ret_t5) else np.nan,
            "ret_t10": round(ret_t10, 2) if not np.isnan(ret_t10) else np.nan,
            "max_gain_10": round(max_gain_10, 2) if not np.isnan(max_gain_10) else np.nan,
            "max_loss_10": round(max_loss_10, 2) if not np.isnan(max_loss_10) else np.nan,
        })

    return {"records": records, "error": False}


# ============================================================
# 回测主流程（并行）
# ============================================================

MAX_WORKERS = max(1, (os.cpu_count() or 4) - 1)


def run_backtest():
    repo = StockRepository(ROOT)
    stock_list = repo.get_stock_list_frame()
    symbols = [str(row["symbol"]).zfill(6) for _, row in stock_list.iterrows()]
    names = {str(row["symbol"]).zfill(6): str(row["name"]) for _, row in stock_list.iterrows()}

    total = len(symbols)
    print(f"B1 回测 | 股票池: {total} 只 | 区间: {START_DATE} ~ {END_DATE}")
    print(f"并行进程数: {MAX_WORKERS}")
    print("-" * 60)

    root_str = str(ROOT)
    task_args = [
        (root_str, sym, names.get(sym, ""), START_DATE, END_DATE)
        for sym in symbols
    ]

    records = []
    matched_count = 0
    error_count = 0
    completed = 0
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_single_stock, args): args[1] for args in task_args}

        for future in as_completed(futures):
            completed += 1
            result = future.result()

            if result["error"]:
                error_count += 1
            else:
                batch = result["records"]
                records.extend(batch)
                matched_count += len(batch)

            if completed % 200 == 0 or completed == total:
                elapsed = time.time() - t0
                print(f"  进度: {completed}/{total}  命中: {matched_count}  耗时: {elapsed:.0f}s")

    elapsed = time.time() - t0
    print(f"\n回测完成: 命中 {matched_count} 次 | 错误 {error_count} | 耗时 {elapsed:.1f}s")

    if not records:
        print("未命中任何 B1 信号，请检查参数或数据。")
        return

    result_df = pd.DataFrame(records)
    result_df = result_df.sort_values(["date", "symbol"]).reset_index(drop=True)

    # 输出 CSV
    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    csv_path = output_dir / "b1_backtest_result.csv"
    result_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"明细已保存: {csv_path}")

    # 生成统计报告
    generate_report(result_df)


def generate_report(df: pd.DataFrame):
    """输出 B1 回测统计报告。"""
    print("\n" + "=" * 60)
    print("B1 战法回测统计报告")
    print("=" * 60)

    print(f"\n总命中次数: {len(df)}")
    print(f"涉及股票数: {df['symbol'].nunique()}")
    print(f"涉及交易日: {df['date'].nunique()}")

    # --- T+5 统计 ---
    valid_t5 = df["ret_t5"].dropna()
    if len(valid_t5) > 0:
        win_t5 = (valid_t5 > 0).sum()
        win_rate_t5 = win_t5 / len(valid_t5) * 100
        avg_gain_t5 = valid_t5[valid_t5 > 0].mean() if win_t5 > 0 else 0
        avg_loss_t5 = valid_t5[valid_t5 <= 0].mean() if (valid_t5 <= 0).sum() > 0 else 0
        profit_loss_ratio_t5 = abs(avg_gain_t5 / avg_loss_t5) if avg_loss_t5 != 0 else float("inf")

        print(f"\n--- T+5 收益 ---")
        print(f"  样本数: {len(valid_t5)}")
        print(f"  胜率: {win_rate_t5:.1f}%")
        print(f"  平均收益: {valid_t5.mean():.2f}%")
        print(f"  平均盈利: {avg_gain_t5:.2f}%  |  平均亏损: {avg_loss_t5:.2f}%")
        print(f"  盈亏比: {profit_loss_ratio_t5:.2f}")

    # --- T+10 统计 ---
    valid_t10 = df["ret_t10"].dropna()
    if len(valid_t10) > 0:
        win_t10 = (valid_t10 > 0).sum()
        win_rate_t10 = win_t10 / len(valid_t10) * 100
        avg_gain_t10 = valid_t10[valid_t10 > 0].mean() if win_t10 > 0 else 0
        avg_loss_t10 = valid_t10[valid_t10 <= 0].mean() if (valid_t10 <= 0).sum() > 0 else 0
        profit_loss_ratio_t10 = abs(avg_gain_t10 / avg_loss_t10) if avg_loss_t10 != 0 else float("inf")

        print(f"\n--- T+10 收益 ---")
        print(f"  样本数: {len(valid_t10)}")
        print(f"  胜率: {win_rate_t10:.1f}%")
        print(f"  平均收益: {valid_t10.mean():.2f}%")
        print(f"  平均盈利: {avg_gain_t10:.2f}%  |  平均亏损: {avg_loss_t10:.2f}%")
        print(f"  盈亏比: {profit_loss_ratio_t10:.2f}")

    # --- 10日内最大盈亏 ---
    valid_gain = df["max_gain_10"].dropna()
    valid_loss = df["max_loss_10"].dropna()
    if len(valid_gain) > 0:
        print(f"\n--- 10日内极值 ---")
        print(f"  平均最大浮盈: {valid_gain.mean():.2f}%")
        print(f"  平均最大浮亏: {valid_loss.mean():.2f}%")
        print(f"  最大单笔浮亏: {valid_loss.min():.2f}%")

    # --- 月度分布 ---
    df_with_month = df.copy()
    df_with_month["month"] = pd.to_datetime(df_with_month["date"]).dt.to_period("M")
    monthly = df_with_month.groupby("month").agg(
        count=("symbol", "count"),
        avg_ret_t5=("ret_t5", "mean"),
    )
    print(f"\n--- 月度命中分布 ---")
    for period, row in monthly.iterrows():
        avg_str = f"{row['avg_ret_t5']:.1f}%" if not np.isnan(row["avg_ret_t5"]) else "N/A"
        print(f"  {period}: {int(row['count'])} 次  平均T+5: {avg_str}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    run_backtest()
