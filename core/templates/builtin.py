from __future__ import annotations

from core.models.template import ScreeningTemplate


DEFAULT_TEMPLATES: tuple[ScreeningTemplate, ...] = (
    ScreeningTemplate(
        id="template-close-above-ma5",
        name="收盘价高于5日均线",
        description="当日收盘价高于 5 日均线。",
        tdx_source="选股:C > MA(C,5);",
    ),
    ScreeningTemplate(
        id="template-close-above-prev-close",
        name="收盘价高于昨日收盘",
        description="当日收盘价高于上一交易日收盘价。",
        tdx_source="选股:C > REF(C,1);",
    ),
    ScreeningTemplate(
        id="template-high-20day-breakout",
        name="最高价创20日新高",
        description="当日最高价大于等于 20 日最高价。",
        tdx_source="选股:H >= HHV(H,20);",
    ),
    ScreeningTemplate(
        id="template-volume-2x-prev",
        name="成交量大于昨日2倍",
        description="当日成交量大于昨日成交量的 2 倍。",
        tdx_source="选股:V > REF(V,1) * 2;",
    ),
)
