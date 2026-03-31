from __future__ import annotations

import json

import pytest

from core.templates.builtin import DEFAULT_TEMPLATES
from core.templates.service import TemplateService


@pytest.fixture
def template_service(temp_root):
    return TemplateService.from_root(temp_root)


def test_list_templates_initializes_defaults_when_file_missing(template_service, temp_root):
    templates = template_service.list_templates()
    assert len(templates) == 4
    assert (temp_root / "templates.json").exists()
    assert sorted(item.id for item in templates) == sorted(item.id for item in DEFAULT_TEMPLATES)


def test_create_template_persists_and_round_trips(template_service, temp_root, ma_condition):
    created = template_service.create_template(
        name="自定义均线模板",
        description="测试模板",
        condition=ma_condition,
        default_time_mode="exact",
    )

    assert created.id.startswith("custom-")
    assert (temp_root / "templates.json").exists()

    reloaded_service = TemplateService.from_root(temp_root)
    templates = reloaded_service.list_templates()
    matched = [item for item in templates if item.id == created.id]
    assert len(matched) == 1
    assert matched[0].name == "自定义均线模板"
    assert matched[0].description == "测试模板"


def test_delete_template_removes_any_template(template_service):
    template = template_service.list_templates()[0]

    template_service.delete_template(template.id)
    templates = template_service.list_templates()
    assert all(item.id != template.id for item in templates)


def test_duplicate_name_is_rejected(template_service, ma_condition):
    template_service.create_template(
        name="重复模板",
        description="",
        condition=ma_condition,
    )

    with pytest.raises(ValueError, match="模板名称已存在"):
        template_service.create_template(
            name="重复模板",
            description="again",
            condition=ma_condition,
        )


def test_invalid_condition_is_rejected(template_service):
    with pytest.raises(ValueError):
        template_service.create_template(
            name="非法模板",
            description="",
            condition={"kind": "field", "field": "UNKNOWN"},
        )


def test_build_screening_request_uses_template_defaults(template_service, ma_condition):
    created = template_service.create_template(
        name="请求模板",
        description="",
        condition=ma_condition,
        default_time_mode="on_or_before",
    )

    request = template_service.build_screening_request(created.id, "2026-03-27")
    assert request.condition == ma_condition
    assert request.target_date == "2026-03-27"
    assert request.time_mode == "on_or_before"


def test_duplicate_template_creates_editable_copy(template_service):
    source = template_service.list_templates()[0]

    duplicated = template_service.duplicate_template(source.id, new_name="模板副本")

    assert duplicated.id.startswith("custom-")
    assert duplicated.name == "模板副本"
    assert duplicated.condition == source.condition


def test_default_template_can_be_updated(template_service):
    template = template_service.list_templates()[0]

    updated = template_service.update_template(
        template.id,
        name="更新后的默认模板",
        description=template.description,
        condition=template.condition,
        default_time_mode=template.default_time_mode,
        stock_pool_name=template.stock_pool_name,
        include_debug=template.include_debug,
    )

    assert updated.name == "更新后的默认模板"
    reloaded = template_service.get_template(template.id)
    assert reloaded.name == "更新后的默认模板"


def test_deleted_defaults_do_not_reappear(template_service):
    for template in list(template_service.list_templates()):
        template_service.delete_template(template.id)

    assert template_service.list_templates() == []

    reloaded_service = TemplateService.from_root(template_service.root)
    assert reloaded_service.list_templates() == []


def test_legacy_is_builtin_field_is_ignored(template_service, temp_root):
    payload = {
        "templates": [
            {
                "id": "legacy-template",
                "name": "旧模板",
                "description": "",
                "condition": {
                    "kind": "comparison",
                    "operator": ">",
                    "left": {"kind": "field", "field": "CLOSE"},
                    "right": {
                        "kind": "function",
                        "name": "MA",
                        "args": [
                            {"kind": "field", "field": "CLOSE"},
                            {"kind": "constant", "value": 5},
                        ],
                    },
                },
                "default_time_mode": "exact",
                "stock_pool_name": "default",
                "include_debug": False,
                "is_builtin": True,
            }
        ]
    }
    (temp_root / "templates.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    templates = template_service.list_templates()

    assert len(templates) == 1
    assert templates[0].id == "legacy-template"
    assert templates[0].name == "旧模板"


def test_repository_invalid_json_raises_value_error(template_service, temp_root):
    (temp_root / "templates.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        template_service.list_templates()
