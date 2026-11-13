# AI contribution: 50% or more AI-generated
from app.core.rate_limit import AttemptLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock) -> AttemptLimiter:
    return AttemptLimiter(max_failures=3, window=60, clock=clock)


def test_a_key_is_blocked_after_too_many_failures_in_the_window():
    # Arrange
    clock = Clock()
    attempts = limiter(clock)
    for _ in range(3):
        attempts.failed("ip:1")

    # Act / Assert
    assert attempts.retry_after("ip:1") == 60
    assert attempts.retry_after("ip:2") == 0


def test_old_failures_leave_the_window():
    # Arrange
    clock = Clock()
    attempts = limiter(clock)
    for _ in range(3):
        attempts.failed("ip:1")

    # Act
    clock.now += 61

    # Assert
    assert attempts.retry_after("ip:1") == 0


def test_any_blocked_key_blocks_the_attempt():
    # Arrange
    clock = Clock()
    attempts = limiter(clock)
    for _ in range(3):
        attempts.failed("email:a")

    # Act / Assert
    assert attempts.retry_after("ip:9", "email:a") > 0


def test_a_success_forgets_only_the_keys_it_names():
    # Arrange
    clock = Clock()
    attempts = limiter(clock)
    for _ in range(3):
        attempts.failed("ip:1", "email:a")

    # Act
    attempts.succeeded("email:a")

    # Assert
    assert attempts.retry_after("email:a") == 0
    assert attempts.retry_after("ip:1") > 0
