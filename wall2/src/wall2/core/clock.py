"""Time source. Everything the bot reasons about is in US Central time."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC).astimezone(CT)


class SimClock:
    """Manually advanced clock for tests, replays and the simulator."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("SimClock needs a tz-aware datetime")
        self._now = start.astimezone(CT)

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        t = t.astimezone(CT)
        if t < self._now:
            raise ValueError("SimClock cannot move backwards")
        self._now = t


def to_ct(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("naive datetime; all times must be tz-aware")
    return t.astimezone(CT)
