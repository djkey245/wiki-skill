"""Per-command time limit: after it expires no new network requests start (data §4)."""
from __future__ import annotations

import time
from typing import Callable


class DeadlineReached(Exception):
    """Raised instead of starting new work once the command's time limit is spent."""


class Deadline:
    def __init__(self, max_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_seconds = max_seconds
        self._clock = clock
        self._end = clock() + max_seconds

    @classmethod
    def unlimited(cls) -> "Deadline":
        return cls(float("inf"))

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    def expired(self) -> bool:
        return self._clock() >= self._end

    def check(self) -> None:
        if self.expired():
            raise DeadlineReached()
