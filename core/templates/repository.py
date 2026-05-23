from __future__ import annotations

from pathlib import Path

from core.data.base_json_repository import BaseJsonRepository
from core.models.template import ScreeningTemplate


class TemplateRepository(BaseJsonRepository):
    def __init__(self, root: Path):
        self.root = Path(root)
        super().__init__(self.root / "templates.json")

    def load_templates(self) -> list[ScreeningTemplate]:
        payload = self._read_json()
        if payload is None:
            return []

        if not isinstance(payload, dict):
            raise ValueError("templates.json 顶层结构必须是对象")

        items = payload.get("templates", [])
        if not isinstance(items, list):
            raise ValueError("templates 字段必须是数组")

        templates: list[ScreeningTemplate] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("模板项必须是对象")
            templates.append(ScreeningTemplate.from_dict(item))
        return templates

    def save_templates(self, templates: list[ScreeningTemplate]) -> None:
        payload = {"templates": [template.to_dict() for template in templates]}
        self._write_json(payload)