"""初始化数据库 + 全量更新股票日线数据"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

# 确保在项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from core.data.database import init_databases, get_market_db
from core.data.io import load_stock_list
from app.history_updater import HistoryUpdater
from app.tushare_client import TushareClient, TushareClientError, DEFAULT_TUSHARE_TOKEN


def init_db_and_import_stocklist():
    """初始化数据库并导入股票列表"""
    print("[1/3] 初始化数据库...")
    init_databases(PROJECT_ROOT)
    market_db = get_market_db()
    print(f"  数据库已创建: {PROJECT_ROOT / 'db' / 'market.db'}")

    # 导入股票列表
    stocklist_csv = PROJECT_ROOT / "stocklist.csv"
    print(f"[1/3] 导入股票列表 ({stocklist_csv.name})...")
    df_stock = pd.read_csv(stocklist_csv, dtype=str)
    df_stock = df_stock.fillna("")
    print(f"  CSV 共 {len(df_stock)} 只股票")

    market_db.upsert_stock_list(df_stock)
    count = market_db.get_stock_list_count()
    print(f"  数据库 stock_list 表: {count} 条记录")

    return market_db


def create_updater():
    """创建 HistoryUpdater 实例"""
    from app.data_loader import normalize_symbol

    stocklist_csv = PROJECT_ROOT / "stocklist.csv"
    stock_daily_dir = PROJECT_ROOT / "stock_daily_data"
    stock_daily_dir.mkdir(exist_ok=True)

    # 确保 Tushare token 可用
    token = os.getenv("TUSHARE_TOKEN", "").strip() or DEFAULT_TUSHARE_TOKEN
    if not token:
        print("ERROR: 未配置 Tushare Token")
        sys.exit(1)

    client = TushareClient(token=token)
    return HistoryUpdater(stocklist_csv=stocklist_csv, stock_daily_data_dir=stock_daily_dir, client=client)


def main():
    t_start = time.perf_counter()

    # Step 1: Init DB
    market_db = init_db_and_import_stocklist()

    # Step 2: Create updater
    print("\n[2/3] 创建数据更新器...")
    updater = create_updater()
    total_stocks = len(updater.df_list)
    print(f"  加载 {total_stocks} 只股票 + 2 个指数")

    # Step 3: Run update
    print(f"\n[3/3] 开始全量更新日线数据...")
    print(f"  时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据范围: 2010-01-01 ~ 至今")
    print(f"  预计耗时: 20~40 分钟 (受 Tushare 限速 200次/分钟)")
    print()

    results, summary = updater.update_all_symbols()

    # Step 4: Print summary
    elapsed = time.perf_counter() - t_start
    print(f"\n{'='*60}")
    print(f"更新完成!")
    print(f"  总股票数:   {summary.total}")
    print(f"  成功更新:   {summary.success}")
    print(f"  无需更新:   {summary.skipped}")
    print(f"  失败:       {summary.failed}")
    print(f"  被取消:     {summary.cancelled}")
    print(f"  总耗时:     {summary.elapsed_seconds:.1f}s ({summary.elapsed_seconds/60:.1f} min)")

    # Show some details
    updated = [r for r in results if r.status == "updated"]
    failed = [r for r in results if r.status == "failed"]

    if updated:
        print(f"\n更新成功的亮点 (前10):")
        for r in updated[:10]:
            print(f"  {r.symbol} {r.name}: +{r.rows_written}行 ({r.message})")

    if failed:
        print(f"\n失败的股票 ({len(failed)}只):")
        for r in failed[:20]:
            print(f"  {r.symbol} {r.name}: {r.message}")

    print(f"\n数据库统计:")
    print(f"  stock_daily 总行数: {market_db.get_total_stock_daily_count()}")
    print(f"  stock_list 记录数: {market_db.get_stock_list_count()}")

    print(f"\n实际总耗时: {elapsed:.1f}s ({elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()
