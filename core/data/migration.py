"""CSV → SQLite 一次性迁移脚本。

用法：
    python -m core.data.migration [项目根目录]

幂等：重复运行使用 INSERT OR REPLACE，不会产生重复数据。
原始 CSV 文件保留不删除，待确认无误后手动清理。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

from core.data.database import MarketDatabase, ScoringDatabase


def migrate_stock_daily(market_db: MarketDatabase, stock_daily_dir: Path):
    """迁移个股日线 CSV → stock_daily 表。"""
    csv_files = sorted(stock_daily_dir.glob("[0-9]*.csv"))
    total = len(csv_files)
    if total == 0:
        print("  跳过：未找到个股日线 CSV 文件")
        return

    print(f"  共 {total} 个文件")
    migrated = 0
    total_rows = 0

    for i, csv_path in enumerate(csv_files, 1):
        symbol = csv_path.stem.zfill(6)
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                continue

            required = {"date", "open", "close", "high", "low"}
            if not required.issubset(set(df.columns)):
                print(f"  警告：{csv_path.name} 缺少必要字段，跳过")
                continue

            if "volume" not in df.columns:
                df["volume"] = None
            if "turnover_rate" not in df.columns:
                df["turnover_rate"] = None

            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["date", "open", "close", "high", "low"])
            df = df.drop_duplicates(subset=["date"], keep="last")

            market_db.bulk_upsert_stock_daily(symbol, df)
            migrated += 1
            total_rows += len(df)
        except Exception as exc:
            print(f"  错误：{csv_path.name} → {exc}")

        if i % 200 == 0 or i == total:
            print(f"  进度：{i}/{total} | 已迁移 {migrated} 文件 {total_rows} 行")

    print(f"  完成：{migrated}/{total} 文件，共 {total_rows} 行")


def migrate_index_daily(market_db: MarketDatabase, stock_daily_dir: Path):
    """迁移指数日线 CSV → index_daily 表。"""
    csv_files = sorted(stock_daily_dir.glob("index_*.csv"))
    if not csv_files:
        print("  跳过：未找到指数日线 CSV 文件")
        return

    for csv_path in csv_files:
        tag = csv_path.stem.replace("index_", "").replace("_", ".")
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                continue
            required = {"date", "open", "close", "high", "low"}
            if not required.issubset(set(df.columns)):
                continue
            if "volume" not in df.columns:
                df["volume"] = None
            if "turnover_rate" not in df.columns:
                df["turnover_rate"] = None
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["date", "open", "close", "high", "low"])
            df = df.drop_duplicates(subset=["date"], keep="last")
            market_db.bulk_upsert_index_daily(tag, df)
            print(f"  指数 {tag}：{len(df)} 行")
        except Exception as exc:
            print(f"  错误：{csv_path.name} → {exc}")


def migrate_oamv(market_db: MarketDatabase, stock_daily_dir: Path):
    """迁移 OAMV 虚拟K线 CSV → oamv_daily 表。"""
    csv_path = stock_daily_dir / "oamv_930903_CSI.csv"
    if not csv_path.exists():
        print("  跳过：未找到 OAMV CSV 文件")
        return

    try:
        df = pd.read_csv(csv_path)
        if df.empty:
            return
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date", "open", "close", "high", "low"])
        df = df.drop_duplicates(subset=["date"], keep="last")
        market_db.bulk_upsert_oamv_daily(df)
        print(f"  OAMV：{len(df)} 行")
    except Exception as exc:
        print(f"  错误：{exc}")


def migrate_industry_daily(market_db: MarketDatabase, industry_dir: Path):
    """迁移行业日线 CSV → industry_daily 表。"""
    if not industry_dir.exists():
        print("  跳过：未找到行业日线目录")
        return

    csv_files = sorted(industry_dir.glob("*.csv"))
    if not csv_files:
        print("  跳过：未找到行业日线 CSV 文件")
        return

    for csv_path in csv_files:
        tag = csv_path.stem.replace("_", ".")
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                continue
            required = {"date", "open", "close", "high", "low"}
            if not required.issubset(set(df.columns)):
                continue
            if "volume" not in df.columns:
                df["volume"] = None
            if "turnover_rate" not in df.columns:
                df["turnover_rate"] = None
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["date", "open", "close", "high", "low"])
            df = df.drop_duplicates(subset=["date"], keep="last")
            market_db.bulk_upsert_industry_daily(tag, df)
            print(f"  行业 {tag}：{len(df)} 行")
        except Exception as exc:
            print(f"  错误：{csv_path.name} → {exc}")


def migrate_stock_list(market_db: MarketDatabase, stocklist_csv: Path):
    """迁移股票列表 CSV → stock_list 表。"""
    if not stocklist_csv.exists():
        print("  跳过：未找到 stocklist.csv")
        return

    try:
        df = pd.read_csv(stocklist_csv, dtype={"symbol": str, "ts_code": str})
        if df.empty:
            return
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        market_db.upsert_stock_list(df)
        print(f"  股票列表：{len(df)} 条")
    except Exception as exc:
        print(f"  错误：{exc}")


def migrate_cross_section(scoring_db: ScoringDatabase, output_dir: Path):
    """迁移截面分位 CSV → cross_section 表。"""
    cs_dir = output_dir / "scoring_cross_section"
    if not cs_dir.exists():
        print("  跳过：未找到截面分位目录")
        return

    csv_files = sorted(cs_dir.glob("*.csv"))
    if not csv_files:
        print("  跳过：未找到截面分位 CSV 文件")
        return

    for csv_path in csv_files:
        date = csv_path.stem
        try:
            df = pd.read_csv(csv_path, dtype={"symbol": str})
            if df.empty:
                continue
            df["symbol"] = df["symbol"].str.zfill(6)
            scoring_db.save_cross_section(date, df)
        except Exception as exc:
            print(f"  错误：{csv_path.name} → {exc}")

    print(f"  截面分位：{len(csv_files)} 天")


def migrate_outcomes(scoring_db: ScoringDatabase, output_dir: Path):
    """迁移收益追踪 CSV → outcomes 表。"""
    outcomes_dir = output_dir / "scoring_outcomes"
    if not outcomes_dir.exists():
        print("  跳过：未找到收益追踪目录")
        return

    csv_files = sorted(outcomes_dir.glob("*.csv"))
    if not csv_files:
        print("  跳过：未找到收益追踪 CSV 文件")
        return

    for csv_path in csv_files:
        date = csv_path.stem
        try:
            df = pd.read_csv(csv_path, dtype={"symbol": str, "score_date": str})
            if df.empty:
                continue
            records = df.to_dict("records")
            scoring_db.save_outcomes(date, records)
        except Exception as exc:
            print(f"  错误：{csv_path.name} → {exc}")

    print(f"  收益追踪：{len(csv_files)} 天")


def migrate_all(root: Path):
    """执行全量迁移。"""
    db_dir = root / "db"
    market_db = MarketDatabase(db_dir / "market.db")
    scoring_db = ScoringDatabase(db_dir / "scoring.db")

    stock_daily_dir = root / "stock_daily_data"
    industry_dir = root / "industry_daily_data"
    stocklist_csv = root / "stocklist.csv"
    output_dir = root / "output"

    start = time.time()
    print("=" * 60)
    print("CSV → SQLite 数据迁移")
    print("=" * 60)

    print("\n[1/7] 迁移个股日线...")
    migrate_stock_daily(market_db, stock_daily_dir)

    print("\n[2/7] 迁移指数日线...")
    migrate_index_daily(market_db, stock_daily_dir)

    print("\n[3/7] 迁移 OAMV 虚拟K线...")
    migrate_oamv(market_db, stock_daily_dir)

    print("\n[4/7] 迁移行业日线...")
    migrate_industry_daily(market_db, industry_dir)

    print("\n[5/7] 迁移股票列表...")
    migrate_stock_list(market_db, stocklist_csv)

    print("\n[6/7] 迁移截面分位...")
    migrate_cross_section(scoring_db, output_dir)

    print("\n[7/7] 迁移收益追踪...")
    migrate_outcomes(scoring_db, output_dir)

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"迁移完成！耗时 {elapsed:.1f}s")
    print(f"  market.db: {db_dir / 'market.db'}")
    print(f"  scoring.db: {db_dir / 'scoring.db'}")

    print("\n数据校验：")
    print(f"  stock_daily 总行数：{market_db.get_total_stock_daily_count()}")
    print(f"  stock_list 条数：{market_db.get_stock_list_count()}")
    print("=" * 60)


if __name__ == "__main__":
    project_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    migrate_all(project_root)
