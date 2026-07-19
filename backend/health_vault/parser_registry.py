"""Parser registry — parsers register; import engine resolves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class HealthParser(Protocol):
    id: str
    name: str
    version: str
    priority: int
    supported_types: list[str]

    def can_parse(self, ctx: dict[str, Any]) -> bool: ...

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class ParserRegistry:
    _parsers: dict[str, Any] = field(default_factory=dict)

    def register(self, parser: Any) -> str:
        pid = getattr(parser, "id", None)
        if not pid:
            raise ValueError("Parser must have id")
        self._parsers[pid] = parser
        return pid

    def list(self) -> list[Any]:
        return list(self._parsers.values())

    def get(self, parser_id: str) -> Any | None:
        return self._parsers.get(parser_id)

    def resolve(self, ctx: dict[str, Any]) -> Any | None:
        candidates = []
        for p in self._parsers.values():
            try:
                if p.can_parse(ctx):
                    candidates.append(p)
            except Exception:
                continue
        candidates.sort(key=lambda x: getattr(x, "priority", 0), reverse=True)
        return candidates[0] if candidates else None

    def parse(self, ctx: dict[str, Any]) -> dict[str, Any]:
        parser = self.resolve(ctx)
        if parser is None:
            return {
                "parser": None,
                "measurements": [],
                "confidence": 0.0,
                "notes": ["No registered parser matched this document"],
            }
        result = parser.parse(ctx) or {}
        return {
            "parser": {
                "id": parser.id,
                "name": parser.name,
                "version": parser.version,
            },
            "measurements": result.get("measurements") or [],
            "confidence": float(result.get("confidence") or 0.0),
            "notes": result.get("notes") or [],
        }


# Global registry used by ImportService; parsers auto-register on import.
DEFAULT_REGISTRY = ParserRegistry()


def get_default_registry() -> ParserRegistry:
    return DEFAULT_REGISTRY
