from __future__ import annotations

import pytest

from core.indicators.registry import get_function_spec



def test_indicator_registry_supports_alias_lookup():
    assert get_function_spec("MA").name == "MA"
    assert get_function_spec("ma").name == "MA"
    assert get_function_spec("kdj").name == "KDJ"



def test_indicator_registry_supports_arg_count():
    spec = get_function_spec("SMA")
    assert spec.supports_arg_count(2) is True
    assert spec.supports_arg_count(3) is True
    assert spec.supports_arg_count(4) is False



def test_indicator_registry_unknown_function():
    with pytest.raises(KeyError, match="未注册的函数"):
        get_function_spec("UNKNOWN")
