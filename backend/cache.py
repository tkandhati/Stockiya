"""In-memory TTL cache used by the middleware detail endpoint."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, maxsize: int = 128, ttl_seconds: int = 900):
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if item is None:
            return None
        ts, value = item
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)
        self._store.move_to_end(key)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)


detail_cache = TTLCache(maxsize=128, ttl_seconds=900)
