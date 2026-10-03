"""NYSE trading calendar in Central time: sessions, half-days, close-out times, T+1 settlement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

from wall2.config import SessionConfig
from wall2.core.clock import CT

REGULAR_OPEN = time(8, 30)
REGULAR_CLOSE = time(15, 0)


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """The schedule for one trading day, all tz-aware in CT."""

    day: date
    open: datetime
    close: datetime
    first_entry: datetime
    closeout: datetime
    report_at: datetime
    half_day: bool


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS", start="2020-01-01")


class TradingCalendar:
    def __init__(self, cfg: SessionConfig) -> None:
        self._cfg = cfg
        self._cal = _xnys()

    def is_session(self, day: date) -> bool:
        return bool(self._cal.is_session(pd.Timestamp(day)))

    def next_session(self, day: date) -> date:
        """First session strictly after `day`."""
        ts = self._cal.date_to_session(pd.Timestamp(day), direction="next")
        if ts.date() == day:
            ts = self._cal.next_session(ts)
        return ts.date()  # type: ignore[no-any-return]

    def settlement_date(self, trade_day: date) -> date:
        """Options settle T+1 (next session)."""
        return self.next_session(trade_day)

    def plan(self, day: date) -> SessionPlan | None:
        if not self.is_session(day):
            return None
        ts = pd.Timestamp(day)
        open_ = self._cal.session_open(ts).tz_convert(CT).to_pydatetime()
        close = self._cal.session_close(ts).tz_convert(CT).to_pydatetime()
        half_day = close.time() < REGULAR_CLOSE
        first_entry = datetime.combine(day, self._cfg.first_entry, CT)
        closeout = close - timedelta(minutes=self._cfg.closeout_minutes_before_close)
        report_at = max(
            datetime.combine(day, self._cfg.report_at, CT), close + timedelta(minutes=15)
        )
        return SessionPlan(
            day, open_, close, max(first_entry, open_), closeout, report_at, half_day
        )
