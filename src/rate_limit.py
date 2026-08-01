from __future__ import annotations

import time
from collections import deque
from collections.abc import Hashable


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        global_max: int | None = None,
    ) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.global_max = global_max
        self._buckets: dict[Hashable, deque[float]] = {}
        self._global: deque[float] = deque()

    def _evict(self, now: float, cutoff: float, key: Hashable) -> deque[float]:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = deque()
            self._buckets[key] = bucket
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket and key in self._buckets:
            del self._buckets[key]
            bucket = deque()
            self._buckets[key] = bucket
        return bucket

    def allow(self, key: Hashable) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        if self.global_max is not None:
            while self._global and self._global[0] < cutoff:
                self._global.popleft()
            if len(self._global) >= self.global_max:
                return False

        bucket = self._evict(now, cutoff, key)
        if len(bucket) >= self.max_events:
            return False

        bucket.append(now)
        if self.global_max is not None:
            self._global.append(now)
        return True
