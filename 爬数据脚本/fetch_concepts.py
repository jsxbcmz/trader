"""
https://stockpage.10jqka.com.cn/603217/,涉及概念
从同花顺股票页面批量抓取股票涉及概念，写入stocklist.csv
使用线程池并发抓取，大幅提升速度
"""

import csv
import re
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import URLError

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocklist.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocklist.csv")

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


def save_progress(rows, fieldnames):
    """保存当前进度到文件"""
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if "涉及概念" not in fieldnames:
        fieldnames = list(fieldnames) + ["涉及概念"]

    total = len(rows)
    print(f"共 {total} 只股票，并发数: {MAX_WORKERS}\n")

    # 读取已完成的进度
    completed = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            existing_reader = csv.DictReader(f)
            for row in existing_reader:
                concept = row.get("涉及概念", "")
                if concept and concept != "获取失败":
                    completed[row["symbol"]] = concept
        print(f"已有 {len(completed)} 条历史记录，将跳过\n")

    # 填充已有数据，筛选待抓取列表
    pending_rows = []
    for row in rows:
        symbol = row["symbol"]
        if symbol in completed:
            row["涉及概念"] = completed[symbol]
        else:
            pending_rows.append(row)

    pending_total = len(pending_rows)
    if pending_total == 0:
        print("✅ 所有股票已抓取完毕，无需再跑")
        return

    print(f"待抓取: {pending_total} 只\n")

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
            row["涉及概念"] = concept
            finished_count += 1

            status = concept[:30] if concept else "无概念"
            elapsed = time.time() - start_time
            speed = finished_count / elapsed if elapsed > 0 else 0
            eta = (pending_total - finished_count) / speed if speed > 0 else 0
            print(f"[{finished_count}/{pending_total}] {row['symbol']} {row.get('name','')} -> {status}  "
                  f"({speed:.1f}条/s, 剩余约{eta:.0f}s)")

            # 每100条保存一次进度
            if finished_count % 100 == 0:
                save_progress(rows, fieldnames)
                print(f"  >>> 已保存进度\n")

    save_progress(rows, fieldnames)
    total_time = time.time() - start_time
    print(f"\n✅ 全部完成！耗时 {total_time:.1f}s，结果已保存到: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
