from __future__ import annotations

import json
import os
import logging
from datetime import date, datetime
from typing import Any

from .requester import ApiResponse

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")


class DataStorage:
    """数据存储模块，负责将接口响应数据持久化到本地文件"""

    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    def is_cache_valid(self, output_file: str) -> bool:
        """检查缓存文件是否存在、是当天生成的、且内容非空"""
        if not output_file:
            return False
        filepath = os.path.join(self.output_dir, output_file)
        if not os.path.exists(filepath):
            return False
        modified_time = datetime.fromtimestamp(os.path.getmtime(filepath))
        if modified_time.date() != date.today():
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
            if isinstance(data, list) and len(data) == 0:
                logger.info(f"缓存文件内容为空数组，视为无效: {output_file}")
                return False
            if isinstance(data, dict) and len(data) == 0:
                logger.info(f"缓存文件内容为空对象，视为无效: {output_file}")
                return False
        except (json.JSONDecodeError, IOError):
            logger.warning(f"缓存文件读取失败，视为无效: {output_file}")
            return False
        return True

    def save_responses(self, responses: list[ApiResponse]) -> list[str]:
        """将每个接口的响应数据保存到各自的固定 JSON 文件中"""
        saved_paths: list[str] = []

        for response in responses:
            if not response.success:
                logger.warning(f"跳过失败接口: {response.api_name}")
                continue

            filename = response.output_file
            if not filename:
                filename = f"{response.api_name}.json"

            filepath = os.path.join(self.output_dir, filename)
            core_data = self._extract_core_data(response)

            if filename == "day_positions.json" and isinstance(core_data, list):
                core_data = [
                    item for item in core_data
                    if isinstance(item, dict) and (
                        str(item.get("code", "")).startswith("00")
                        or str(item.get("code", "")).startswith("60")
                    )
                ]

            with open(filepath, "w", encoding="utf-8") as output_file:
                json.dump(core_data, output_file, ensure_ascii=False, indent=2)

            logger.info(f"[{response.api_name}] 数据已保存至: {filepath}")
            saved_paths.append(filepath)

        return saved_paths

    def load_positions(self) -> list[dict]:
        """加载每日持仓数据"""
        filepath = os.path.join(self.output_dir, "day_positions.json")
        try:
            with open(filepath, "r", encoding="utf-8") as positions_file:
                return json.load(positions_file)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def _extract_core_data(response: ApiResponse) -> Any:
        """提取核心数据"""
        response_data = response.response_data
        if isinstance(response_data, dict) and "rank_list" in response_data:
            return response_data["rank_list"]
        return response_data
