from __future__ import annotations

from src.rate_limit import SlidingWindowLimiter


def test_allows_up_to_max_then_denies():
    limiter = SlidingWindowLimiter(max_events=3, window_seconds=60)
    assert limiter.allow("a")
    assert limiter.allow("a")
    assert limiter.allow("a")
    assert limiter.allow("a") is False


def test_independent_keys():
    limiter = SlidingWindowLimiter(max_events=2, window_seconds=60)
    assert limiter.allow("a")
    assert limiter.allow("a")
    assert limiter.allow("a") is False
    assert limiter.allow("b")
    assert limiter.allow("b")


def test_global_cap():
    limiter = SlidingWindowLimiter(max_events=10, window_seconds=60, global_max=2)
    assert limiter.allow("a")
    assert limiter.allow("b")
    assert limiter.allow("c") is False


def test_window_evicts_old_events(monkeypatch):
    base = 1000.0
    times = [base]

    def fake_monotonic():
        return times[0]

    import src.rate_limit as rl

    monkeypatch.setattr(rl.time, "monotonic", lambda: times[0])

    limiter = SlidingWindowLimiter(max_events=1, window_seconds=10)
    assert limiter.allow("a")
    assert limiter.allow("a") is False
    times[0] = base + 11
    assert limiter.allow("a")
