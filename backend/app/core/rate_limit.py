# AI contribution: 50% or more AI-generated
"""Count failed attempts in a sliding window, to slow down password guessing."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class AttemptLimiter:
    """Failures per key (an IP address, an email) within the last `window` seconds.

    Kept in memory, so each API process counts on its own and a restart forgets.
    That is enough for one process; several would need a shared store.
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures[key]
        cutoff = self.clock() - self.window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def retry_after(self, *keys: str) -> int:
        """Seconds until any of the keys may try again; 0 when none is blocked."""
        with self._lock:
            waits = [
                failures[0] + self.window - self.clock()
                for failures in map(self._recent, keys)
                if len(failures) >= self.max_failures
            ]
        return max(1, round(max(waits))) if waits else 0

    def failed(self, *keys: str) -> None:
        with self._lock:
            now = self.clock()
            for key in keys:
                self._recent(key).append(now)

    def succeeded(self, *keys: str) -> None:
        """Forget the keys' failures; pass only the email, so one good account cannot
        clear an address that is guessing others."""
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
