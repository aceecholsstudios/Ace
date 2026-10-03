from datetime import date

from conftest import ct

from wall2.config import SessionConfig
from wall2.session.calendar import TradingCalendar

cal = TradingCalendar(SessionConfig())


def test_full_day_plan_in_central_time() -> None:
    p = cal.plan(date(2026, 10, 2))
    assert p is not None
    assert (p.open, p.close) == (ct(8, 30), ct(15, 0))
    assert p.first_entry == ct(8, 35)
    assert p.closeout == ct(14, 50)
    assert p.report_at == ct(15, 15)
    assert not p.half_day


def test_half_day_closes_out_at_1150() -> None:
    d = date(2026, 11, 27)  # day after Thanksgiving
    p = cal.plan(d)
    assert p is not None and p.half_day
    assert p.close == ct(12, 0, d)
    assert p.closeout == ct(11, 50, d)


def test_weekend_and_holiday_have_no_plan() -> None:
    assert cal.plan(date(2026, 10, 3)) is None  # Saturday
    assert cal.plan(date(2026, 11, 26)) is None  # Thanksgiving


def test_settlement_is_next_session() -> None:
    assert cal.settlement_date(date(2026, 10, 2)) == date(2026, 10, 5)  # Fri → Mon
    assert cal.settlement_date(date(2026, 11, 25)) == date(2026, 11, 27)  # skips Thanksgiving
    assert cal.settlement_date(date(2026, 10, 6)) == date(2026, 10, 7)
