"""SQLite 数据库连接管理与基础操作。

提供 MarketDatabase 和 ScoringDatabase 两个管理类，
分别管理 market.db（行情+元数据）和 scoring.db（评分衍生数据）。
"""

from __future__ import annotations

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pandas as pd

logger = logging.getLogger(__name__)


class DatabaseManager:
    """SQLite 数据库连接管理基类。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self.connect() as conn:
            self._create_tables(conn)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def read_df(self, sql: str, params: list | tuple | None = None) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params or [])

    def write_df(self, df: pd.DataFrame, table: str, if_exists: str = "append") -> int:
        with self.connect() as conn:
            return df.to_sql(table, conn, if_exists=if_exists, index=False)

    def execute(self, sql: str, params: list | tuple | None = None):
        with self.connect() as conn:
            conn.execute(sql, params or [])

    def executemany(self, sql: str, params_list: list[list | tuple]):
        with self.connect() as conn:
            conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: list | tuple | None = None):
        with self.connect() as conn:
            cursor = conn.execute(sql, params or [])
            return cursor.fetchone()

    def _create_tables(self, conn: sqlite3.Connection):
        raise NotImplementedError


class MarketDatabase(DatabaseManager):
    """行情 + 元数据数据库（market.db）。"""

    def _create_tables(self, conn: sqlite3.Connection):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_daily (
                symbol        TEXT    NOT NULL,
                date          TEXT    NOT NULL,
                open          REAL    NOT NULL,
                close         REAL    NOT NULL,
                high          REAL    NOT NULL,
                low           REAL    NOT NULL,
                volume        REAL,
                turnover_rate REAL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE IF NOT EXISTS index_daily (
                ts_code       TEXT    NOT NULL,
                date          TEXT    NOT NULL,
                open          REAL    NOT NULL,
                close         REAL    NOT NULL,
                high          REAL    NOT NULL,
                low           REAL    NOT NULL,
                volume        REAL,
                turnover_rate REAL,
                PRIMARY KEY (ts_code, date)
            );

            CREATE TABLE IF NOT EXISTS oamv_daily (
                date          TEXT    PRIMARY KEY,
                open          REAL    NOT NULL,
                close         REAL    NOT NULL,
                high          REAL    NOT NULL,
                low           REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS industry_daily (
                ts_code       TEXT    NOT NULL,
                date          TEXT    NOT NULL,
                open          REAL    NOT NULL,
                close         REAL    NOT NULL,
                high          REAL    NOT NULL,
                low           REAL    NOT NULL,
                volume        REAL,
                turnover_rate REAL,
                PRIMARY KEY (ts_code, date)
            );

            CREATE TABLE IF NOT EXISTS stock_list (
                symbol        TEXT    PRIMARY KEY,
                ts_code       TEXT,
                name          TEXT,
                area          TEXT,
                industry      TEXT,
                market        TEXT,
                concepts      TEXT,
                ths_industry  TEXT
            );
        """)

    def bulk_upsert_stock_daily(self, symbol: str, df: pd.DataFrame):
        sql = (
            "INSERT OR REPLACE INTO stock_daily "
            "(symbol, date, open, close, high, low, volume, turnover_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        rows = [
            (
                symbol,
                str(row.date),
                float(row.open),
                float(row.close),
                float(row.high),
                float(row.low),
                float(row.volume) if pd.notna(row.volume) else None,
                float(row.turnover_rate) if pd.notna(row.turnover_rate) else None,
            )
            for row in df.itertuples(index=False)
        ]
        self.executemany(sql, rows)

    def bulk_upsert_index_daily(self, ts_code: str, df: pd.DataFrame):
        sql = (
            "INSERT OR REPLACE INTO index_daily "
            "(ts_code, date, open, close, high, low, volume, turnover_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        rows = [
            (
                ts_code,
                str(row.date),
                float(row.open),
                float(row.close),
                float(row.high),
                float(row.low),
                float(row.volume) if pd.notna(row.volume) else None,
                float(row.turnover_rate) if pd.notna(row.turnover_rate) else None,
            )
            for row in df.itertuples(index=False)
        ]
        self.executemany(sql, rows)

    def bulk_upsert_oamv_daily(self, df: pd.DataFrame):
        sql = (
            "INSERT OR REPLACE INTO oamv_daily "
            "(date, open, close, high, low) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        rows = [
            (
                str(row.date),
                float(row.open),
                float(row.close),
                float(row.high),
                float(row.low),
            )
            for row in df.itertuples(index=False)
        ]
        self.executemany(sql, rows)

    def bulk_upsert_industry_daily(self, ts_code: str, df: pd.DataFrame):
        sql = (
            "INSERT OR REPLACE INTO industry_daily "
            "(ts_code, date, open, close, high, low, volume, turnover_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        rows = [
            (
                ts_code,
                str(row.date),
                float(row.open),
                float(row.close),
                float(row.high),
                float(row.low),
                float(row.volume) if pd.notna(row.volume) else None,
                float(row.turnover_rate) if pd.notna(row.turnover_rate) else None,
            )
            for row in df.itertuples(index=False)
        ]
        self.executemany(sql, rows)

    def upsert_stock_list(self, df: pd.DataFrame):
        sql = (
            "INSERT OR REPLACE INTO stock_list "
            "(symbol, ts_code, name, area, industry, market, concepts, ths_industry) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        rows = [
            (
                str(row.get("symbol", "")).zfill(6),
                str(row.get("ts_code", "") or ""),
                str(row.get("name", "") or ""),
                str(row.get("area", "") or ""),
                str(row.get("industry", "") or ""),
                str(row.get("market", "") or ""),
                str(row.get("涉及概念", "") or row.get("concepts", "") or ""),
                str(row.get("涉及行业", "") or row.get("ths_industry", "") or ""),
            )
            for _, row in df.iterrows()
        ]
        self.executemany(sql, rows)

    def update_turnover_rate(self, symbol: str, date: str, turnover_rate: float):
        self.execute(
            "UPDATE stock_daily SET turnover_rate = ? WHERE symbol = ? AND date = ?",
            [turnover_rate, symbol, date],
        )

    def bulk_update_turnover_rate(self, symbol: str, rate_map: dict[str, float]):
        sql = "UPDATE stock_daily SET turnover_rate = ? WHERE symbol = ? AND date = ?"
        rows = [(rate, symbol, date) for date, rate in rate_map.items() if rate is not None]
        if rows:
            self.executemany(sql, rows)

    def get_last_trade_date(self, symbol: str) -> str | None:
        result = self.fetchone(
            "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
            [symbol],
        )
        return result[0] if result and result[0] else None

    def get_index_last_trade_date(self, ts_code: str) -> str | None:
        result = self.fetchone(
            "SELECT MAX(date) FROM index_daily WHERE ts_code = ?",
            [ts_code],
        )
        return result[0] if result and result[0] else None

    def get_industry_last_trade_date(self, ts_code: str) -> str | None:
        result = self.fetchone(
            "SELECT MAX(date) FROM industry_daily WHERE ts_code = ?",
            [ts_code],
        )
        return result[0] if result and result[0] else None

    def get_stock_daily_count(self, symbol: str) -> int:
        result = self.fetchone(
            "SELECT COUNT(*) FROM stock_daily WHERE symbol = ?",
            [symbol],
        )
        return result[0] if result else 0

    def get_total_stock_daily_count(self) -> int:
        result = self.fetchone("SELECT COUNT(*) FROM stock_daily")
        return result[0] if result else 0

    def get_stock_list_count(self) -> int:
        result = self.fetchone("SELECT COUNT(*) FROM stock_list")
        return result[0] if result else 0

    def update_stock_concepts(self, symbol: str, concepts: str):
        self.execute(
            "UPDATE stock_list SET concepts = ? WHERE symbol = ?",
            [concepts, symbol],
        )

    def update_stock_ths_industry(self, symbol: str, ths_industry: str):
        self.execute(
            "UPDATE stock_list SET ths_industry = ? WHERE symbol = ?",
            [ths_industry, symbol],
        )


class ScoringDatabase(DatabaseManager):
    """评分衍生数据数据库（scoring.db）。"""

    def _create_tables(self, conn: sqlite3.Connection):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cross_section (
                date                  TEXT    NOT NULL,
                symbol                TEXT    NOT NULL,
                day_change            REAL,
                day_change_pct        REAL,
                force_ratio           REAL,
                force_ratio_pct       REAL,
                short_trend_slope     REAL,
                short_trend_slope_pct REAL,
                PRIMARY KEY (date, symbol)
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                score_date    TEXT    NOT NULL,
                symbol        TEXT    NOT NULL,
                t1_return     REAL,
                t1_is_green   INTEGER,
                t2_return     REAL,
                t2_is_green   INTEGER,
                t3_return     REAL,
                t3_is_green   INTEGER,
                PRIMARY KEY (score_date, symbol)
            );
        """)

    def save_cross_section(self, date: str, df: pd.DataFrame):
        self.execute("DELETE FROM cross_section WHERE date = ?", [date])
        if df.empty:
            return
        insert_df = df.copy()
        insert_df.insert(0, "date", date)
        self.write_df(insert_df, "cross_section", if_exists="append")

    def load_cross_section(self, date: str) -> pd.DataFrame:
        return self.read_df(
            "SELECT symbol, day_change, day_change_pct, force_ratio, "
            "force_ratio_pct, short_trend_slope, short_trend_slope_pct "
            "FROM cross_section WHERE date = ?",
            [date],
        )

    def save_outcomes(self, score_date: str, records: list[dict]):
        self.execute("DELETE FROM outcomes WHERE score_date = ?", [score_date])
        if not records:
            return

        def _safe_int(v):
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
            return int(v)

        def _safe_float(v):
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
            return float(v)

        sql = (
            "INSERT INTO outcomes "
            "(score_date, symbol, t1_return, t1_is_green, "
            "t2_return, t2_is_green, t3_return, t3_is_green) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        rows = [
            (
                score_date,
                r["symbol"],
                _safe_float(r.get("t1_return")),
                _safe_int(r.get("t1_is_green")),
                _safe_float(r.get("t2_return")),
                _safe_int(r.get("t2_is_green")),
                _safe_float(r.get("t3_return")),
                _safe_int(r.get("t3_is_green")),
            )
            for r in records
        ]
        self.executemany(sql, rows)

    def load_outcomes(self, score_date: str) -> pd.DataFrame:
        return self.read_df(
            "SELECT symbol, score_date, t1_return, t1_is_green, "
            "t2_return, t2_is_green, t3_return, t3_is_green "
            "FROM outcomes WHERE score_date = ?",
            [score_date],
        )


# ── 全局实例管理 ──────────────────────────────────────────

_market_db: MarketDatabase | None = None
_scoring_db: ScoringDatabase | None = None


def init_databases(root: Path):
    """应用启动时调用，初始化全局数据库实例。"""
    global _market_db, _scoring_db
    db_dir = root / "db"
    _market_db = MarketDatabase(db_dir / "market.db")
    _scoring_db = ScoringDatabase(db_dir / "scoring.db")
    logger.info("数据库初始化完成: %s", db_dir)


def get_market_db() -> MarketDatabase:
    if _market_db is None:
        raise RuntimeError("数据库未初始化，请先调用 init_databases()")
    return _market_db


def get_scoring_db() -> ScoringDatabase:
    if _scoring_db is None:
        raise RuntimeError("数据库未初始化，请先调用 init_databases()")
    return _scoring_db
