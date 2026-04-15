from __future__ import annotations

import json
import os
import time
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import requests

from .config_loader import ApiConfig, Settings

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")


@dataclass
class ApiResponse:
    """单个接口的响应结果"""
    api_name: str
    url: str
    status_code: int
    success: bool
    response_data: Any = None
    error_message: Optional[str] = None
    elapsed_seconds: float = 0.0
    headers: dict = field(default_factory=dict)
    output_file: str = ""


class ApiRequester:
    """接口请求引擎，负责按顺序请求接口并收集响应"""

    def __init__(self, settings: Settings, progress_callback: Optional[Callable] = None):
        self.settings = settings
        self.session = requests.Session()
        self._on_progress = progress_callback

    def _notify(self, api_name: str, event_type: str, **kwargs):
        """触发进度回调"""
        if self._on_progress:
            self._on_progress(api_name, event_type, **kwargs)

    def request_all(self, api_configs: list[ApiConfig]) -> list[ApiResponse]:
        """按顺序请求所有接口，返回响应列表"""
        responses: list[ApiResponse] = []
        total = len(api_configs)

        for index, api_config in enumerate(api_configs, start=1):
            logger.info(f"[{index}/{total}] 正在请求: {api_config.name} ({api_config.url})")

            if api_config.pagination.enabled:
                response = self._request_paginated(api_config)
            elif api_config.batch.enabled:
                response = self._request_batched(api_config)
            else:
                response = self._request_single(api_config)

            response.output_file = api_config.output_file
            responses.append(response)

            if response.success:
                logger.info(f"  ✅ 成功 | 状态码: {response.status_code} | 耗时: {response.elapsed_seconds:.2f}s")
            else:
                logger.warning(f"  ❌ 失败 | 错误: {response.error_message}")

            if index < total:
                time.sleep(self.settings.delay_between_requests)

        return responses

    def _request_paginated(self, api_config: ApiConfig) -> ApiResponse:
        """分页请求接口，自动翻页并汇总所有数据"""
        pagination = api_config.pagination
        all_items: list = []
        metadata: dict = {}
        page = 1
        total_elapsed = 0.0
        last_status_code = 200
        max_count = 0

        self._notify(api_config.name, "api_start", total=0)
        self._notify(api_config.name, "log", message=f"[{api_config.name}] 开始分页请求...", level="info")

        while True:
            page_params = {**self._resolve_params(api_config.params), pagination.page_param: str(page)}
            for param_key, param_template in pagination.dynamic_params.items():
                page_params[param_key] = param_template.replace("{page}", str(page))

            page_config = ApiConfig(
                name=f"{api_config.name}_page{page}",
                url=api_config.url,
                method=api_config.method,
                headers=api_config.headers,
                params=page_params,
                body=api_config.body,
            )

            logger.info(f"  📄 请求第 {page} 页...")
            response = self._request_single(page_config)
            total_elapsed += response.elapsed_seconds
            last_status_code = response.status_code

            if not response.success:
                logger.warning(f"  第 {page} 页请求失败: {response.error_message}")
                self._notify(api_config.name, "api_error")
                break

            response_json = response.response_data

            if page == 1:
                for field_name, field_path in pagination.metadata_fields.items():
                    metadata[field_name] = self._extract_by_path(response_json, field_path)
                max_count = metadata.get("max_count", 0) or 0
                logger.info(f"  总数据量: {max_count}, 总页数: {metadata.get('max_page', '未知')}")
                self._notify(api_config.name, "api_start", total=max_count)
                self._notify(api_config.name, "log", message=f"[{api_config.name}] 总数据量: {max_count}", level="info")

            items = self._extract_by_path(response_json, pagination.data_path)
            if not isinstance(items, list):
                items = []

            fetched_count = len(items)
            logger.info(f"  第 {page} 页获取到 {fetched_count} 条数据")

            if pagination.keep_fields:
                items = [
                    {key: item.get(key) for key in pagination.keep_fields}
                    for item in items
                    if isinstance(item, dict)
                ]

            all_items.extend(items)

            self._notify(api_config.name, "progress",
                         current=len(all_items), total=max_count,
                         elapsed=round(total_elapsed, 1))

            if fetched_count < pagination.page_size:
                logger.info(f"  已获取全部数据（共 {len(all_items)} 条）")
                break

            page += 1
            time.sleep(self.settings.delay_between_requests)

        self._notify(api_config.name, "api_done",
                     count=len(all_items), elapsed=round(total_elapsed, 1))
        self._notify(api_config.name, "log",
                     message=f"[{api_config.name}] 完成，共 {len(all_items)} 条，耗时 {round(total_elapsed, 1)}s",
                     level="success")

        aggregated_data = {
            **metadata,
            "total_count": len(all_items),
            "rank_list": all_items,
        }

        return ApiResponse(
            api_name=api_config.name,
            url=api_config.url,
            status_code=last_status_code,
            success=len(all_items) > 0,
            response_data=aggregated_data,
            elapsed_seconds=round(total_elapsed, 3),
        )

    def _request_batched(self, api_config: ApiConfig) -> ApiResponse:
        """批量遍历参数请求接口，合并所有结果"""
        batch = api_config.batch
        all_items: list = []
        total_elapsed = 0.0
        last_status_code = 200
        fail_count = 0

        source_path = os.path.join(OUTPUT_DIR, batch.source_file)
        try:
            with open(source_path, "r", encoding="utf-8") as source_file:
                source_data = json.load(source_file)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            self._notify(api_config.name, "api_error")
            return ApiResponse(
                api_name=api_config.name,
                url=api_config.url,
                status_code=-1,
                success=False,
                error_message=f"读取批量参数源文件失败: {error}",
            )

        # 兼容新格式（带元信息 {collected_date, data: [...]})和旧格式（纯数组 [...]）
        if isinstance(source_data, dict) and "data" in source_data:
            source_data = source_data["data"]

        param_values: list[str] = []
        if isinstance(source_data, list):
            for item in source_data:
                if isinstance(item, dict) and batch.source_field in item:
                    param_values.append(str(item[batch.source_field]))
                elif isinstance(item, (str, int, float)):
                    param_values.append(str(item))

        total_keys = len(param_values)
        logger.info(f"  从 {batch.source_file} 读取到 {total_keys} 个参数值")

        self._notify(api_config.name, "api_start", total=total_keys)
        self._notify(api_config.name, "log",
                     message=f"[{api_config.name}] 开始批量请求，共 {total_keys} 个用户...",
                     level="info")

        resolved_params = self._resolve_params(api_config.params)

        for index, value in enumerate(param_values, start=1):
            item_params = {**resolved_params, batch.param_name: value}

            item_config = ApiConfig(
                name=f"{api_config.name}_{batch.param_name}={value}",
                url=api_config.url,
                method=api_config.method,
                headers=api_config.headers,
                params=item_params,
                body=api_config.body,
            )

            if index % 50 == 1 or index == total_keys:
                logger.info(f"  🔄 请求进度: {index}/{total_keys} (user_key={value})")
                self._notify(api_config.name, "log",
                             message=f"[{api_config.name}] 进度: {index}/{total_keys}",
                             level="info")

            response = self._request_single(item_config)
            total_elapsed += response.elapsed_seconds
            last_status_code = response.status_code

            if not response.success:
                fail_count += 1
                if fail_count <= 3:
                    logger.warning(f"  user_key={value} 请求失败: {response.error_message}")
                continue

            items = self._extract_by_path(response.response_data, batch.data_path)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        item[batch.param_name] = value
                if batch.merge_mode == "flatten":
                    all_items.extend(items)
                else:
                    all_items.append(items)

            self._notify(api_config.name, "progress",
                         current=index, total=total_keys,
                         elapsed=round(total_elapsed, 1))

            time.sleep(self.settings.delay_between_requests)

        logger.info(f"  批量请求完成: 成功 {total_keys - fail_count}/{total_keys}, 合并数据 {len(all_items)} 条")

        self._notify(api_config.name, "api_done",
                     count=len(all_items), elapsed=round(total_elapsed, 1))
        self._notify(api_config.name, "log",
                     message=f"[{api_config.name}] 完成，合并 {len(all_items)} 条数据，耗时 {round(total_elapsed, 1)}s",
                     level="success")

        return ApiResponse(
            api_name=api_config.name,
            url=api_config.url,
            status_code=last_status_code,
            success=len(all_items) > 0,
            response_data=all_items,
            elapsed_seconds=round(total_elapsed, 3),
        )

    @staticmethod
    def _resolve_params(params: dict) -> dict:
        """解析参数中的动态占位符，如 {today}"""
        resolved = {}
        today_str = datetime.now().strftime("%Y%m%d")
        for key, value in params.items():
            if isinstance(value, str) and "{today}" in value:
                resolved[key] = value.replace("{today}", today_str)
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _extract_by_path(data: Any, path: str) -> Any:
        """根据点分路径从嵌套字典中提取值"""
        if not path or not isinstance(data, dict):
            return None
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def _request_single(self, api_config: ApiConfig) -> ApiResponse:
        """请求单个接口，支持重试"""
        last_error = None

        for attempt in range(1, self.settings.retry_count + 1):
            try:
                return self._do_request(api_config)
            except Exception as error:
                last_error = error
                if attempt < self.settings.retry_count:
                    time.sleep(self.settings.retry_delay)

        return ApiResponse(
            api_name=api_config.name,
            url=api_config.url,
            status_code=-1,
            success=False,
            error_message=f"重试 {self.settings.retry_count} 次后仍失败: {last_error}",
        )

    def _do_request(self, api_config: ApiConfig) -> ApiResponse:
        """执行单次 HTTP 请求"""
        start_time = time.time()

        request_kwargs: dict[str, Any] = {
            "url": api_config.url,
            "headers": api_config.headers,
            "params": api_config.params,
            "timeout": self.settings.timeout,
        }

        if api_config.method in ("POST", "PUT", "PATCH") and api_config.body is not None:
            request_kwargs["json"] = api_config.body

        raw_response = self.session.request(method=api_config.method, **request_kwargs)
        elapsed_seconds = time.time() - start_time

        try:
            response_data = raw_response.json()
        except ValueError:
            response_data = raw_response.text

        return ApiResponse(
            api_name=api_config.name,
            url=api_config.url,
            status_code=raw_response.status_code,
            success=200 <= raw_response.status_code < 300,
            response_data=response_data,
            elapsed_seconds=elapsed_seconds,
            headers=dict(raw_response.headers),
        )

    def close(self):
        """关闭请求会话"""
        self.session.close()
