"""统计页拆分模块。"""
from .constants import (
    API_DISPLAY_NAMES,
    API_ID_MAP,
    OPERATION_MAP,
    OPERATION_SORT_ORDER,
    _char_initial,
    _name_initials,
)
from .dialogs import RateDetailDialog, StockPreviewDialog
from .widgets import ApiCard, OperationTag, PositionsTable
from .workers import CollectWorker, SingleStockUpdateWorker

__all__ = [
    "ApiCard",
    "API_DISPLAY_NAMES",
    "API_ID_MAP",
    "CollectWorker",
    "OPERATION_MAP",
    "OPERATION_SORT_ORDER",
    "OperationTag",
    "PositionsTable",
    "RateDetailDialog",
    "SingleStockUpdateWorker",
    "StockPreviewDialog",
    "_char_initial",
    "_name_initials",
]
