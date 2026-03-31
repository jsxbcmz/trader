from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.models.template import ScreeningTemplate


class TemplateRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.file_path = self.root / "templates.json"

    def load_templates(self) -> list[ScreeningTemplate]:
        if not self.file_path.exists():
            return []

        with self.file_path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

        if not isinstance(payload, dict):
            raise ValueError("templates.json 顶层结构必须是对象")

        items = payload.get("templates", [])
        if not isinstance(items, list):
            raise ValueError("templates 字段必须是数组")

        templates: list[ScreeningTemplate] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("模板项必须是对象")
            template = ScreeningTemplate.from_dict(item)
            templates.append(template)
        return templates

    def save_templates(self, templates: list[ScreeningTemplate]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"templates": [template.to_dict() for template in templates]}
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=self.file_path.parent,
            encoding="utf-8",
            suffix=".tmp",
        ) as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            temp_path = Path(fp.name)
        temp_path.replace(self.file_path)
