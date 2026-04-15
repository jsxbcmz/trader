"""手动采集每日持仓数据脚本，独立于 GUI 运行。"""
from __future__ import annotations

import json
import os
import requests
import time
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def main():
    source_path = os.path.join(OUTPUT_DIR, "user_keys.json")
    if not os.path.exists(source_path):
        print("❌ user_keys.json 不存在，请先在 GUI 中采集比赛排名数据")
        return

    with open(source_path, "r", encoding="utf-8") as f:
        user_keys = json.load(f)

    today = datetime.now().strftime("%Y%m%d")
    session = requests.Session()
    all_items = []
    total = len(user_keys)
    fail_count = 0

    print(f"📊 开始采集每日持仓，共 {total} 个用户，日期: {today}")
    start_time = time.time()

    for i, item in enumerate(user_keys, 1):
        user_key = str(item.get("user_key", ""))
        if not user_key:
            continue
        try:
            resp = session.get(
                "https://capital.hexin.cn/caishen_httpserver/direct/caishen_fund/community_share/v1/day_position_by_share",
                params={
                    "date": today,
                    "terminal": "1",
                    "version": "2",
                    "delivery_id": "",
                    "match_type": "community",
                    "key": "fxDXFko",
                    "user_key": user_key,
                    "hexin-v": "Azta1qdk1pwV4OooMGOMT1CVzBSgkE-TSaQTRi34FzpRjFXKtWDf4ll0o54-",
                },
                timeout=30,
            )
            items = resp.json().get("ex_data", {}).get("list", [])
            for record in items:
                if isinstance(record, dict):
                    record["user_key"] = user_key
            all_items.extend(items)
        except Exception as error:
            fail_count += 1
            if fail_count <= 5:
                print(f"  ⚠️ user_key={user_key} 失败: {error}")

        if i % 100 == 0 or i == total:
            elapsed = time.time() - start_time
            print(f"  进度: {i}/{total} | 持仓记录: {len(all_items)} | 失败: {fail_count} | 耗时: {elapsed:.1f}s")

        time.sleep(0.3)

    # 过滤只保留 00/60 开头的股票
    filtered = [
        item for item in all_items
        if isinstance(item, dict)
        and (str(item.get("code", "")).startswith("00") or str(item.get("code", "")).startswith("60"))
    ]

    output_path = os.path.join(OUTPUT_DIR, "day_positions.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print(f"\n✅ 采集完成！")
    print(f"   用户总数: {total}")
    print(f"   原始记录: {len(all_items)}")
    print(f"   过滤后: {len(filtered)} (仅 00/60 开头)")
    print(f"   失败: {fail_count}")
    print(f"   总耗时: {elapsed:.1f}s")
    print(f"   保存至: {output_path}")


if __name__ == "__main__":
    main()
