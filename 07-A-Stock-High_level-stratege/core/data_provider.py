# core/data_provider.py —— 数据获取模块
#
# 提供：
#   get_all_stocks()                   → List[str]   全A股代码列表
#   fetch_fundamentals_batch(codes)    → DataFrame   批量TTM财务指标
#   get_stock_names_bulk(codes)        → dict         股票名称映射
#   fetch_history_em(code, start, end) → DataFrame   日K线（EastMoney）
#   fetch_index_history(code, s, e)    → DataFrame   指数日K线
#   fetch_realtime(codes)              → DataFrame   实时行情
#   get_index_constituents(index_code) → List[str]   指数成分股
#
# 数据源：AKShare（免费，无需注册）
# 缓存策略：当日 CSV/JSON/Parquet 文件复用

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import List, Dict, Optional

import akshare as ak
import pandas as pd
from tqdm.auto import tqdm


def _ak_call(fn, *args, max_retries: int = 4, base_delay: float = 3.0, **kwargs):
    """
    Call an akshare function with exponential-backoff retry on connection errors.
    Keeps proxy env vars intact (this host needs the proxy to reach the internet).
    max_retries and base_delay are consumed here and NOT forwarded to fn.
    """
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            err_str = str(e)
            # Retry only on transient network/proxy errors
            if any(kw in err_str for kw in (
                "RemoteDisconnected", "ConnectionReset", "ConnectionError",
                "ProxyError", "NewConnectionError", "Max retries",
                "timed out", "Network is unreachable",
            )):
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logging.getLogger(__name__).warning(
                        f"网络错误（第{attempt+1}次），{delay:.0f}s 后重试: {e}"
                    )
                    time.sleep(delay)
                    continue
            raise
    raise last_exc

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_HIST_CACHE_DIR = _DATA_DIR / "hist_cache"
_HIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 全A股代码列表
# ─────────────────────────────────────────────────────────────────────────────

def get_all_stocks() -> List[str]:
    """返回今日全A股代码列表（沪深两市，6位数字）。"""
    today = date.today().strftime('%Y%m%d')
    cache = _DATA_DIR / f"spot_snapshot_{today}.csv"

    if cache.exists():
        try:
            df = pd.read_csv(cache, dtype={'代码': str, 'code': str})
            col = '代码' if '代码' in df.columns else 'code'
            codes = df[col].astype(str).str.zfill(6).tolist()
            logger.info(f"全市场快照命中缓存（{len(codes)} 只）")
            return codes
        except Exception:
            pass

    logger.info("获取全A股行情快照...")
    try:
        df = _ak_call(ak.stock_zh_a_spot_em)
    except Exception as e:
        logger.error(f"获取行情快照失败: {e}")
        return []

    df.to_csv(cache, index=False, encoding='utf-8')
    col = '代码' if '代码' in df.columns else 'code'
    codes = df[col].astype(str).str.zfill(6).tolist()
    logger.info(f"全市场快照已缓存（{len(codes)} 只）")
    return codes


# ─────────────────────────────────────────────────────────────────────────────
# 历史K线（EastMoney / 前复权）
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_history_tx(code: str, start: str, end: str) -> pd.DataFrame:
    """
    腾讯行情 API 获取前复权日K线。
    支持个股（sh/sz自动判断）和指数（000xxx → sh，399xxx → sz）。
    """
    import requests

    # 指数前缀判断：000/880/999 系列 → sh；399 系列 → sz；个股按首字符
    if code.startswith("399"):
        prefix = "sz"
    elif code.startswith(("000", "880", "999")):
        prefix = "sh"
    else:
        prefix = "sh" if code[:1] in ("6", "9") else "sz"
    market_code = f"{prefix}{code}"

    import urllib.request, urllib.parse
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_fmt   = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    # _var=kline_dayhfq 触发 JSONP 模式，可获取更长的历史数据（比纯 JSON 模式多）
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urllib.parse.urlencode({
        "_var": "kline_dayhfq",
        "param": f"{market_code},day,{start_fmt},{end_fmt},2000,qfq",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", "ignore")

    # 响应是 JSONP：kline_dayhfq={...}
    json_str = text[text.index("=") + 1:]
    data = json.loads(json_str)

    stock_data = data.get("data", {})
    if not isinstance(stock_data, dict):
        return pd.DataFrame()
    stock_data = stock_data.get(market_code, {})
    # key 优先取 qfqday（按日）或 qfq，否则 day
    rows = stock_data.get("qfqday") or stock_data.get("qfq") or stock_data.get("day") or []
    if not rows:
        return pd.DataFrame()

    # 腾讯格式：[date, open, close, high, low, volume, ...]
    records = []
    for r in rows:
        try:
            records.append({
                "date": r[0], "open": float(r[1]), "close": float(r[2]),
                "high": float(r[3]), "low": float(r[4]), "volume": float(r[5]),
            })
        except Exception:
            continue
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_history_em(
    code: str,
    start: str,
    end: str,
    period: str = "daily",
    adjust: str = "qfq",
) -> pd.DataFrame:
    """
    获取单只股票历史日K线（前复权）。
    优先腾讯行情（proxy 可达），失败时切换东方财富。
    start/end 格式：YYYYMMDD

    返回 DataFrame，index 为 DatetimeIndex，列：open, high, low, close, volume
    缓存：data/hist_cache/{code}_{start}_{end}.parquet（6小时TTL）
    """
    cache = _HIST_CACHE_DIR / f"{code}_{start}_{end}.parquet"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 21600:
        try:
            df = pd.read_parquet(cache)
            df.index = pd.to_datetime(df.index)
            print(f"  [缓存命中] {code} ({len(df)} 行)", flush=True)
            return df
        except Exception:
            pass

    raw = pd.DataFrame()

    # ── 主数据源：腾讯（proxy 可达）──
    try:
        raw = _fetch_history_tx(code, start, end)
    except Exception as e:
        # 只打印错误类型，不打印完整 URL
        err_short = type(e).__name__ + ": " + str(e)[:60]
        logger.warning(f"  [{code}] 腾讯失败 → 切换东方财富 ({err_short})")

    # ── 备用数据源：东方财富 ──
    if raw is None or (hasattr(raw, 'empty') and raw.empty):
        try:
            raw = _ak_call(
                ak.stock_zh_a_hist,
                symbol=code, period=period,
                start_date=start, end_date=end,
                adjust=adjust,
                max_retries=2, base_delay=2.0,
            )
        except Exception as e2:
            err_short = type(e2).__name__ + ": " + str(e2)[:60]
            print(f"  [{code}] 数据获取失败: {err_short}", flush=True)
            return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()

    # 东方财富返回的 raw 需要列名标准化；腾讯的已是标准格式
    if not isinstance(raw.index, pd.DatetimeIndex):
        raw.columns = [c.strip() for c in raw.columns]
        col_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high',  '最低': 'low',  '成交量': 'volume',
            '成交额': 'amount', '涨跌幅': 'pct_chg',
        }
        raw.rename(columns=col_map, inplace=True)
        if 'date' in raw.columns:
            raw['date'] = pd.to_datetime(raw['date'])
            raw.set_index('date', inplace=True)

    needed = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in raw.columns]
    raw = raw[needed].astype(float)

    try:
        raw.to_parquet(cache)
    except Exception:
        pass
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# 指数历史K线
# ─────────────────────────────────────────────────────────────────────────────

def fetch_index_history(
    index_code: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    获取指数历史日K线。index_code 如 '000300'（沪深300）。
    start/end 格式：YYYYMMDD
    返回 DataFrame，index 为 DatetimeIndex，列：open, high, low, close, volume
    """
    cache = _HIST_CACHE_DIR / f"idx_{index_code}_{start}_{end}.parquet"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 21600:
        try:
            df = pd.read_parquet(cache)
            df.index = pd.to_datetime(df.index)
            return df
        except Exception:
            pass

    raw = pd.DataFrame()

    # ── 主数据源：腾讯（指数用 sh 前缀）──
    try:
        raw = _fetch_history_tx(index_code, start, end)
        if raw is not None and not raw.empty:
            logger.debug(f"fetch_index_history({index_code}): 腾讯数据源成功")
    except Exception as e:
        logger.warning(f"fetch_index_history({index_code}) 腾讯失败，切换东方财富: {e}")

    # ── 备用数据源：东方财富 ──
    if raw is None or (hasattr(raw, 'empty') and raw.empty):
        try:
            raw = _ak_call(
                ak.index_zh_a_hist,
                symbol=index_code, period="daily",
                start_date=start, end_date=end,
            )
        except Exception as e:
            logger.error(f"fetch_index_history({index_code}): {e}")
            return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()

    # 东方财富返回的列需标准化；腾讯的已是标准格式
    if not isinstance(raw.index, pd.DatetimeIndex):
        raw.columns = [c.strip() for c in raw.columns]
        col_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high',  '最低': 'low',  '成交量': 'volume',
        }
        raw.rename(columns=col_map, inplace=True)
        if 'date' in raw.columns:
            raw['date'] = pd.to_datetime(raw['date'])
            raw.set_index('date', inplace=True)

    cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in raw.columns]
    raw = raw[cols].astype(float)

    try:
        raw.to_parquet(cache)
    except Exception:
        pass
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# 实时行情（用于模拟交易刷新持仓价格）
# ─────────────────────────────────────────────────────────────────────────────

def fetch_realtime(codes: List[str]) -> pd.DataFrame:
    """
    获取股票实时行情。
    返回 DataFrame，列：code, name, price, change_pct
    """
    try:
        df = _ak_call(ak.stock_zh_a_spot_em)
    except Exception as e:
        logger.error(f"fetch_realtime: {e}")
        return pd.DataFrame(columns=['code', 'name', 'price', 'change_pct'])

    if df is None or df.empty:
        return pd.DataFrame(columns=['code', 'name', 'price', 'change_pct'])

    df.columns = [c.strip() for c in df.columns]
    col_map = {
        '代码': 'code', '名称': 'name',
        '最新价': 'price', '涨跌幅': 'change_pct',
    }
    df.rename(columns=col_map, inplace=True)

    needed = [c for c in ['code', 'name', 'price', 'change_pct'] if c in df.columns]
    df = df[needed].copy()
    if 'code' in df.columns:
        df['code'] = df['code'].astype(str).str.zfill(6)
        code_set = set(codes)
        df = df[df['code'].isin(code_set)]
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 指数成分股
# ─────────────────────────────────────────────────────────────────────────────

def get_index_constituents(index_code: str) -> List[str]:
    """
    获取指数成分股代码列表。index_code 如 '000300'（沪深300）。
    """
    try:
        df = ak.index_stock_cons(symbol=index_code)
    except Exception as e:
        logger.error(f"get_index_constituents({index_code}): {e}")
        return []

    if df is None or df.empty:
        return []

    col = None
    for c in df.columns:
        if '代码' in str(c) or 'code' in str(c).lower() or 'symbol' in str(c).lower():
            col = c
            break
    if col is None:
        col = df.columns[0]

    return df[col].astype(str).str.zfill(6).tolist()


# ─────────────────────────────────────────────────────────────────────────────
# 批量获取TTM财务指标（ROE / ROA / 市值 / PE / PB）
# ─────────────────────────────────────────────────────────────────────────────

def fetch_fundamentals_batch(codes: List[str]) -> pd.DataFrame:
    """
    批量获取TTM财务指标。
    优先使用 data/fundamentals_YYYYMMDD.parquet 当日缓存。
    返回 DataFrame，列：code, roe, roa, market_cap, pe, pb
      - roe/roa：小数（0.15 = 15%）
      - market_cap：亿元
    """
    today = date.today().strftime('%Y%m%d')
    cache = _DATA_DIR / f"fundamentals_{today}.parquet"

    cached_df = pd.DataFrame()
    if cache.exists():
        try:
            cached_df = pd.read_parquet(cache)
        except Exception:
            cached_df = pd.DataFrame()

    if not cached_df.empty and 'code' in cached_df.columns:
        cached_codes = set(cached_df['code'].astype(str).tolist())
        missing = [c for c in codes if c not in cached_codes]
    else:
        missing = list(codes)

    new_rows = []
    if missing:
        logger.info(f"拉取基本面数据：{len(missing)} 只...")
        for code in tqdm(missing, desc="基本面数据"):
            row = _fetch_one_fundamental(code)
            if row:
                new_rows.append(row)

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        cached_df = pd.concat([cached_df, new_df], ignore_index=True) if not cached_df.empty else new_df
        try:
            cached_df.to_parquet(cache, index=False)
        except Exception as e:
            logger.warning(f"基本面缓存写入失败: {e}")

    if cached_df.empty:
        return pd.DataFrame(columns=['code', 'roe', 'roa', 'market_cap', 'pe', 'pb'])

    result = cached_df[cached_df['code'].astype(str).isin(set(str(c) for c in codes))].copy()
    return result


def _fetch_one_fundamental(code: str) -> Optional[Dict]:
    try:
        df = ak.stock_a_indicator_lg(symbol=code)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    df.columns = [c.lower().strip() for c in df.columns]
    latest = df.iloc[-1]

    roe = _safe_float(latest, ['roe', 'roettm', 'roe_ttm'])
    roa = _safe_float(latest, ['roa', 'roattm', 'roa_ttm'])
    pe  = _safe_float(latest, ['pe', 'pe_ttm', 'pettm'])
    pb  = _safe_float(latest, ['pb', 'pb_ttm', 'pbttm', 'pb_mrq'])
    market_cap = _safe_float(latest, ['总市值', 'total_mv', 'market_cap', 'totalmarketcap'])
    if market_cap and market_cap > 1e6:
        market_cap = market_cap / 1e8
    elif market_cap and market_cap > 1e4:
        market_cap = market_cap / 1e4

    if roe is not None and abs(roe) > 2:
        roe = roe / 100.0
    if roa is not None and abs(roa) > 2:
        roa = roa / 100.0

    return {
        'code': str(code), 'roe': roe, 'roa': roa,
        'market_cap': market_cap, 'pe': pe, 'pb': pb,
    }


def _safe_float(row, col_names: list) -> Optional[float]:
    for col in col_names:
        if col in row.index:
            try:
                v = float(row[col])
                if pd.notna(v):
                    return v
            except (ValueError, TypeError):
                continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 批量获取股票名称
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_names_bulk(codes: List[str]) -> Dict[str, str]:
    """
    批量获取股票名称，缓存到 data/name_map_YYYYMMDD.json。
    返回 {code → name} 映射。
    """
    today = date.today().strftime('%Y%m%d')
    cache = _DATA_DIR / f"name_map_{today}.json"

    name_map: Dict[str, str] = {}
    if cache.exists():
        try:
            name_map = json.loads(cache.read_text(encoding='utf-8'))
        except Exception:
            name_map = {}

    missing = [c for c in codes if c not in name_map]
    if not missing:
        return {c: name_map.get(c, c) for c in codes}

    try:
        df_all = ak.stock_info_a_code_name()
        if isinstance(df_all, pd.DataFrame):
            col_code = 'code' if 'code' in df_all.columns else df_all.columns[0]
            col_name = 'name' if 'name' in df_all.columns else df_all.columns[1]
            bulk = dict(zip(df_all[col_code].astype(str), df_all[col_name].astype(str)))
            for c in missing:
                if c in bulk:
                    name_map[c] = bulk[c]
            missing = [c for c in missing if c not in name_map]
    except Exception:
        pass

    for code in missing:
        try:
            info = ak.stock_individual_info_em(symbol=code)
            if isinstance(info, pd.DataFrame):
                row = info[info.iloc[:, 0].astype(str).str.contains('股票简称', na=False)]
                if not row.empty:
                    name_map[code] = str(row.iloc[0, 1]).strip()
        except Exception:
            continue

    try:
        cache.write_text(json.dumps(name_map, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

    return {c: name_map.get(c, c) for c in codes}
