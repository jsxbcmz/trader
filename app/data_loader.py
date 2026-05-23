"""Data IO (shim).

历史上本模块同时定义了纯文件 IO 与缓存逻辑；纯函数已下沉到 ``core.data.io``。
保留此 shim 兼容 app/* 内的现有 import 路径；新代码请直接从 ``core.data.io`` 导入。
"""

from __future__ import annotations

from core.data.io import (  # noqa: F401
    DAILY_COLUMNS,
    _daily_data_cache,
    clear_daily_data_cache,
    get_last_trade_date,
    load_daily_csv,
    load_index_csv,
    load_industry_csv,
    load_industry_mapping,
    load_oamv_csv,
    load_raw_daily_csv,
    load_stock_list,
    normalize_daily_dataframe,
    normalize_symbol,
    save_daily_csv,
)
