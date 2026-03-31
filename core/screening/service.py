from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.models.screening import ScreeningRequest, ScreeningResult
from core.screening.engine import ScreeningEngine
from core.screening.result_formatter import format_error_lines, format_match_lines, format_screening_summary


@dataclass(slots=True)
class ScreeningService:
    engine: ScreeningEngine

    @classmethod
    def from_root(cls, root: Path) -> "ScreeningService":
        return cls(engine=ScreeningEngine.from_root(root))

    def screen(self, request: ScreeningRequest) -> ScreeningResult:
        return self.engine.run(request)

    def screen_with_summary(self, request: ScreeningRequest) -> dict:
        result = self.screen(request)
        return {
            "result": result,
            "summary": format_screening_summary(result),
            "matches": format_match_lines(result),
            "errors": format_error_lines(result),
        }
