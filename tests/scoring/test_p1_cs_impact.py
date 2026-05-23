"""P1-3 跨时期回测对比 MVP：测试截面归一化（P1-2）是否真的让评分更好。

对最近 N 个交易日：
1. 跑两次评分（A 旧绝对阈值 / B 新截面分位）
2. 算 Top 20 在 T+1 的平均收益、胜率
3. 算 IC（评分与 T+1 收益的秩相关）
4. 对比 A/B 哪个更好

注意：这是 MVP 版，只跑近期数据看趋势。完整版（2020/2022/2024 三时期）
是 P1-3 的远期扩展，需要专门的批量回测脚本。

用法：python tests/scoring/test_p1_cs_impact.py [N_DAYS]
默认 N=5（约 1 周交易日）；扩到 30 约需 5 分钟。
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.data.io import load_daily_csv
from core.data.repository import StockRepository
from core.scoring import MainBoardScoringEngine


CALENDAR_REF = "000001"


def get_recent_trade_dates(root: Path, n: int) -> list[str]:
    """用 000001 日线作为交易日历，取最近 n 个交易日（跳过最末 4 天以保证 T+3 收益可算）。"""
    df = load_daily_csv(root / "stock_daily_data", CALENDAR_REF)
    dates = pd.to_datetime(df["date"]).sort_values().tolist()
    # 留最近 4 天用于算 T+1~T+3
    cutoff = len(dates) - 4
    if cutoff <= n:
        return []
    return [d.strftime("%Y-%m-%d") for d in dates[cutoff - n:cutoff]]


def compute_t1_return(repo: StockRepository, symbol: str, score_date: str) -> float | None:
    """从 score_date 收盘到 T+1 收盘的简单收益率。"""
    df = repo.get_daily_frame(symbol)
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(df["date"])
    score_ts = pd.Timestamp(score_date)
    idx = df.index[dates == score_ts]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    if i + 1 >= len(df):
        return None
    c0 = float(df.loc[i, "close"])
    c1 = float(df.loc[i + 1, "close"])
    if c0 <= 0:
        return None
    return (c1 - c0) / c0


def evaluate_mode(label: str, dates: list[str], root: Path, use_cs: bool) -> dict:
    """跑指定模式，返回汇总指标。"""
    engine = MainBoardScoringEngine.from_root(root, use_cross_section=use_cs)
    repo = StockRepository(root=root)

    all_top20_returns: list[float] = []
    win_count = total_count = 0
    ic_pairs: list[tuple[float, float]] = []

    print(f"\n── 模式 {label}（use_cross_section={use_cs}）──")
    for d in dates:
        t0 = time.time()
        result = engine.score_date(d)
        elapsed = time.time() - t0

        matched = sorted(
            [m for m in result.matches if m.final_matched],
            key=lambda m: m.final_score, reverse=True,
        )
        top20 = matched[:20]

        # 算每只 top20 的 T+1
        for m in top20:
            r = compute_t1_return(repo, m.symbol, d)
            if r is None:
                continue
            all_top20_returns.append(r)
            total_count += 1
            if r > 0:
                win_count += 1

        # IC：评分 vs T+1 收益 的秩相关（用全部命中票，不只 top20）
        pairs = []
        for m in matched:
            r = compute_t1_return(repo, m.symbol, d)
            if r is None:
                continue
            pairs.append((m.final_score, r))
        if len(pairs) >= 10:
            scores = pd.Series([p[0] for p in pairs])
            rets = pd.Series([p[1] for p in pairs])
            # Spearman 秩相关 = 秩序列的 Pearson 相关（避免依赖 scipy）
            ic = scores.rank().corr(rets.rank())
            if pd.notna(ic):
                ic_pairs.append((d, float(ic)))

        print(f"  {d}: 命中 {len(matched)} / Top20 平均 T+1 = "
              f"{np.mean([r for r in [compute_t1_return(repo, m.symbol, d) for m in top20] if r is not None])*100:+.2f}%  ({elapsed:.1f}s)")

    avg_return = float(np.mean(all_top20_returns)) if all_top20_returns else 0.0
    win_rate = (win_count / total_count) if total_count else 0.0
    avg_ic = float(np.mean([p[1] for p in ic_pairs])) if ic_pairs else 0.0

    return {
        "label": label,
        "avg_t1_return": avg_return,
        "win_rate": win_rate,
        "avg_ic": avg_ic,
        "sample_count": total_count,
        "ic_days": len(ic_pairs),
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    root = PROJECT_ROOT
    dates = get_recent_trade_dates(root, n)
    if not dates:
        print("交易日不足，无法回测")
        sys.exit(1)

    print(f"=== P1-3 截面归一化效果对比（{len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}）===")

    res_a = evaluate_mode("A 旧绝对阈值", dates, root, use_cs=False)
    res_b = evaluate_mode("B 截面分位", dates, root, use_cs=True)

    print("\n" + "=" * 60)
    print("汇总对比")
    print("=" * 60)
    print(f"{'指标':<25} {'A 旧':<15} {'B 截面分位':<15} {'差异':<10}")
    print("-" * 60)
    print(f"{'Top20 平均 T+1 收益':<25} {res_a['avg_t1_return']*100:>+8.3f}%   "
          f"{res_b['avg_t1_return']*100:>+8.3f}%   "
          f"{(res_b['avg_t1_return']-res_a['avg_t1_return'])*100:>+6.3f}%")
    print(f"{'Top20 胜率':<25} {res_a['win_rate']*100:>8.1f}%   "
          f"{res_b['win_rate']*100:>8.1f}%   "
          f"{(res_b['win_rate']-res_a['win_rate'])*100:>+6.1f}%")
    print(f"{'平均日 IC':<25} {res_a['avg_ic']:>8.4f}    "
          f"{res_b['avg_ic']:>8.4f}    "
          f"{res_b['avg_ic']-res_a['avg_ic']:>+6.4f}")
    print(f"{'样本数':<25} {res_a['sample_count']:>8d}    {res_b['sample_count']:>8d}")
    print()
    if res_b["avg_ic"] > res_a["avg_ic"]:
        print(f"✅ 截面分位使 IC 提升 {(res_b['avg_ic']-res_a['avg_ic']):+.4f}")
    else:
        print(f"⚠️  截面分位使 IC 下降 {(res_b['avg_ic']-res_a['avg_ic']):+.4f}（样本太少可能噪声主导）")
    print()
    print("注：样本量小时（5~10 天）IC 噪声大，结论需用 30+ 天回测确认。")


if __name__ == "__main__":
    main()
