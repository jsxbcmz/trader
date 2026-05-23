"""
从同花顺概念页面抓取概念名称和对应code
https://q.10jqka.com.cn/gn/
"""

import json
import re
import urllib.request


def fetch_ths_concept():
    url = "https://q.10jqka.com.cn/gn/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://q.10jqka.com.cn/",
    }

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("gbk", errors="ignore")

    # 匹配类似: <a href="http://q.10jqka.com.cn/gn/detail/code/308699/" target="_blank">科创次新股</a>
    pattern = r'<a\s+href="http://q\.10jqka\.com\.cn/gn/detail/code/(\d+)/"\s*target="_blank">([^<]+)</a>'
    matches = re.findall(pattern, html)

    if not matches:
        print("未匹配到数据，尝试打印部分HTML以调试：")
        print(html[:3000])
        return {}

    concept_map = {}
    for code, name in matches:
        concept_map[name] = code

    return concept_map


def main():
    print("正在抓取同花顺概念数据...")
    concept_map = fetch_ths_concept()

    if not concept_map:
        print("抓取失败，未获取到数据")
        return

    output_path = "ths_concept.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(concept_map, f, ensure_ascii=False, indent=2)

    print(f"抓取完成，共 {len(concept_map)} 个概念，已保存到 {output_path}")


if __name__ == "__main__":
    main()
