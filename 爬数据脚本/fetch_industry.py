"""
从同花顺 basic.10jqka.com.cn/{symbol}/field.html 页面抓取二级行业分类，
将第二级行业写入数据库 stock_list 表的 ths_industry 字段。
"""

import re
import sys
import time
import random
import os
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core.data.database import init_databases, get_market_db

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")

# 优先匹配二级行业分类，fallback 到三级行业分类
INDUSTRY_PATTERN_2 = re.compile(
    r'二级行业分类：<span class="tip f14">\s*(.+?)\s*（共', re.DOTALL
)
INDUSTRY_PATTERN_3 = re.compile(
    r'三级行业分类：<span class="tip f14">\s*(.+?)\s*（共', re.DOTALL
)


def extract_second_level(raw_text: str) -> str:
    """从 '基础化工 -- 化学制品' 中提取第二级 '化学制品'"""
    parts = [p.strip() for p in raw_text.split("--")]
    if len(parts) >= 2:
        return parts[1]
    return raw_text.strip()


def fetch_industry(symbol: str, max_retries: int = 3) -> str:
    """抓取单只股票的二级行业分类（第二级）"""
    url = f"https://basic.10jqka.com.cn/{symbol}/field.html"
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": "https://basic.10jqka.com.cn/",
    })
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=15) as response:
                raw = response.read()
            html = raw.decode("gbk", errors="ignore")
            # 优先匹配二级行业分类
            match = INDUSTRY_PATTERN_2.search(html)
            if match:
                return extract_second_level(match.group(1))
            # fallback: 从三级行业分类中提取第二级
            match = INDUSTRY_PATTERN_3.search(html)
            if match:
                return extract_second_level(match.group(1))
            return ""
        except Exception as err:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt + random.uniform(0, 1)
                print(f"  [重试] {symbol} 第{attempt+1}次失败: {err}，{wait_time:.1f}s后重试")
                time.sleep(wait_time)
            else:
                print(f"  [失败] {symbol} 全部重试失败: {err}")
                return "获取失败"


def main():
    project_root = Path(os.path.dirname(os.path.abspath(__file__))).parent
    init_databases(project_root)
    market_db = get_market_db()

    df = market_db.read_df(
        "SELECT symbol, name, ths_industry FROM stock_list ORDER BY symbol"
    )
    if df.empty:
        print("数据库中无股票列表，请先运行迁移脚本")
        return

    pending_rows = []
    for _, row in df.iterrows():
        ths_industry = row.get("ths_industry", "") or ""
        if ths_industry and ths_industry != "获取失败":
            continue
        pending_rows.append({"symbol": str(row["symbol"]).zfill(6), "name": row.get("name", "")})

    total = len(pending_rows)
    if total == 0:
        print("✅ 所有股票行业已抓取完毕，无需再跑")
        return

    print(f"待抓取: {total} 条\n")

    for idx, row in enumerate(pending_rows):
        symbol = row["symbol"]
        print(f"[{idx+1}/{total}] 抓取 {symbol} {row['name']} ...")

        industry = fetch_industry(symbol)
        market_db.update_stock_ths_industry(symbol, industry)

        if industry:
            print(f"  -> {industry}")
        else:
            print(f"  -> (未获取到)")

        time.sleep(random.uniform(0.3, 0.8))

    print(f"\n全部完成！结果已保存到数据库")


if __name__ == "__main__":
    main()
