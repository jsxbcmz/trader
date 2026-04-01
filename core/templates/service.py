from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.models.screening import ScreeningRequest
from core.models.template import ScreeningTemplate

from .builtin import DEFAULT_TEMPLATES
from .repository import TemplateRepository


class TemplateService:
    def __init__(self, root: Path, repository: TemplateRepository | None = None):
        self.root = Path(root)
        self.repository = repository or TemplateRepository(self.root)

    @classmethod
    def from_root(cls, root: Path) -> "TemplateService":
        return cls(root)

    def list_templates(self) -> list[ScreeningTemplate]:
        templates = self._load_templates()
        return sorted(templates, key=lambda item: item.name)

    def get_template(self, template_id: str) -> ScreeningTemplate:
        target = str(template_id or "").strip()
        for template in self.list_templates():
            if template.id == target:
                return template
        raise ValueError("模板不存在")

    def create_template(
        self,
        *,
        name: str,
        description: str,
        tdx_source: str,
        stock_pool_name: str = "default",
        include_debug: bool = False,
    ) -> ScreeningTemplate:
        now = self._now_iso()
        template = ScreeningTemplate(
            id=f"custom-{uuid4().hex}",
            name=str(name or "").strip(),
            description=str(description or "").strip(),
            tdx_source=str(tdx_source or "").strip(),
            stock_pool_name=str(stock_pool_name or "default").strip() or "default",
            include_debug=bool(include_debug),
            created_at=now,
            updated_at=now,
        )
        validated = self._validate_template(template)
        templates = self._load_templates()
        self._ensure_unique_name(validated.name, templates)
        templates.append(validated)
        self.repository.save_templates(templates)
        return validated

    def update_template(
        self,
        template_id: str,
        *,
        name: str,
        description: str,
        tdx_source: str,
        stock_pool_name: str = "default",
        include_debug: bool = False,
    ) -> ScreeningTemplate:
        templates = self._load_templates()
        target = str(template_id or "").strip()
        for index, template in enumerate(templates):
            if template.id != target:
                continue
            updated = replace(
                template,
                name=str(name or "").strip(),
                description=str(description or "").strip(),
                tdx_source=str(tdx_source or "").strip(),
                stock_pool_name=str(stock_pool_name or "default").strip() or "default",
                include_debug=bool(include_debug),
                updated_at=self._now_iso(),
            )
            validated = self._validate_template(updated)
            self._ensure_unique_name(validated.name, templates, exclude_id=validated.id)
            templates[index] = validated
            self.repository.save_templates(templates)
            return validated
        raise ValueError("模板不存在")

    def delete_template(self, template_id: str) -> None:
        templates = self._load_templates()
        target = str(template_id or "").strip()
        new_templates = [template for template in templates if template.id != target]
        if len(new_templates) == len(templates):
            raise ValueError("模板不存在")
        self.repository.save_templates(new_templates)

    def duplicate_template(self, template_id: str, new_name: str | None = None) -> ScreeningTemplate:
        source = self.get_template(template_id)
        duplicate_name = str(new_name or "").strip() or f"{source.name} - 副本"
        return self.create_template(
            name=duplicate_name,
            description=source.description,
            tdx_source=source.tdx_source,
            stock_pool_name=source.stock_pool_name,
            include_debug=source.include_debug,
        )

    def build_screening_request(
        self,
        template_id: str,
        target_date: str,
    ) -> ScreeningRequest:
        template = self.get_template(template_id)
        return ScreeningRequest(
            tdx_source=template.tdx_source,
            target_date=str(target_date or "").strip(),
            stock_pool_name=template.stock_pool_name,
            include_debug=template.include_debug,
        )

    def _load_templates(self) -> list[ScreeningTemplate]:
        templates = self.repository.load_templates()
        if not templates:
            if not self.repository.file_path.exists():
                templates = [self._validate_template(template) for template in DEFAULT_TEMPLATES]
                self.repository.save_templates(templates)
                return list(templates)
            return []

        validated: list[ScreeningTemplate] = []
        names_seen: set[str] = set()
        ids_seen: set[str] = set()
        for template in templates:
            normalized = self._validate_template(template)
            key_name = normalized.name.casefold()
            if key_name in names_seen:
                raise ValueError(f"模板名称重复: {normalized.name}")
            if normalized.id in ids_seen:
                raise ValueError(f"模板 ID 重复: {normalized.id}")
            names_seen.add(key_name)
            ids_seen.add(normalized.id)
            validated.append(normalized)
        return validated

    def _validate_template(self, template: ScreeningTemplate) -> ScreeningTemplate:
        if not template.id:
            raise ValueError("模板 ID 不能为空")
        if not template.name:
            raise ValueError("模板名称不能为空")
        if not template.tdx_source or not template.tdx_source.strip():
            raise ValueError("通达信条件代码不能为空")
        return template

    def _ensure_unique_name(
        self,
        name: str,
        templates: list[ScreeningTemplate],
        exclude_id: str | None = None,
    ) -> None:
        key = str(name or "").strip().casefold()
        for template in templates:
            if exclude_id and template.id == exclude_id:
                continue
            if template.name.casefold() == key:
                raise ValueError("模板名称已存在")

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
