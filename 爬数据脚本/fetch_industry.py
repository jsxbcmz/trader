"""
从同花顺 basic.10jqka.com.cn/{symbol}/field.html 页面抓取二级行业分类，
将第二级行业写入 stocklist.csv 的'涉及行业'列（插在 industry 和 涉及概念 之间）。
"""

import csv
import re
import time
import random
import os
from urllib.request import Request, urlopen

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stocklist.csv")

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
    # 读取 CSV
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        original_fieldnames = reader.fieldnames
        rows = list(reader)

    # 如果已有'涉及行业'列，检查是否需要继续补充
    has_column = "涉及行业" in original_fieldnames
    if has_column:
        # 找出还没抓取的行（涉及行业为空或"获取失败"的）
        pending_rows = [(i, r) for i, r in enumerate(rows)
                        if not r.get("涉及行业") or r["涉及行业"] == "获取失败"]
        print(f"已有'涉及行业'列，还有 {len(pending_rows)} 条待抓取")
    else:
        # 新增列，所有行都需要抓取
        for row in rows:
            row["涉及行业"] = ""
        pending_rows = list(enumerate(rows))
        print(f"新增'涉及行业'列，共 {len(pending_rows)} 条待抓取")

    # 构建输出列顺序：industry 后面插入 涉及行业
    if not has_column:
        fieldnames = []
        for field in original_fieldnames:
            fieldnames.append(field)
            if field == "industry":
                fieldnames.append("涉及行业")
    else:
        fieldnames = original_fieldnames

    total = len(pending_rows)
    save_interval = 50  # 每50条保存一次

    for idx, (row_index, row) in enumerate(pending_rows):
        symbol = row["symbol"]
        print(f"[{idx+1}/{total}] 抓取 {symbol} {row['name']} ...")

        industry = fetch_industry(symbol)
        rows[row_index]["涉及行业"] = industry

        if industry:
            print(f"  -> {industry}")
        else:
            print(f"  -> (未获取到)")

        # 定期保存，防止中断丢失
        if (idx + 1) % save_interval == 0 or (idx + 1) == total:
            with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"  --- 已保存 ({idx+1}/{total}) ---")

        # 随机延迟，避免被封
        time.sleep(random.uniform(0.3, 0.8))

    print(f"\n全部完成！结果已写入 {CSV_PATH}")


if __name__ == "__main__":
    main()
