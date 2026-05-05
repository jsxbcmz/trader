#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股交易辅助数据自动采集脚本
===============================
无需用户交互，通过命令行参数控制模式和参数，全自动采集并输出结构化报告。

模式 A — 盘前计划：
    python collect.py --mode premarket --stocks "600519,300750,002594,601318"

模式 B — 交易复盘：
    python collect.py --mode review --stock "300750" --date 2026-04-13 \\
        --entry-time "10:02" --entry-price 198.50 \\
        --exit-time "10:20" --exit-price 196.20 \\
        --stop 193.00 --strategy "开盘区间突破"

通用参数：
    --output   输出目录（默认 reports/）
    --top-n    板块排行默认取前5
    --news-n   新闻条数（默认8）
    --no-file  只打印到控制台，不写文件

依赖安装：
    python -m pip install -r scripts/requirements.txt
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: 请先安装依赖: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# 全局配置
# ─────────────────────────────────────────────────────────────

TIMEOUT = 12  # 单次请求超时秒数

HEADERS_SINA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

HEADERS_EM = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.eastmoney.com/",
}

# 主要指数（新浪格式代码）
MAJOR_INDICES = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "沪深300":  "sh000300",
    "中证1000": "sh000852",
    "科创50":   "sh000688",
}

# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def now_cst() -> str:
    """返回当前北京时间字符串"""
    return datetime.now().strftime("%H:%M CST %d %b %Y")


def fmt_pct(v, with_arrow: bool = True) -> str:
    """格式化涨跌幅，带方向符号"""
    try:
        f = float(v)
        arrow = " ▲" if f > 0 else (" ▼" if f < 0 else " —")
        return f"{f:+.2f}%{arrow if with_arrow else ''}"
    except Exception:
        return str(v)


def fmt_amt(yi: float) -> str:
    """亿元格式化"""
    try:
        return f"{float(yi):.1f} 亿"
    except Exception:
        return str(yi)


def normalize_code(code: str) -> str:
    """将各种格式代码统一为新浪格式，如 600519 → sh600519"""
    code = code.strip().replace(" ", "")
    # 已有正确前缀
    if re.match(r"^(sh|sz)\d{6}$", code, re.I):
        return code.lower()
    # 带市场后缀
    code = re.sub(r"\.(SH|sh)$", "", code)
    code = re.sub(r"\.(SZ|sz)$", "", code)
    digits = re.sub(r"\D", "", code)
    if not digits:
        return code.lower()
    if digits.startswith("6"):
        return "sh" + digits
    return "sz" + digits


def safe_get(url: str, headers: dict, params: dict = None, encoding: str = "utf-8") -> Optional[str]:
    """带超时、编码和异常处理的 GET，失败返回 None"""
    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        r.encoding = encoding
        return r.text
    except requests.exceptions.RequestException:
        return None


def safe_get_json(url: str, headers: dict = None, params: dict = None) -> Optional[dict]:
    """带超时和异常处理的 JSON GET"""
    h = headers or HEADERS_EM
    text = safe_get(url, headers=h, params=params, encoding="utf-8")
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def configure_console_output() -> None:
    """尽量将终端输出切换为 UTF-8，避免 Windows 控制台编码报错。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            # 某些终端或重定向场景不支持 reconfigure，保留原编码继续运行。
            pass


def make_console_safe(text: str) -> str:
    """在控制台不支持某些字符时，降级少数字符，避免打印整个报告失败。"""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        replacements = {
            "🔴": "高",
            "🟡": "中",
            "⚪": "低",
        }
        safe_text = text
        for old, new in replacements.items():
            safe_text = safe_text.replace(old, new)
        try:
            safe_text.encode(encoding)
            return safe_text
        except UnicodeEncodeError:
            return safe_text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def safe_print(text: str = "") -> None:
    """兼容 Windows 控制台编码的 print。"""
    print(make_console_safe(str(text)))


# ─────────────────────────────────────────────────────────────
# 计算辅助：Pivot 支撑阻力位
# ─────────────────────────────────────────────────────────────

def calc_pivots(high: float, low: float, close: float) -> dict:
    """
    经典 Pivot 公式（与 a-share/scripts/pivot.py 保持一致）
    P  = (H + L + C) / 3
    R1 = 2P - L    S1 = 2P - H
    R2 = P + (H-L) S2 = P - (H-L)
    """
    p = (high + low + close) / 3
    r = high - low
    return {
        "P":  round(p, 2),
        "R1": round(2 * p - low, 2),
        "R2": round(p + r, 2),
        "S1": round(2 * p - high, 2),
        "S2": round(p - r, 2),
    }


# ─────────────────────────────────────────────────────────────
# 计算辅助：涨停 / 跌停参考价
# ─────────────────────────────────────────────────────────────

def calc_limit_prices(yclose: float, code: str) -> dict:
    """
    按板块规则计算涨跌停参考价（精确到分，四舍五入到最近分）
    - 科创板 (688xxx) / 创业板 (300xxx) / 北交所 (8xxxxx / 4xxxxx): ±20%
    - ST / *ST: ±5%（代码层面无法判断，用 ±10% 兜底，标注'请确认是否ST'）
    - 主板其余: ±10%
    返回: {limit_up, limit_down, pct_label}
    """
    digits = re.sub(r"^(sh|sz)", "", code)
    if digits.startswith("688") or digits.startswith("300") or digits.startswith("8") or digits.startswith("4"):
        pct = 0.20
        label = "±20%"
    else:
        pct = 0.10
        label = "±10%"
    limit_up   = round(yclose * (1 + pct), 2)
    limit_down = round(yclose * (1 - pct), 2)
    return {"limit_up": limit_up, "limit_down": limit_down, "pct_label": label}


# ─────────────────────────────────────────────────────────────
# 计算辅助：盘前优先级自动评分
# ─────────────────────────────────────────────────────────────

def calc_priority(d: dict, flow_data: dict = None) -> str:
    """
    规则化优先级评分，返回 '🔴 高' / '🟡 中' / '⚪ 低'。
    评分维度（满分 12 分）：
      涨跌幅强度 (4 分):
        >= 5%: 4  |  2-5%: 3  |  0-2%: 2  |  负数: 0
      成交额活跃度 (3 分):
        >= 1 亿: 3  |  >= 0.1 亿: 2  |  其余: 1
      今开 vs 前收 (3 分):
        高开 >= 2%: 3  |  平开/小幅: 2  |  低开: 1
      主力净流入 (2 分):
        > 0.5 亿: 2  |  > 0: 1  |  负或暂缺: 0
    High >= 10  |  Mid 7-9  |  Low < 7
    """
    score = 0
    change_pct = float(d.get("change_pct", 0))
    if change_pct >= 5:
        score += 4
    elif change_pct >= 2:
        score += 3
    elif change_pct >= 0:
        score += 2
    else:
        score += 0

    amt = float(d.get("amount_yi", 0))
    if amt >= 1:
        score += 3
    elif amt >= 0.1:
        score += 2
    else:
        score += 1

    yclose = float(d.get("yclose", 0))
    opn    = float(d.get("open", 0))
    gap_pct = (opn - yclose) / yclose * 100 if yclose else 0
    if gap_pct >= 2:
        score += 3
    elif gap_pct >= -1:
        score += 2
    else:
        score += 1

    # 主力净流入维度（需要 flow_data）
    mf = (flow_data or {}).get("主力净流入亿", "暂缺")
    try:
        mf_val = float(mf)
        if mf_val > 0.5:
            score += 2
        elif mf_val > 0:
            score += 1
        # 负流入不加分
    except (TypeError, ValueError):
        pass  # 暂缺时不影响评分

    if score >= 10:
        return "🔴 高"
    elif score >= 7:
        return "🟡 中"
    return "⚪ 低"


# ─────────────────────────────────────────────────────────────
# 模块 1：指数实时报价（新浪）
# ─────────────────────────────────────────────────────────────

def fetch_indices() -> dict:
    """
    获取主要 A 股指数实时报价
    数据源：新浪行情接口 hq.sinajs.cn（最稳定的公开行情接口之一）
    返回：{指数名: {close, change_pct, open, high, low, amount_yi, time}}
    """
    codes = list(MAJOR_INDICES.values())
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    text = safe_get(url, headers=HEADERS_SINA, encoding="gbk")
    result = {}
    if not text:
        return {k: {"error": "接口无响应"} for k in MAJOR_INDICES}

    for name, sina_code in MAJOR_INDICES.items():
        pat = rf'var hq_str_{re.escape(sina_code)}="([^"]*)"'
        m = re.search(pat, text)
        if not m:
            result[name] = {"error": "未找到数据"}
            continue
        fields = m.group(1).split(",")
        try:
            yclose = float(fields[2])
            close  = float(fields[3])
            result[name] = {
                "close":      close,
                "open":       float(fields[1]),
                "high":       float(fields[4]),
                "low":        float(fields[5]),
                "yclose":     yclose,
                "change_pct": round((close - yclose) / yclose * 100, 2) if yclose else 0,
                "amount_yi":  round(float(fields[9]) / 1e8, 1),
                "time":       fields[31] if len(fields) > 31 else "",
                "source":     "新浪财经 (hq.sinajs.cn)",
            }
        except Exception:
            result[name] = {"error": "字段解析失败"}
    return result


# ─────────────────────────────────────────────────────────────
# 模块 2：自选股实时报价（新浪）
# ─────────────────────────────────────────────────────────────

def fetch_stocks(codes: list) -> dict:
    """
    获取指定个股实时报价
    数据源：新浪行情接口
    返回：{sina_code: {name, close, change_pct, open, high, low, volume, amount_yi, time}}
    """
    sina_codes = [normalize_code(c) for c in codes]
    url = "https://hq.sinajs.cn/list=" + ",".join(sina_codes)
    text = safe_get(url, headers=HEADERS_SINA, encoding="gbk")
    result = {}
    if not text:
        return {c: {"error": "接口无响应"} for c in sina_codes}

    for line in text.strip().splitlines():
        m = re.match(r'var hq_str_(\w+)="(.*)"', line)
        if not m:
            continue
        label  = m.group(1)
        fields = m.group(2).split(",")
        if len(fields) < 32 or not fields[0]:
            result[label] = {"error": "无数据（停牌或代码错误）"}
            continue
        try:
            yclose = float(fields[2])
            close  = float(fields[3])
            result[label] = {
                "name":       fields[0],
                "close":      close,
                "open":       float(fields[1]),
                "high":       float(fields[4]),
                "low":        float(fields[5]),
                "yclose":     yclose,
                "change_pct": round((close - yclose) / yclose * 100, 2) if yclose else 0,
                "volume":     int(float(fields[8])),
                "amount_yi":  round(float(fields[9]) / 1e8, 4),
                "time":       fields[31],
                "source":     "新浪财经 (hq.sinajs.cn)",
            }
        except Exception:
            result[label] = {"error": "字段解析失败"}
    return result


# ─────────────────────────────────────────────────────────────
# 模块 3：市场广度（东方财富 datacenter）
# ─────────────────────────────────────────────────────────────

def fetch_market_breadth() -> dict:
    """
    获取全市场涨跌家数、涨停跌停数、两市成交额
    数据源：东方财富 datacenter-web API
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName":  "RPT_MARKET_STAT",
        "columns":     "BOARD_DATE,RISE_COUNT,FALL_COUNT,LIMIT_UP_NUM,LIMIT_DOWN_NUM,"
                       "OPEN_LIMIT_DOWN_NUM,TRADE_AMOUNT,TRADE_VOLUME",
        "filter":      '(TRADE_MARKET_CODE="001001")',
        "pageNumber":  1,
        "pageSize":    1,
        "sortTypes":   -1,
        "sortColumns": "BOARD_DATE",
    }
    data = safe_get_json(url, params=params)
    fallback = {
        "上涨家数": "暂缺", "下跌家数": "暂缺",
        "涨停数":   "暂缺", "跌停数":   "暂缺",
        "两市成交额亿": "暂缺", "source": "东方财富 datacenter-web (暂缺)",
    }
    if not data:
        return fallback
    rows = (data.get("result") or {}).get("data") or []
    if not rows:
        return fallback
    r = rows[0]
    def _int(k):
        v = r.get(k)
        return int(v) if v is not None else "暂缺"
    def _amt(k):
        v = r.get(k)
        return round(float(v) / 1e8, 0) if v else "暂缺"
    return {
        "日期":       r.get("BOARD_DATE", "")[:10],
        "上涨家数":   _int("RISE_COUNT"),
        "下跌家数":   _int("FALL_COUNT"),
        "涨停数":     _int("LIMIT_UP_NUM"),
        "跌停数":     _int("LIMIT_DOWN_NUM"),
        "炸板数":     _int("OPEN_LIMIT_DOWN_NUM"),
        "两市成交额亿": _amt("TRADE_AMOUNT"),
        "source":     "东方财富 (datacenter-web.eastmoney.com)",
    }


# ─────────────────────────────────────────────────────────────
# 模块 3b：情绪/连板统计（东方财富，盘后有效）
# ─────────────────────────────────────────────────────────────

def fetch_emotion_stats() -> dict:
    """
    获取连板统计：最高连板高度、各连板层级家数
    数据源：东方财富 datacenter-web RPT_ZLSJ_CXZT（持续涨停）
    盘中数据可能不完整，盘后（15:30后）最准确。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName":  "RPT_ZLSJ_CXZT",
        "columns":     "SECURITY_CODE,SECURITY_NAME,CONTINUE_DAY,LIMIT_UP_PRICE,"
                       "CLOSE_PRICE,CHANGE_RATE,PCHANGE_RATE,TRADE_DATE",
        "filter":      "",
        "pageNumber":  1,
        "pageSize":    100,
        "sortTypes":   -1,
        "sortColumns": "CONTINUE_DAY",
    }
    data = safe_get_json(url, params=params)
    fallback = {"最高连板": "暂缺", "连板分布": {}, "source": "东方财富 RPT_ZLSJ_CXZT (暂缺)"}
    if not data:
        return fallback
    rows = (data.get("result") or {}).get("data") or []
    if not rows:
        return fallback

    dist: dict[int, int] = {}
    for row in rows:
        try:
            day = int(row.get("CONTINUE_DAY", 0) or 0)
            dist[day] = dist.get(day, 0) + 1
        except Exception:
            continue

    max_day = max(dist.keys()) if dist else 0
    return {
        "最高连板":  max_day,
        "连板分布":  dict(sorted(dist.items(), reverse=True)),
        "source":   "东方财富 (datacenter-web.eastmoney.com/RPT_ZLSJ_CXZT)",
    }


# ─────────────────────────────────────────────────────────────
# 模块 4：行业板块涨跌幅排行（东方财富）
# ─────────────────────────────────────────────────────────────

def fetch_sectors(top_n: int = 5) -> dict:
    """
    获取申万一级行业板块涨跌幅排行
    数据源：东方财富 push2 clist API
    返回：{top: [{name, change_pct, amount_yi}...], bottom: [...]}
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 100, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f8,f6,f20",
    }
    data = safe_get_json(url, params=params)
    if not data:
        return {"top": [], "bottom": [], "source": "东方财富 (暂缺)"}
    items = data.get("data", {}).get("diff") or []
    sectors = []
    for item in items:
        try:
            sectors.append({
                "name":       item["f14"],
                "change_pct": round(float(item.get("f3", 0)), 2),
                "amount_yi":  round(float(item.get("f6", 0)) / 1e8, 1),
            })
        except Exception:
            continue
    sectors.sort(key=lambda x: x["change_pct"], reverse=True)
    return {
        "top":    sectors[:top_n],
        "bottom": sectors[-top_n:][::-1],
        "source": "东方财富 (push2.eastmoney.com)",
    }


# ─────────────────────────────────────────────────────────────
# 模块 5：北向资金（东方财富）
# ─────────────────────────────────────────────────────────────

def fetch_northbound() -> dict:
    """
    获取北向资金（沪股通 + 深股通）今日净流入
    数据源：东方财富 push2 stock API
    secid: 116.518880 = 沪股通  116.518890 = 深股通
    """
    result = {
        "沪股通净流入亿": "暂缺",
        "深股通净流入亿": "暂缺",
        "合计净流入亿":   "暂缺",
        "时间":           now_cst(),
        "source":         "东方财富 (push2.eastmoney.com)",
    }
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    mapping = [("沪股通净流入亿", "116.518880"), ("深股通净流入亿", "116.518890")]
    for key, secid in mapping:
        params = {
            "invt": 2, "fltt": 2,
            "fields": "f57,f58,f135,f136,f137,f169,f170",
            "secid": secid,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        d = safe_get_json(url, params=params)
        if d:
            # f135 = 今日净买入额（元）
            val = (d.get("data") or {}).get("f135")
            if val is not None:
                try:
                    result[key] = round(float(val) / 1e8, 2)
                except Exception:
                    pass
    try:
        a = result["沪股通净流入亿"]
        b = result["深股通净流入亿"]
        if a != "暂缺" and b != "暂缺":
            result["合计净流入亿"] = round(float(a) + float(b), 2)
    except Exception:
        pass
    return result


# ─────────────────────────────────────────────────────────────
# 模块 6：市场热点新闻（东方财富快讯 + 新浪备用）
# ─────────────────────────────────────────────────────────────

def fetch_market_news(n: int = 8) -> list:
    """
    获取市场热点快讯
    主源：东方财富快讯 API
    备用：新浪财经滚动新闻
    返回：[{title, time, url}]
    """
    url = "https://np-cjrj.eastmoney.com/api/BssWeb/Mzx/GetTopArticleList"
    params = {"uid": "", "client": "web", "clientVersion": "", "_": int(time.time() * 1000)}
    data = safe_get_json(url, params=params)
    news = []
    if data:
        items = (data.get("data") or {}).get("list") or []
        for item in items[:n]:
            news.append({
                "title":  item.get("title", ""),
                "time":   item.get("showTime", ""),
                "url":    "https://finance.eastmoney.com/a/" + (item.get("art_code") or ""),
                "source": "东方财富快讯",
            })

    if not news:
        # 备用：新浪财经滚动新闻
        url2 = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=253&lid=2514&num={n}&page=1&r=0"
        data2 = safe_get_json(url2, headers=HEADERS_SINA)
        if data2:
            for item in (data2.get("result") or {}).get("data", [])[:n]:
                news.append({
                    "title":  item.get("title", ""),
                    "time":   item.get("mtime", ""),
                    "url":    item.get("url", ""),
                    "source": "新浪财经",
                })
    return news


# ─────────────────────────────────────────────────────────────
# 模块 7：个股相关公告与新闻（东方财富公告接口）
# ─────────────────────────────────────────────────────────────

def fetch_stock_news(code: str, n: int = 5) -> list:
    """
    获取个股最新公告摘要
    数据源：东方财富公告接口
    """
    raw = re.sub(r"^(sh|sz)", "", normalize_code(code))
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": -1, "page_size": n, "page_index": 1,
        "ann_type": "A,B,H", "client_source": "web",
        "stock_list": raw,
    }
    data = safe_get_json(url, params=params)
    result = []
    if data:
        for item in (data.get("data") or {}).get("list", [])[:n]:
            art_code = item.get("art_code") or ""
            result.append({
                "title":  item.get("title", ""),
                "time":   item.get("notice_date", ""),
                "url":    f"https://data.eastmoney.com/notices/detail/{art_code}.html",
                "source": "东方财富公告",
            })
    return result


# ─────────────────────────────────────────────────────────────
# 辅助：新浪格式代码 → 东方财富 secid
# ─────────────────────────────────────────────────────────────

def to_secid(sina_code: str) -> str:
    """将新浪格式代码转为东方财富 secid 格式，如 sh600519 → 1.600519，sz300750 → 0.300750"""
    code = sina_code.lower().strip()
    digits = re.sub(r"^(sh|sz)", "", code)
    if code.startswith("sh"):
        return "1." + digits
    return "0." + digits


# ─────────────────────────────────────────────────────────────
# 模块 8：个股量比 + 今日资金流向（东方财富）
# ─────────────────────────────────────────────────────────────

def fetch_volume_ratio_and_flow(code: str) -> dict:
    """
    获取个股量比、换手率、今日主力/大单/中单/小单净流入。
    数据源：东方财富 push2 stock API（单次请求，带 fltt=2 返回浮点值）
    字段说明：
      f11 量比  f10 换手率%
      f62 主力净流入(元) = 超大单+大单  f184 主力净占比%
      f66 超大单净流入(元)  f69 大单净流入(元)
      f72 中单净流入(元)    f75 小单净流入(元)
    """
    secid = to_secid(normalize_code(code))
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid":  secid,
        "fields": "f57,f58,f11,f10,f62,f184,f66,f69,f72,f75",
        "ut":     "fa5fd1943c7b386f172d6893dbfba10b",
        "invt":   2,
        "fltt":   2,
    }
    fallback = {
        "量比":           "暂缺",
        "换手率":         "暂缺",
        "主力净流入亿":   "暂缺",
        "主力净占比":     "暂缺",
        "超大单净流入亿": "暂缺",
        "大单净流入亿":   "暂缺",
        "中单净流入亿":   "暂缺",
        "小单净流入亿":   "暂缺",
        "source":         "东方财富 push2 (暂缺)",
    }
    data = safe_get_json(url, params=params)
    if not data:
        return fallback
    d = data.get("data") or {}
    if not d:
        return fallback

    def _f(k):
        v = d.get(k)
        if v is None:
            return "暂缺"
        try:
            return round(float(v), 2)
        except Exception:
            return "暂缺"

    def _yi(k):
        v = d.get(k)
        if v is None:
            return "暂缺"
        try:
            return round(float(v) / 1e8, 2)
        except Exception:
            return "暂缺"

    return {
        "量比":           _f("f11"),
        "换手率":         _f("f10"),
        "主力净流入亿":   _yi("f62"),
        "主力净占比":     _f("f184"),
        "超大单净流入亿": _yi("f66"),
        "大单净流入亿":   _yi("f69"),
        "中单净流入亿":   _yi("f72"),
        "小单净流入亿":   _yi("f75"),
        "source":         "东方财富 (push2.eastmoney.com)",
    }


# ─────────────────────────────────────────────────────────────
# 构建报告
# ─────────────────────────────────────────────────────────────

def build_premarket_report(args) -> str:
    """
    盘前计划报告：采集并输出结构化 Markdown
    """
    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    top_n = args.top_n
    news_n = args.news_n

    print("[1/6] 获取指数行情...")
    indices = fetch_indices()

    print("[2/6] 获取自选股行情...")
    stocks = fetch_stocks(codes)

    print("[3/6] 获取市场广度...")
    breadth = fetch_market_breadth()

    print("[4/6] 获取板块轮动...")
    sectors = fetch_sectors(top_n)

    print("[5/6] 获取北向资金与新闻...")
    nb = fetch_northbound()
    news = fetch_market_news(news_n)

    print("[6/6] 获取个股量比与资金流向...")
    flows: dict = {}
    for raw_code in codes:
        sc = normalize_code(raw_code)
        flows[sc] = fetch_volume_ratio_and_flow(sc)

    gaps = []
    now = now_cst()
    lines = [
        f"<!-- generated: {now} -->",
        f"# A股盘前计划 — {now}",
        "",
        "---",
        "",
        "## 一、大盘环境",
        "",
        "| 指数 | 当前 | 涨跌幅 | 最高 | 最低 | 成交额 | 来源 |",
        "| ---- | ---- | ------ | ---- | ---- | ------ | ---- |",
    ]
    for name, d in indices.items():
        if "error" in d:
            gaps.append(f"{name}: {d['error']}")
            lines.append(f"| {name} | — | — | — | — | — | 暂缺 |")
        else:
            lines.append(
                f"| {name} | {d['close']} | {fmt_pct(d['change_pct'])} "
                f"| {d['high']} | {d['low']} | {fmt_amt(d['amount_yi'])} "
                f"| {d.get('source','')} |"
            )

    lines += [
        "",
        "## 二、市场广度",
        "",
        f"- 上涨家数：{breadth.get('上涨家数', '暂缺')}  下跌家数：{breadth.get('下跌家数', '暂缺')}",
        f"- 涨停数：{breadth.get('涨停数', '暂缺')}  跌停数：{breadth.get('跌停数', '暂缺')}",
        f"- 两市成交额：{fmt_amt(breadth.get('两市成交额亿', '暂缺'))}",
        f"- 数据来源：{breadth.get('source', '暂缺')}",
        "",
        "## 三、板块轮动",
        "",
        "**领涨板块：**",
        "",
        "| 板块 | 涨跌幅 | 成交额 |",
        "| ---- | ------ | ------ |",
    ]
    for s in sectors.get("top", []):
        lines.append(f"| {s['name']} | {fmt_pct(s['change_pct'])} | {fmt_amt(s['amount_yi'])} |")

    lines += [
        "",
        "**领跌板块：**",
        "",
        "| 板块 | 涨跌幅 | 成交额 |",
        "| ---- | ------ | ------ |",
    ]
    for s in sectors.get("bottom", []):
        lines.append(f"| {s['name']} | {fmt_pct(s['change_pct'])} | {fmt_amt(s['amount_yi'])} |")
    lines.append(f"\n数据来源：{sectors.get('source', '暂缺')}")

    lines += [
        "",
        "## 四、北向资金",
        "",
        f"- 沪股通净流入：{fmt_amt(nb.get('沪股通净流入亿', '暂缺'))}",
        f"- 深股通净流入：{fmt_amt(nb.get('深股通净流入亿', '暂缺'))}",
        f"- 合计净流入：{fmt_amt(nb.get('合计净流入亿', '暂缺'))}",
        f"- 更新时间：{nb.get('时间', now)}",
        f"- 数据来源：{nb.get('source', '暂缺')}",
        "",
        "## 五、自选股盘前执行表",
        "",
        "| 标的 | 代码 | 前收 | 当前 | 涨跌幅 | 今开 | 涨停价 | 距涨停 | Pivot P | R1 | S1 | 量比 | 主力流入 | 量能 | 优先级 |",
        "| ---- | ---- | ---- | ---- | ------ | ---- | ------ | ------ | ------- | -- | -- | ---- | -------- | ---- | ------ |",
    ]
    for sina_code in [normalize_code(c) for c in codes]:
        d = stocks.get(sina_code, {})
        if "error" in d:
            gaps.append(f"{sina_code}: {d['error']}")
            lines.append(f"| {sina_code} | — | — | — | — | — | — | — | — | — | — | — | — |")
        else:
            yclose = float(d.get("yclose", 0) or 0)
            close  = float(d.get("close",  0) or 0)
            opn    = float(d.get("open",   0) or 0)
            hi     = float(d.get("high",   0) or 0)
            lo     = float(d.get("low",    0) or 0)

            # 涨停/跌停价
            lp = calc_limit_prices(yclose, sina_code)
            limit_up = lp["limit_up"]
            # 出场价到涨停的剩余空间
            gap_to_limit = f"{(limit_up - close) / close * 100:.1f}%" if close else "—"

            # Pivot（用昨日 OHLC：open/high/low/yclose 近似）
            if hi and lo and yclose:
                pv = calc_pivots(hi, lo, yclose)
                p_str  = str(pv["P"])
                r1_str = str(pv["R1"])
                s1_str = str(pv["S1"])
            else:
                p_str = r1_str = s1_str = "—"

            flow = flows.get(sina_code, {})
            priority = calc_priority(d, flow_data=flow)
            lines.append(
                f"| {d.get('name','')} | {sina_code} | {yclose} "
                f"| {close} | {fmt_pct(d.get('change_pct',0))} "
                f"| {opn} | {limit_up}({lp['pct_label']}) | {gap_to_limit} "
                f"| {p_str} | {r1_str} | {s1_str} "
                f"| {flow.get('量比', '—')} | {fmt_amt(flow.get('主力净流入亿', '—'))} "
                f"| {fmt_amt(d.get('amount_yi',0))} | {priority} |"
            )

    # 为所有票拉公告（限每只 2 条避免报告过长）
    if codes:
        lines += ["", "## 五-附：个股近期公告", ""]
        for raw_code in codes:
            sc = normalize_code(raw_code)
            name = stocks.get(sc, {}).get("name", sc)
            ann = fetch_stock_news(sc, 2)
            if ann:
                lines.append(f"**{name}：**")
                for n_item in ann:
                    lines.append(f"- [{n_item['title']}]({n_item['url']}) — {n_item['time']}")
                lines.append("")

    # 个股资金流向详情
    if flows:
        lines += ["", "## 五-bis：个股资金流向", "",
                  "| 标的 | 量比 | 换手率% | 主力净流入 | 主力净占比% | 超大单 | 大单 | 中单 | 小单 | 来源 |",
                  "| ---- | ---- | ------- | ---------- | ----------- | ------ | ---- | ---- | ---- | ---- |"]
        for raw_code in codes:
            sc = normalize_code(raw_code)
            name = stocks.get(sc, {}).get("name", sc)
            f = flows.get(sc, {})
            if f.get("量比") == "暂缺" and f.get("主力净流入亿") == "暂缺":
                gaps.append(f"{name} 资金流向：接口无响应")
            lines.append(
                f"| {name} | {f.get('量比','—')} | {f.get('换手率','—')} "
                f"| {fmt_amt(f.get('主力净流入亿','—'))} | {f.get('主力净占比','—')} "
                f"| {fmt_amt(f.get('超大单净流入亿','—'))} | {fmt_amt(f.get('大单净流入亿','—'))} "
                f"| {fmt_amt(f.get('中单净流入亿','—'))} | {fmt_amt(f.get('小单净流入亿','—'))} "
                f"| {f.get('source','暂缺')} |"
            )
        lines.append("")

    lines += [
        "",
        "## 六、市场新闻（近期快讯）",
        "",
    ]
    if news:
        for n_item in news:
            lines.append(f"- **{n_item.get('time','')}** [{n_item['title']}]({n_item['url']}) — {n_item.get('source','')}")
    else:
        gaps.append("市场快讯：接口无响应")
        lines.append("暂缺 — 请直接查看 [东方财富快讯](https://kuaixun.eastmoney.com/)")

    lines += [
        "",
        "## 七、数据可得性与缺口",
        "",
    ]
    if gaps:
        for g in gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- 本次采集无明显缺口")

    lines += [
        "",
        "---",
        "",
        "> 本报告由脚本自动采集，仅用于辅助分析，不构成投资建议。",
    ]
    return "\n".join(lines)


def build_review_report(args) -> str:
    """
    交易复盘报告：采集交易背景数据并输出结构化 Markdown
    """
    stock_code = normalize_code(args.stock)
    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    entry_time  = getattr(args, "entry_time", "")
    exit_time   = getattr(args, "exit_time", "")
    entry_price = getattr(args, "entry_price", None)
    exit_price  = getattr(args, "exit_price", None)
    stop_price  = getattr(args, "stop", None)
    strategy    = getattr(args, "strategy", "未指定")

    print("[1/5] 获取标的行情...")
    stocks = fetch_stocks([stock_code])
    stock_info = stocks.get(stock_code, {})

    print("[2/5] 获取指数环境...")
    indices = fetch_indices()

    print("[3/5] 获取北向资金与板块...")
    nb = fetch_northbound()
    sectors = fetch_sectors(3)

    print("[4/5] 获取个股公告与新闻...")
    stock_news = fetch_stock_news(stock_code, 5)

    print("[5/5] 获取个股量比与资金流向...")
    flow = fetch_volume_ratio_and_flow(stock_code)

    gaps = []
    now = now_cst()

    # 计算盈亏
    pnl_pct = ""
    if entry_price and exit_price:
        pnl = round((float(exit_price) - float(entry_price)) / float(entry_price) * 100, 2)
        pnl_pct = f"{pnl:+.2f}%"

    lines = [
        f"<!-- generated: {now} -->",
        f"# A股交易复盘 — {stock_info.get('name', stock_code)} {trade_date}",
        "",
        "---",
        "",
        "## 一、交易信息",
        "",
        f"| 字段 | 值 |",
        f"| ---- | -- |",
        f"| 标的 | {stock_info.get('name', stock_code)} ({stock_code}) |",
        f"| 日期 | {trade_date} |",
        f"| 策略类型 | {strategy} |",
        f"| 进场时间 | {entry_time or '未填写'} |",
        f"| 进场价格 | {entry_price or '未填写'} |",
        f"| 出场时间 | {exit_time or '未填写'} |",
        f"| 出场价格 | {exit_price or '未填写'} |",
        f"| 计划止损 | {stop_price or '未填写'} |",
        f"| 实际盈亏 | {pnl_pct or '未计算'} |",
        "",
        "## 二、标的当日行情",
        "",
    ]

    if "error" in stock_info:
        gaps.append(f"标的行情: {stock_info['error']}")
        lines.append("标的行情暂缺，请直接查看东方财富或同花顺。")
    else:
        yclose = stock_info.get("yclose", 0)
        close  = stock_info.get("close", 0)
        limit_up_price   = round(yclose * 1.2, 2) if yclose else "—"
        limit_down_price = round(yclose * 0.8, 2) if yclose else "—"
        space_to_limit = "—"
        if exit_price and yclose:
            try:
                space_to_limit = f"{round((float(limit_up_price) - float(exit_price)) / float(exit_price) * 100, 2)}%"
            except Exception:
                pass

        lines += [
            f"| 字段 | 值 |",
            f"| ---- | -- |",
            f"| 当前价 | {close} |",
            f"| 前收价 | {yclose} |",
            f"| 当日涨跌幅 | {fmt_pct(stock_info.get('change_pct', 0))} |",
            f"| 今日最高 | {stock_info.get('high', '—')} |",
            f"| 今日最低 | {stock_info.get('low', '—')} |",
            f"| 涨停价参考 | {limit_up_price} |",
            f"| 跌停价参考 | {limit_down_price} |",
            f"| 出场时距涨停空间 | {space_to_limit} |",
            f"| 数据来源 | {stock_info.get('source', '暂缺')} |",
        ]

    # 资金流向（复盘背景）
    if flow.get("量比") != "暂缺" or flow.get("主力净流入亿") != "暂缺":
        lines += [
            "",
            "### 当日资金流向",
            "",
            f"| 指标 | 值 |",
            f"| ---- | -- |",
            f"| 量比 | {flow.get('量比', '—')} |",
            f"| 换手率% | {flow.get('换手率', '—')} |",
            f"| 主力净流入 | {fmt_amt(flow.get('主力净流入亿', '—'))} |",
            f"| 主力净占比% | {flow.get('主力净占比', '—')} |",
            f"| 超大单净流入 | {fmt_amt(flow.get('超大单净流入亿', '—'))} |",
            f"| 大单净流入 | {fmt_amt(flow.get('大单净流入亿', '—'))} |",
            f"| 中单净流入 | {fmt_amt(flow.get('中单净流入亿', '—'))} |",
            f"| 小单净流入 | {fmt_amt(flow.get('小单净流入亿', '—'))} |",
            f"| 数据来源 | {flow.get('source', '暂缺')} |",
        ]
    else:
        gaps.append("个股资金流向：接口无响应")

    lines += [
        "",
        "## 三、市场背景（复盘参考）",
        "",
        "| 指数 | 当日涨跌幅 | 来源 |",
        "| ---- | ---------- | ---- |",
    ]
    for name, d in indices.items():
        if "error" in d:
            gaps.append(f"{name}: {d['error']}")
        else:
            lines.append(f"| {name} | {fmt_pct(d.get('change_pct', 0))} | {d.get('source','')} |")

    lines += [
        "",
        f"**北向资金：**",
        f"- 合计净流入：{fmt_amt(nb.get('合计净流入亿', '暂缺'))}",
        f"- 沪股通：{fmt_amt(nb.get('沪股通净流入亿', '暂缺'))}  深股通：{fmt_amt(nb.get('深股通净流入亿', '暂缺'))}",
        f"- 来源：{nb.get('source', '暂缺')}",
        "",
        "**强势板块（仅作参考）：**",
        "",
        "| 板块 | 涨跌幅 | 成交额 |",
        "| ---- | ------ | ------ |",
    ]
    for s in sectors.get("top", []):
        lines.append(f"| {s['name']} | {fmt_pct(s['change_pct'])} | {fmt_amt(s['amount_yi'])} |")

    lines += [
        "",
        "## 四、个股近期公告",
        "",
    ]
    if stock_news:
        for item in stock_news:
            lines.append(f"- [{item['title']}]({item['url']}) — {item['time']}")
    else:
        gaps.append("个股公告：接口无响应")
        lines.append(f"暂缺 — 请查看 [东方财富公告](https://data.eastmoney.com/notices/)")

    lines += [
        "",
        "## 五、复盘结构（待填写）",
        "",
        "| 维度 | 内容 |",
        "| ---- | ---- |",
        "| 事实依据 |  |",
        "| 规则检查 |  |",
        "| 违规点 |  |",
        "| 心理模式 |  |",
        "| 可量化改进动作 |  |",
        "",
        "**执行力评分：**",
        "",
        "| 维度 | 评分 |",
        "| ---- | ---- |",
        "| 进场执行力 | /10 |",
        "| 出场执行力 | /10 |",
        "| 情绪管控力 | /10 |",
        "| 综合纪律评分 | /10 |",
        "",
        "## 六、数据可得性与缺口",
        "",
    ]
    if gaps:
        for g in gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- 本次采集无明显缺口")

    lines += [
        "",
        "---",
        "",
        "> 本报告由脚本自动采集，仅用于辅助分析，不构成投资建议。",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────

def main():
    configure_console_output()
    parser = argparse.ArgumentParser(
        description="A股交易辅助数据自动采集脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # ── 盘前计划 ──
    pm = sub.add_parser("premarket", aliases=["pm"], help="盘前计划数据采集")
    stock_group = pm.add_mutually_exclusive_group(required=True)
    stock_group.add_argument(
        "--stocks",
        help='自选股代码，逗号分隔，如 "600519,300750,002594"',
    )
    stock_group.add_argument(
        "--watchlist",
        nargs="?", const="default",
        metavar="GROUP",
        help='读取 watchlist.json 中的分组（默认 "default"），如 --watchlist core_index',
    )
    pm.add_argument("--top-n",  type=int, default=5, help="板块排行取前N（默认5）")
    pm.add_argument("--news-n", type=int, default=8, help="新闻条数（默认8）")
    pm.add_argument("--output", default="reports",  help="输出目录（默认 reports/）")
    pm.add_argument("--no-file", action="store_true", help="只打印到终端，不写文件")

    # ── 交易复盘 ──
    rv = sub.add_parser("review", aliases=["rv"], help="交易复盘背景数据采集")
    rv.add_argument("--stock",        required=True, help="单只标的代码，如 300750")
    rv.add_argument("--date",         default=None,  help="交易日期，如 2026-04-13")
    rv.add_argument("--entry-time",   default="",    help="进场时间，如 10:02")
    rv.add_argument("--entry-price",  type=float,    default=None, help="进场价格")
    rv.add_argument("--exit-time",    default="",    help="出场时间，如 10:20")
    rv.add_argument("--exit-price",   type=float,    default=None, help="出场价格")
    rv.add_argument("--stop",         type=float,    default=None, help="计划止损价")
    rv.add_argument("--strategy",     default="未指定", help="策略类型")
    rv.add_argument("--top-n",  type=int, default=3, help="板块排行取前N（默认3）")
    rv.add_argument("--news-n", type=int, default=5, help="新闻条数（默认5）")
    rv.add_argument("--output", default="reports",  help="输出目录（默认 reports/）")
    rv.add_argument("--no-file", action="store_true", help="只打印到终端，不写文件")

    args = parser.parse_args()

    # 生成报告
    if args.mode in ("premarket", "pm"):
        # 解析自选股：--stocks 或 --watchlist
        if getattr(args, "watchlist", None) is not None:
            wl_path = Path(__file__).parent / "watchlist.json"
            if not wl_path.exists():
                print(f"ERROR: watchlist.json 不存在: {wl_path}")
                sys.exit(1)
            with open(wl_path, encoding="utf-8") as f:
                wl_data = json.load(f)
            group = args.watchlist or "default"
            if group not in wl_data:
                print(f"ERROR: watchlist.json 中没有 '{group}' 分组。可用分组: {[k for k in wl_data if not k.startswith('_')]}")
                sys.exit(1)
            args.stocks = ",".join(wl_data[group].get("stocks", []))
            safe_print(f"[INFO] 使用 watchlist 分组 '{group}'：{args.stocks}")
        safe_print("=== A股盘前计划数据采集 ===")
        report = build_premarket_report(args)
        suffix = "盘前计划"
    else:
        safe_print("=== A股交易复盘数据采集 ===")
        report = build_review_report(args)
        suffix = f"复盘_{getattr(args,'stock','')}"

    # 输出
    safe_print("\n" + "─" * 60)
    safe_print(report)
    safe_print("─" * 60 + "\n")

    if not args.no_file:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_file = out_dir / f"{date_str}_{suffix}.md"
        # 如文件已存在，追加数字后缀
        counter = 2
        while out_file.exists():
            out_file = out_dir / f"{date_str}_{suffix}_{counter}.md"
            counter += 1
        out_file.write_text(report, encoding="utf-8")
        safe_print(f"报告已保存：{out_file}")


if __name__ == "__main__":
    main()
