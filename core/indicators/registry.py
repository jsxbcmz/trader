from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from . import builtin, tdx_compat

ReturnKind = Literal["series", "multi_series"]


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    name: str
    func: Callable
    min_args: int
    max_args: int | None
    return_kind: ReturnKind = "series"
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def supports_arg_count(self, count: int) -> bool:
        if count < self.min_args:
            return False
        if self.max_args is None:
            return True
        return count <= self.max_args


FUNCTION_SPECS: tuple[FunctionSpec, ...] = (
    FunctionSpec("MA", builtin.ma, 2, 2, aliases=("ma",)),
    FunctionSpec("EMA", builtin.ema, 2, 2, aliases=("ema",)),
    FunctionSpec("SMA", tdx_compat.tdx_sma, 2, 3, aliases=("sma", "TDX_SMA", "tdx_sma")),
    FunctionSpec("HHV", builtin.hhv, 2, 2, aliases=("hhv",)),
    FunctionSpec("LLV", builtin.llv, 2, 2, aliases=("llv",)),
    FunctionSpec("REF", builtin.ref, 1, 2, aliases=("ref",)),
    FunctionSpec("MAX", builtin.max_series, 2, 2, aliases=("max",)),
    FunctionSpec("MIN", builtin.min_series, 2, 2, aliases=("min",)),
    FunctionSpec("ABS", builtin.abs_series, 1, 1, aliases=("abs",)),
    FunctionSpec("KDJ", tdx_compat.kdj, 3, 3, return_kind="multi_series", aliases=("kdj",)),
    FunctionSpec("ZX_SHORT_TREND", tdx_compat.zx_short_trend, 1, 1, aliases=("zx_short_trend",)),
    FunctionSpec("ZX_LONG_SHORT", tdx_compat.zx_long_short, 1, 2, aliases=("zx_long_short",)),
)


FUNCTION_REGISTRY: dict[str, FunctionSpec] = {}
for spec in FUNCTION_SPECS:
    FUNCTION_REGISTRY[spec.name.upper()] = spec
    for alias in spec.aliases:
        FUNCTION_REGISTRY[alias.upper()] = spec


def get_function_spec(name: str) -> FunctionSpec:
    key = str(name or "").upper()
    if key not in FUNCTION_REGISTRY:
        raise KeyError(f"未注册的函数: {name}")
    return FUNCTION_REGISTRY[key]


def is_registered_function(name: str) -> bool:
    return str(name or "").upper() in FUNCTION_REGISTRY
