"""恢复更新 — 跳过已完成的股票，继续更新剩余的"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(sys.path[0])

from pathlib import Path
from core.data.database import init_databases, get_market_db
from app.history_updater import HistoryUpdater
from app.tushare_client import TushareClient

init_databases(Path.cwd())
market_db = get_market_db()

client = TushareClient.from_env()
updater = HistoryUpdater(
    stocklist_csv=Path('stocklist.csv'),
    stock_daily_data_dir=Path('stock_daily_data'),
    client=client,
)

done = set(market_db.read_df('SELECT DISTINCT symbol FROM stock_daily')['symbol'].tolist())
all_symbols = [s.zfill(6) for s in updater.df_list['symbol'].tolist()]
remaining = [s for s in all_symbols if s not in done]

print(f'共 {len(all_symbols)} 只, 已完成 {len(done)}, 剩余 {len(remaining)}', flush=True)
if not remaining:
    print('全部已完成，无需更新', flush=True)
    sys.exit(0)

success = skipped = failed = 0
t0 = time.perf_counter()

for idx, symbol in enumerate(remaining):
    result = updater.update_symbol(symbol)
    if result.status == 'updated':
        success += 1
    elif result.status == 'skipped':
        skipped += 1
    else:
        failed += 1

    if (idx + 1) % 100 == 0 or idx == len(remaining) - 1:
        elapsed = time.perf_counter() - t0
        rate = (idx + 1) / elapsed * 60 if elapsed > 0 else 0
        print(f'[{idx+1}/{len(remaining)}] '
              f'↑{success} -{skipped} ✗{failed} '
              f'| {elapsed/60:.1f}min {rate:.0f}只/min',
              flush=True)

# 更新指数
for ts_code, name in updater.INDEX_CODES:
    r = updater.update_index(ts_code)
    print(f'指数 {name}: {r.status}', flush=True)

# 申万行业
if updater._load_sw_industry_list():
    for ts_code, ind_name in updater._sw_industry_list:
        r = updater.update_industry(ts_code, ind_name)
        if r.status == 'updated':
            print(f'  行业 {ind_name}: +{r.rows_written}行', flush=True)

elapsed = time.perf_counter() - t0
rows = market_db.get_total_stock_daily_count()
print(f'\n✅ 完成! 耗时 {elapsed/60:.1f}min')
print(f'  成功: {success}, 跳过: {skipped}, 失败: {failed}')
print(f'  stock_daily 总行数: {rows}')
