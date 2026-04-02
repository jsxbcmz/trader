from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


class BaseJsonRepository:
    """JSON 文件持久化基类。

    提供统一的 JSON 文件读写能力：
    - 文件不存在时返回 None（由子类决定默认值）
    - 写入时自动创建父目录
    - 使用临时文件原子写入，防止写入中断导致数据损坏
    """

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)

    def _read_json(self) -> Any | None:
        """读取 JSON 文件内容。文件不存在时返回 None。"""
        if not self.file_path.exists():
            return None
        with self.file_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)

    def _write_json(self, data: Any) -> None:
        """原子写入 JSON 文件（先写临时文件再 rename）。"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=self.file_path.parent,
            encoding="utf-8",
            suffix=".tmp",
        ) as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
            temp_path = Path(fp.name)
        temp_path.replace(self.file_path)
