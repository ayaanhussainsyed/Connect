"""A3 – suggestions behind one interface.

    from cs.suggest import get_suggestions
    sugs = get_suggestions(season=2026, engine="baseline")      # or "ml"; default: settings.engine

One ``Suggestion`` per target row (``evaluation/targets_<season>.csv``), *before* guardrails
(run ``cs.guardrails.apply_guardrails`` or use ``cs.export.build``, which does both).
"""
from __future__ import annotations

from datetime import date

from cs.config import settings
from cs.domain import Suggestion

ENGINES = ("baseline", "ml")


def get_suggestions(season: int = 2026, engine: str | None = None,
                    entity_ids: list[str] | set[str] | None = None,
                    as_of: date | str | None = None) -> list[Suggestion]:
    engine = (engine or settings.engine or "baseline").lower()
    if engine == "baseline":
        from cs.suggest import baseline
        return baseline.suggestions(season, entity_ids, as_of)
    if engine == "ml":
        from cs.suggest import engine_adapter
        return engine_adapter.suggestions(season, entity_ids, as_of)
    raise ValueError(f"unknown engine {engine!r}; use one of {ENGINES}")
