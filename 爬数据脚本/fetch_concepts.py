"""
https://stockpage.10jqka.com.cn/603217/,涉及概念
从同花顺股票页面批量抓取股票涉及概念，写入数据库 stock_list 表
使用线程池并发抓取，大幅提升速度
"""

import re
import sys
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core.data.database import MarketDatabase, init_databases, get_market_db

MAX_WORKERS = 10  # 并发线程数，太大容易被封IP

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")

CONCEPT_PATTERN = re.compile(
    r'<dt>涉及概念：</dt>\s*<dd\s+title="([^"]*)"', re.DOTALL
)


def fetch_concept(symbol: str, max_retries: int = 3) -> str:
    """抓取单只股票的涉及概念"""
    url = f"https://stockpage.10jqka.com.cn/{symbol}/"
    for attempt in range(max_retries):
        try:
            req = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://stockpage.10jqka.com.cn/",
            })
            with urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")
            match = CONCEPT_PATTERN.search(html)
            if match:
                return match.group(1).strip()
            return ""
        except Exception as err:
            if attempt < max_retries - 1:
                wait_time = 1 + random.uniform(0, 1)
                time.sleep(wait_time)
            else:
                return "获取失败"


def main():
    project_root = Path(os.path.dirname(os.path.abspath(__file__))).parent
    init_databases(project_root)
    market_db = get_market_db()

    df = market_db.read_df(
        "SELECT symbol, name, concepts FROM stock_list ORDER BY symbol"
    )
    if df.empty:
        print("数据库中无股票列表，请先运行迁移脚本")
        return

    total = len(df)
    print(f"共 {total} 只股票，并发数: {MAX_WORKERS}\n")

    pending_rows = []
    for _, row in df.iterrows():
        concepts = row.get("concepts", "") or ""
        if concepts and concepts != "获取失败":
            continue
        pending_rows.append({"symbol": str(row["symbol"]).zfill(6), "name": row.get("name", "")})

    pending_total = len(pending_rows)
    if pending_total == 0:
        print("✅ 所有股票已抓取完毕，无需再跑")
        return

    print(f"已有 {total - pending_total} 条历史记录，待抓取: {pending_total} 只\n")

    finished_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_row = {
            executor.submit(fetch_concept, row["symbol"]): row
            for row in pending_rows
        }

        for future in as_completed(future_to_row):
            row = future_to_row[future]
            concept = future.result()
            finished_count += 1

            market_db.update_stock_concepts(row["symbol"], concept)

            status = concept[:30] if concept else "无概念"
            elapsed = time.time() - start_time
            speed = finished_count / elapsed if elapsed > 0 else 0
            eta = (pending_total - finished_count) / speed if speed > 0 else 0
            print(f"[{finished_count}/{pending_total}] {row['symbol']} {row.get('name','')} -> {status}  "
                  f"({speed:.1f}条/s, 剩余约{eta:.0f}s)")

    total_time = time.time() - start_time
    print(f"\n✅ 全部完成！耗时 {total_time:.1f}s，结果已保存到数据库")


if __name__ == "__main__":
    main()
