from __future__ import annotations

from dataclasses import replace

from ..api.ports import ResolutionCache, TermResolution


class MemoryResolutionCache(ResolutionCache):
    """Small process-local cache seam; SQLite persistence can implement the same port."""

    def __init__(self):
        self._items: dict[str, TermResolution] = {}

    def get(self, key: str) -> TermResolution | None:
        value = self._items.get(key)
        return replace(value, cache_hit=True) if value else None

    def set(self, key: str, value: TermResolution) -> None:
        self._items[key] = replace(value, cache_hit=False, cache_key=key)
