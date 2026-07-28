"""Observation and pairing rate limits for HC-304B companion host."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    allowed: bool
    error: str | None = None


class SlidingWindowLimiter:
    """
    Process-local sliding window with a hard cap on distinct keys (LRU eviction).

    Acceptable for a **single-worker pilot**. Multi-worker deployments must share
    a persistent limiter or terminate at one worker — documented in HC-304B.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        max_keys: int = 256,
    ) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> RateLimitResult:
        ts = time.time() if now is None else now
        with self._lock:
            if key in self._events:
                self._events.move_to_end(key)
            else:
                while len(self._events) >= self.max_keys:
                    self._events.popitem(last=False)
                self._events[key] = deque()
            q = self._events[key]
            cutoff = ts - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_events:
                return RateLimitResult(False, "rate_limited")
            q.append(ts)
            return RateLimitResult(True, None)


# Conservative defaults for personal pilot host (single worker).
OBSERVATION_LIMITER = SlidingWindowLimiter(max_events=30, window_seconds=60.0, max_keys=128)
PAIR_CONFIRM_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=60.0, max_keys=128)
PAIR_START_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=60.0, max_keys=64)
