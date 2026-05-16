"""统计页：后台 Worker。"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6 import QtCore

from app.data_loader import get_last_trade_date
from app.history_updater import HistoryUpdater
from app.stats import (
    ApiRequester,
    ApiResponse,
    ConfigLoader,
    DataAnalyzer,
    DataStorage,
)

from .constants import API_ID_MAP

logger = logging.getLogger(__name__)


class CollectWorker(QtCore.QObject):
    """在后台线程中执行 API 采集任务"""

    logMessage = QtCore.Signal(str, str)          # (message, level)
    apiStart = QtCore.Signal(str, int)             # (api_id, total)
    apiProgress = QtCore.Signal(str, int, int, float)  # (api_id, current, total, elapsed)
    apiDone = QtCore.Signal(str, int, float)       # (api_id, count, elapsed)
    apiCached = QtCore.Signal(str)                 # (api_id,)
    apiError = QtCore.Signal(str)                  # (api_id,)
    allDone = QtCore.Signal(str)                   # (report_text,)

    def __init__(self):
        super().__init__()

    def _progress_callback(self, api_name: str, event_type: str, **kwargs):
        """requester 的进度回调，转发为 Qt 信号"""
        api_id = API_ID_MAP.get(api_name, api_name)

        if event_type == "log":
            self.logMessage.emit(kwargs.get("message", ""), kwargs.get("level", "info"))
        elif event_type == "api_start":
            self.apiStart.emit(api_id, kwargs.get("total", 0))
        elif event_type == "progress":
            self.apiProgress.emit(
                api_id,
                kwargs.get("current", 0),
                kwargs.get("total", 0),
                kwargs.get("elapsed", 0.0),
            )
        elif event_type == "api_done":
            self.apiDone.emit(api_id, kwargs.get("count", 0), kwargs.get("elapsed", 0.0))
        elif event_type == "api_error":
            self.apiError.emit(api_id)

    @QtCore.Slot()
    def run(self):
        try:
            config_loader = ConfigLoader()
            api_configs, settings = config_loader.load()
            storage = DataStorage()

            self.logMessage.emit(f"加载了 {len(api_configs)} 个接口配置", "info")

            apis_to_request = []
            for api_config in api_configs:
                api_id = API_ID_MAP.get(api_config.name, api_config.name)
                if storage.is_cache_valid(api_config.output_file):
                    self.apiCached.emit(api_id)
                    self.logMessage.emit(f"[{api_config.name}] 当天缓存有效，跳过", "success")
                else:
                    apis_to_request.append(api_config)

            if not apis_to_request:
                self.logMessage.emit("所有接口数据均为当天缓存，无需重新请求", "success")
                self.allDone.emit("")
                return

            self.logMessage.emit(f"开始请求 {len(apis_to_request)} 个接口...", "info")

            requester = ApiRequester(settings, progress_callback=self._progress_callback)
            responses: list = []
            try:
                for api_config in apis_to_request:
                    if api_config.pagination.enabled:
                        response = requester._request_paginated(api_config)
                    elif api_config.batch.enabled:
                        response = requester._request_batched(api_config)
                    else:
                        response = requester._request_single(api_config)
                    response.output_file = api_config.output_file
                    responses.append(response)

                    # 逐个保存，确保后续接口能读到前置接口的输出
                    if response.success:
                        saved_paths = storage.save_responses([response])
                        for path in saved_paths:
                            self.logMessage.emit(f"已保存: {path}", "success")
                    else:
                        self.logMessage.emit(
                            f"跳过失败接口: {api_config.name}", "warning",
                        )
            finally:
                requester.close()

            analyzer = DataAnalyzer()
            report = analyzer.analyze(responses)
            report_text = analyzer.format_report(report)

            self.logMessage.emit(
                f"采集完成 | 成功: {report.success_count}/{report.total_apis} | 平均耗时: {report.average_elapsed_seconds}s",
                "success",
            )
            self.allDone.emit(report_text)

        except Exception as error:
            self.logMessage.emit(f"任务异常: {error}", "error")
            self.allDone.emit("")


# ═══════════════════════════════════════════════════════════════════════════
#  进度卡片 Widget
# ═══════════════════════════════════════════════════════════════════════════


class SingleStockUpdateWorker(QtCore.QObject):
    """在后台线程中更新单只股票的日线数据"""

    finished = QtCore.Signal(bool, str)  # (success, message)

    def __init__(self, symbol: str, stocklist_csv: Path, stock_daily_data_dir: Path):
        super().__init__()
        self._symbol = symbol
        self._stocklist_csv = stocklist_csv
        self._stock_daily_data_dir = stock_daily_data_dir

    @QtCore.Slot()
    def run(self):
        try:
            updater = HistoryUpdater(self._stocklist_csv, self._stock_daily_data_dir)
            result = updater.update_symbol(self._symbol)
            if result.status == "failed":
                self.finished.emit(False, result.message)
            else:
                self.finished.emit(True, result.message)
        except Exception as error:
            self.finished.emit(False, str(error))


# ═══════════════════════════════════════════════════════════════════════════
#  统计页面主 Widget
# ═══════════════════════════════════════════════════════════════════════════
