"""M2 真实准确率重算：用 M1 落库的 intraday_verdict 替代「只看收盘价」口径。

读 scoring.db 的 intraday_review 表，按路径级裁定算「真对率」，并定位错误环节
（开盘判错 / 盘中尾盘跳水），与 T6 的收盘价口径（close_correct）对比，揭示
「收盘价模糊准确率」高估了多少（高开低走被旧口径误判为对）。

用法：
    python scripts/intraday_accuracy.py 2026-05-29   # 重算指定 review_date(T+1)
    python scripts/intraday_accuracy.py              # 自动取表中所有 review_date
"""

import sys
from pathlib import Path

PROJECT = Path('/opt/data/workspace/trader')
sys.path.insert(0, str(PROJECT))

from core.data.database import init_databases, get_scoring_db
from core.scoring.intraday_metrics import aggregate_intraday_verdicts


def _all_review_dates(scoring_db):
    df = scoring_db.read_df(
        "SELECT DISTINCT review_date FROM intraday_review ORDER BY review_date")
    return [str(d) for d in df["review_date"].tolist()] if not df.empty else []


def _print_report(review_date, rows):
    stats = aggregate_intraday_verdicts(rows)
    print(f"\n========== {review_date}（{len(rows)} 只复盘）==========")
    if stats["judged_total"] == 0:
        print(f"  无可裁定样本（排除 {stats['excluded_count']} 只：中性/无分时/窄幅）")
        return
    print(f"  真实准确率(路径级): {stats['true_accuracy']}%  "
          f"真对 {stats['true_count']}/{stats['judged_total']}")
    print(f"  宽口径准确率(含蒙对≈旧收盘价口径): {stats['loose_accuracy']}%")
    print(f"  └ 蒙对(结果对但开盘判错): {stats['lucky_count']}  "
          f"→ 旧口径高估了 {stats['lucky_count']} 只")
    print(f"  错误: {stats['error_count']}  排除(中性/无数据): {stats['excluded_count']}")
    if stats["error_stage"]:
        print("  错误环节归因:")
        for stage, count in sorted(stats["error_stage"].items(), key=lambda kv: -kv[1]):
            print(f"    - {stage}: {count} 只")


def main():
    init_databases(PROJECT)
    scoring_db = get_scoring_db()
    review_dates = sys.argv[1:] or _all_review_dates(scoring_db)
    if not review_dates:
        print("intraday_review 表为空，请先跑 scripts/review_intraday.py 落库")
        return

    all_rows = []
    for review_date in review_dates:
        df = scoring_db.load_intraday_review(review_date)
        rows = df.to_dict("records")
        all_rows.extend(rows)
        _print_report(review_date, rows)

    if len(review_dates) > 1:
        print("\n" + "=" * 44)
        print("【汇总（全部 review_date）】")
        _print_report("汇总", all_rows)


if __name__ == '__main__':
    main()
