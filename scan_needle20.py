"""
扫描单针下20指标：
  长期值从前一天的100降到当天的80~85
  短期值从前一天的95以上降到当天的5~20（不含边界）
时间范围：20240101 ~ 20260331
输出：筛选结果 + 后续3天收益率，生成 md 文件
"""

import pandas as pd
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
STOCK_DATA_DIR = PROJECT_ROOT / "stock_daily_data"
STOCKLIST_CSV = PROJECT_ROOT / "stocklist.csv"
OUTPUT_MD = PROJECT_ROOT / "output" / "单针下20筛选结果.md"

START_DATE = "2024-01-01"
END_DATE = "2026-03-31"

# 指标周期参数（来自 单针下20.txt）
SHORT_PERIOD = 3
LONG_PERIOD = 20


def compute_indicator(low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """计算单针下20指标值: 100*(C-LLV(L,N))/(HHV(C,N)-LLV(L,N))，使用pandas滚动窗口加速"""
    low_series = pd.Series(low)
    close_series = pd.Series(close)
    llv_low = low_series.rolling(window=period, min_periods=period).min().values
    hhv_close = close_series.rolling(window=period, min_periods=period).max().values
    span = hhv_close - llv_low
    safe_span = np.where(np.abs(span) < 1e-12, np.nan, span)
    result = 100.0 * (close - llv_low) / safe_span
    return result


def load_stock_list() -> pd.DataFrame:
    df = pd.read_csv(STOCKLIST_CSV, dtype={"symbol": str, "ts_code": str})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def load_daily_data(symbol: str) -> pd.DataFrame:
    filepath = STOCK_DATA_DIR / f"{symbol}.csv"
    if not filepath.exists():
        return pd.DataFrame()
    df = pd.read_csv(filepath)
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close", "low"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def scan_single_stock(symbol: str, name: str) -> list[dict]:
    df = load_daily_data(symbol)
    if df.empty or len(df) < LONG_PERIOD + 1:
        return []

    close = df["close"].values.astype(float)
    low = df["low"].values.astype(float)
    dates = df["date"].values

    short_vals = compute_indicator(low, close, SHORT_PERIOD)
    long_vals = compute_indicator(low, close, LONG_PERIOD)

    results = []

    start_dt = np.datetime64(START_DATE)
    end_dt = np.datetime64(END_DATE)

    for i in range(3, len(df)):
        current_date = dates[i]
        if current_date < start_dt or current_date > end_dt:
            continue

        # 检查当天和前3天的值是否有效
        has_nan = False
        for j in range(i - 3, i + 1):
            if np.isnan(short_vals[j]) or np.isnan(long_vals[j]):
                has_nan = True
                break
        if has_nan:
            continue

        curr_long = long_vals[i]
        curr_short = short_vals[i]

        # 检查前3天到前1天（i-3, i-2, i-1）短期和长期都需要在80以上
        prev_days_ok = True
        for j in range(i - 3, i):
            if short_vals[j] < 80.0 or long_vals[j] < 80.0:
                prev_days_ok = False
                break
        if not prev_days_ok:
            continue

        # 检查前3天内不能出现涨停（涨幅>=9.8%）
        # 前3天指 i-3, i-2, i-1 这三天，各自相对前一天的涨幅
        has_limit_up = False
        for j in range(i - 3, i):
            if j >= 1 and close[j - 1] > 0:
                change_pct = (close[j] - close[j - 1]) / close[j - 1] * 100.0
                if change_pct >= 9.8:
                    has_limit_up = True
                    break
        if has_limit_up:
            continue

        # 检查当天跌幅 > 5%
        if i >= 1 and close[i - 1] > 0:
            day_drop = (close[i] - close[i - 1]) / close[i - 1] * 100.0
            if day_drop > -5.0:
                continue
        else:
            continue

        prev_long = long_vals[i - 1]
        prev_short = short_vals[i - 1]

        # 筛选条件：
        # 长期值：前一天=100，当天80~85
        # 短期值：前一天>=95，当天 >5 且 <20
        # 前3天到前1天：短期和长期都>=80
        # 前3天内不能出现涨停
        # 当天跌幅 > 5%
        if (prev_long == 100.0 and 80.0 <= curr_long <= 85.0 and
                prev_short >= 95.0 and curr_short > 5.0 and curr_short < 20.0):

            date_str = pd.Timestamp(current_date).strftime("%Y-%m-%d")

            returns = {}
            for day_offset in [1, 2, 3]:
                future_idx = i + day_offset
                if future_idx < len(df):
                    future_close = close[future_idx]
                    current_close = close[i]
                    ret = (future_close - current_close) / current_close * 100.0
                    returns[f"day{day_offset}"] = round(ret, 2)
                else:
                    returns[f"day{day_offset}"] = None

            results.append({
                "symbol": symbol,
                "name": name,
                "date": date_str,
                "prev_short": round(prev_short, 2),
                "curr_short": round(curr_short, 2),
                "prev_long": round(prev_long, 2),
                "curr_long": round(curr_long, 2),
                "ret_day1": returns["day1"],
                "ret_day2": returns["day2"],
                "ret_day3": returns["day3"],
            })

    return results


def generate_markdown(all_results: list[dict]):
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("## 单针下20指标筛选结果")
    lines.append("")
    lines.append("**筛选条件：**")
    lines.append(f"- **时间范围**: {START_DATE} ~ {END_DATE}")
    lines.append(f"- **长期值**（周期{LONG_PERIOD}）: 前一天 = 100，当天降到 80~85")
    lines.append(f"- **短期值**（周期{SHORT_PERIOD}）: 前一天 ≥ 95，当天降到 5 < 值 < 20")
    lines.append(f"- **前3天条件**: 前3天到前1天的短期和长期值均 ≥ 80")
    lines.append(f"- **涨停排除**: 前3天内不能出现涨停（涨幅 ≥ 9.8%）")
    lines.append(f"- **当天跌幅**: 当天跌幅 > 5%")
    lines.append("")
    lines.append(f"**共筛选出 {len(all_results)} 条记录**")
    lines.append("")

    if not all_results:
        lines.append("> 未找到符合条件的记录。")
    else:
        lines.append("| 序号 | 股票代码 | 股票名称 | 触发日期 | 前日短期 | 当日短期 | 前日长期 | 当日长期 | T+1收益率 | T+2收益率 | T+3收益率 |")
        lines.append("|------|----------|----------|----------|----------|----------|----------|----------|-----------|-----------|-----------|")

        for idx, record in enumerate(all_results, 1):
            ret1 = f"{record['ret_day1']}%" if record['ret_day1'] is not None else "N/A"
            ret2 = f"{record['ret_day2']}%" if record['ret_day2'] is not None else "N/A"
            ret3 = f"{record['ret_day3']}%" if record['ret_day3'] is not None else "N/A"

            lines.append(
                f"| {idx} "
                f"| {record['symbol']} "
                f"| {record['name']} "
                f"| {record['date']} "
                f"| {record['prev_short']} "
                f"| {record['curr_short']} "
                f"| {record['prev_long']} "
                f"| {record['curr_long']} "
                f"| {ret1} "
                f"| {ret2} "
                f"| {ret3} |"
            )

        # 统计汇总
        lines.append("")
        lines.append("### 收益率统计")
        lines.append("")

        valid_ret1 = [r["ret_day1"] for r in all_results if r["ret_day1"] is not None]
        valid_ret2 = [r["ret_day2"] for r in all_results if r["ret_day2"] is not None]
        valid_ret3 = [r["ret_day3"] for r in all_results if r["ret_day3"] is not None]

        lines.append("| 统计项 | T+1收益率 | T+2收益率 | T+3收益率 |")
        lines.append("|--------|-----------|-----------|-----------|")

        if valid_ret1:
            lines.append(
                f"| 平均值 | {round(np.mean(valid_ret1), 2)}% | "
                f"{round(np.mean(valid_ret2), 2) if valid_ret2 else 'N/A'}% | "
                f"{round(np.mean(valid_ret3), 2) if valid_ret3 else 'N/A'}% |"
            )
            lines.append(
                f"| 中位数 | {round(np.median(valid_ret1), 2)}% | "
                f"{round(np.median(valid_ret2), 2) if valid_ret2 else 'N/A'}% | "
                f"{round(np.median(valid_ret3), 2) if valid_ret3 else 'N/A'}% |"
            )
            win_rate1 = round(sum(1 for r in valid_ret1 if r > 0) / len(valid_ret1) * 100, 1)
            win_rate2 = round(sum(1 for r in valid_ret2 if r > 0) / len(valid_ret2) * 100, 1) if valid_ret2 else "N/A"
            win_rate3 = round(sum(1 for r in valid_ret3 if r > 0) / len(valid_ret3) * 100, 1) if valid_ret3 else "N/A"
            lines.append(f"| 胜率 | {win_rate1}% | {win_rate2}% | {win_rate3}% |")
            lines.append(
                f"| 最大收益 | {round(max(valid_ret1), 2)}% | "
                f"{round(max(valid_ret2), 2) if valid_ret2 else 'N/A'}% | "
                f"{round(max(valid_ret3), 2) if valid_ret3 else 'N/A'}% |"
            )
            lines.append(
                f"| 最大亏损 | {round(min(valid_ret1), 2)}% | "
                f"{round(min(valid_ret2), 2) if valid_ret2 else 'N/A'}% | "
                f"{round(min(valid_ret3), 2) if valid_ret3 else 'N/A'}% |"
            )

    content = "\n".join(lines) + "\n"
    OUTPUT_MD.write_text(content, encoding="utf-8")
    print(f"\n结果已保存到: {OUTPUT_MD}")


def main():
    stock_list = load_stock_list()
    total = len(stock_list)
    print(f"共 {total} 只股票待扫描，时间范围: {START_DATE} ~ {END_DATE}")
    print(f"筛选条件: 长期(前日=100, 当日80~85), 短期(前日>=95, 当日 5<值<20)")
    print("-" * 60)

    all_results = []
    for idx, row in stock_list.iterrows():
        symbol = row["symbol"]
        name = row["name"]

        if (idx + 1) % 500 == 0 or idx == 0:
            print(f"扫描进度: {idx + 1}/{total} - {symbol} {name}")

        hits = scan_single_stock(symbol, name)
        if hits:
            for hit in hits:
                print(f"  ✅ 命中: {symbol} {name} @ {hit['date']} "
                      f"短期 {hit['prev_short']}->{hit['curr_short']} "
                      f"长期 {hit['prev_long']}->{hit['curr_long']}")
            all_results.extend(hits)

    # 按日期排序
    all_results.sort(key=lambda x: (x["date"], x["symbol"]))

    print(f"\n{'=' * 60}")
    print(f"扫描完成！共找到 {len(all_results)} 条符合条件的记录")

    generate_markdown(all_results)


if __name__ == "__main__":
    main()
