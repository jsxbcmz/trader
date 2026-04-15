from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaginationConfig:
    """分页配置"""
    enabled: bool = False
    page_param: str = "page"
    page_size: int = 50
    dynamic_params: dict = field(default_factory=dict)
    data_path: str = ""
    metadata_fields: dict = field(default_factory=dict)
    keep_fields: list = field(default_factory=list)


@dataclass
class BatchConfig:
    """批量遍历参数配置：用同一接口遍历一组参数值"""
    enabled: bool = False
    param_name: str = ""
    source_file: str = ""
    source_field: str = ""
    data_path: str = ""
    merge_mode: str = "flatten"


@dataclass
class ApiConfig:
    """单个接口的配置"""
    name: str
    url: str
    method: str
    headers: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    body: Optional[dict] = None
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)
    output_file: str = ""


@dataclass
class Settings:
    """全局设置"""
    timeout: int = 30
    retry_count: int = 3
    retry_delay: int = 2
    delay_between_requests: float = 1


class ConfigLoader:
    """配置加载器，负责读取和解析接口配置文件"""

    def __init__(self, config_path: str | None = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "api_config.json")
        self.config_path = config_path
        self._raw_config: dict = {}

    def load(self) -> tuple[list[ApiConfig], Settings]:
        """加载配置文件，返回接口列表和全局设置"""
        with open(self.config_path, "r", encoding="utf-8") as file:
            self._raw_config = json.load(file)

        api_configs = self._parse_apis()
        settings = self._parse_settings()
        return api_configs, settings

    def _parse_apis(self) -> list[ApiConfig]:
        """解析接口配置列表"""
        apis_data = self._raw_config.get("apis", [])
        api_configs = []
        for item in apis_data:
            pagination = self._parse_pagination(item.get("pagination"))
            batch = self._parse_batch(item.get("batch"))
            api_config = ApiConfig(
                name=item["name"],
                url=item["url"],
                method=item.get("method", "GET").upper(),
                headers=item.get("headers", {}),
                params=item.get("params", {}),
                body=item.get("body"),
                pagination=pagination,
                batch=batch,
                output_file=item.get("output_file", ""),
            )
            api_configs.append(api_config)
        return api_configs

    def _parse_pagination(self, pagination_data: Optional[dict]) -> PaginationConfig:
        """解析分页配置"""
        if not pagination_data or not pagination_data.get("enabled"):
            return PaginationConfig()
        return PaginationConfig(
            enabled=True,
            page_param=pagination_data.get("page_param", "page"),
            page_size=pagination_data.get("page_size", 50),
            dynamic_params=pagination_data.get("dynamic_params", {}),
            data_path=pagination_data.get("data_path", ""),
            metadata_fields=pagination_data.get("metadata_fields", {}),
            keep_fields=pagination_data.get("keep_fields", []),
        )

    def _parse_batch(self, batch_data: Optional[dict]) -> BatchConfig:
        """解析批量遍历配置"""
        if not batch_data or not batch_data.get("enabled"):
            return BatchConfig()
        return BatchConfig(
            enabled=True,
            param_name=batch_data.get("param_name", ""),
            source_file=batch_data.get("source_file", ""),
            source_field=batch_data.get("source_field", ""),
            data_path=batch_data.get("data_path", ""),
            merge_mode=batch_data.get("merge_mode", "flatten"),
        )

    def _parse_settings(self) -> Settings:
        """解析全局设置"""
        settings_data = self._raw_config.get("settings", {})
        return Settings(
            timeout=settings_data.get("timeout", 30),
            retry_count=settings_data.get("retry_count", 3),
            retry_delay=settings_data.get("retry_delay", 2),
            delay_between_requests=settings_data.get("delay_between_requests", 1),
        )
