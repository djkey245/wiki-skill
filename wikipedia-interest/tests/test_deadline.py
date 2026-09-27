import pytest

from wikitrends.deadline import Deadline, DeadlineReached


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_remaining_and_expiry():
    clock = FakeClock()
    deadline = Deadline(90, clock=clock)
    assert deadline.remaining() == 90
    assert not deadline.expired()
    clock.now += 89.5
    assert deadline.remaining() == pytest.approx(0.5)
    deadline.check()
    clock.now += 0.5
    assert deadline.expired()
    assert deadline.remaining() == 0
    with pytest.raises(DeadlineReached):
        deadline.check()


def test_unlimited_never_expires():
    deadline = Deadline.unlimited()
    assert not deadline.expired()
    deadline.check()
