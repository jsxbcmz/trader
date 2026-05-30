"""T6 预测准确率统计：自动(screening_predictions) vs 手动(manual_predictions)。

需先跑 backfill_predictions.py 回填 open_correct/close_correct，本脚本读取回填结果统计。
手动预测目录的字段（expected_direction/source）与自动一致，但需先回填——
若手动文件尚未回填，统计会把它们计入 not_filled。

用法：
    python scripts/prediction_accuracy.py            # 汇总全部日期
    python scripts/prediction_accuracy.py 2026-05-26 # 只看指定日期
"""

import sys
import json
from pathlib import Path

PROJECT = Path('/opt/data/workspace/trader')
sys.path.insert(0, str(PROJECT))

from core.scoring.prediction_review import aggregate_accuracy

AUTO_DIR = PROJECT / 'output' / 'screening_predictions'
MANUAL_DIR = PROJECT / 'output' / 'manual_predictions'


def _load_stocks(path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8') as f:
        return json.load(f).get('stocks', [])


def _collect(directory, dates):
    """收集指定目录下若干日期的全部 stocks。"""
    all_stocks = []
    per_date = {}
    for date in dates:
        path = directory / f'{date}.json'
        stocks = _load_stocks(path)
        if stocks:
            per_date[date] = stocks
            all_stocks.extend(stocks)
    return all_stocks, per_date


def _resolve_dates(arg_dates, directory):
    if arg_dates:
        return arg_dates
    return [
        p.stem for p in sorted(directory.glob('2026-*.json'))
        if not p.stem.startswith('review')
    ]


def _print_block(title, stats):
    if stats["open_total"] == 0 and stats["close_total"] == 0:
        print(f"\n【{title}】无可统计样本"
              f"（中性 {stats['neutral_count']} / 未回填 {stats['not_filled_count']}）")
        return
    print(f"\n【{title}】")
    print(f"  开盘方向准确率: {stats['open_accuracy']}%  "
          f"({stats['open_hit']}/{stats['open_total']})")
    print(f"  收盘方向准确率: {stats['close_accuracy']}%  "
          f"({stats['close_hit']}/{stats['close_total']})")
    print(f"  中性(不计入): {stats['neutral_count']}  "
          f"未回填: {stats['not_filled_count']}")


def main():
    arg_dates = sys.argv[1:]
    auto_dates = _resolve_dates(arg_dates, AUTO_DIR)
    manual_dates = _resolve_dates(arg_dates, MANUAL_DIR)

    auto_all, _ = _collect(AUTO_DIR, auto_dates)
    manual_all, _ = _collect(MANUAL_DIR, manual_dates)

    print("=" * 56)
    print("预测准确率统计（仅统计已回填且非中性的样本）")
    print("=" * 56)
    _print_block("自动 (screening_predictions)", aggregate_accuracy(auto_all))
    _print_block("手动 (manual_predictions)", aggregate_accuracy(manual_all))

    auto_stats = aggregate_accuracy(auto_all)
    manual_stats = aggregate_accuracy(manual_all)
    if auto_stats["close_accuracy"] is not None and manual_stats["close_accuracy"] is not None:
        print(f"\n收盘方向对比: 自动 {auto_stats['close_accuracy']}% "
              f"vs 手动 {manual_stats['close_accuracy']}%")
    else:
        print("\n（数据未回填完成，待 market.db 补齐 05-23 后数据后重跑可得真实对比）")


if __name__ == '__main__':
    main()
