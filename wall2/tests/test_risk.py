from datetime import date
from decimal import Decimal

import pytest
from conftest import ct, quote, signal
from hypothesis import given
from hypothesis import strategies as st

from wall2.config import RiskConfig, SessionConfig
from wall2.core.types import Direction
from wall2.risk.gate import DayState, Reject, RiskGate
from wall2.risk.settlement import SettlementLedger
from wall2.selection.selector import Selection
from wall2.session.calendar import TradingCalendar

PLAN = TradingCalendar(SessionConfig()).plan(date(2026, 10, 2))
gate = RiskGate(RiskConfig())


def check(day: DayState, sig=None, now=None):
    assert PLAN is not None
    return gate.check_signal(sig or signal(), day, PLAN, now or ct(10, 0))


def test_happy_path() -> None:
    assert check(DayState()) is None


@pytest.mark.parametrize(
    ("setup", "now", "expected"),
    [
        (lambda d: setattr(d, "paused", True), None, Reject.PAUSED),
        (lambda d: d.disabled_etfs.add("SPY"), None, Reject.ETF_DISABLED),
        (lambda d: None, ct(8, 34), Reject.BEFORE_FIRST_ENTRY),
        (lambda d: None, ct(14, 50), Reject.AFTER_CLOSEOUT),
        (lambda d: d.halted_etfs.add("SPY"), None, Reject.HALTED),
        (lambda d: setattr(d, "trades_today", 3), None, Reject.TRADE_CAP),
        (lambda d: d.open_etfs.add("SPY"), None, Reject.POSITION_OPEN),
    ],
)
def test_rejections(setup, now, expected) -> None:
    d = DayState()
    setup(d)
    assert check(d, now=now) is expected


def test_direction_lock_until_15min_close_flips() -> None:
    d = DayState()
    d.on_entry("SPY", Direction.UP)
    d.on_exit("SPY")
    assert check(d, signal(d=Direction.DOWN)) is Reject.DIRECTION_LOCK
    assert check(d, signal(d=Direction.UP)) is None
    d.on_trend_bar("SPY", Direction.UP)  # same side: lock stays
    assert check(d, signal(d=Direction.DOWN)) is Reject.DIRECTION_LOCK
    d.on_trend_bar("SPY", Direction.DOWN)  # 15-min close on the other side: lock lifts
    assert check(d, signal(d=Direction.DOWN)) is None


def test_other_etfs_unaffected_by_one_position() -> None:
    d = DayState()
    d.on_entry("SPY", Direction.UP)
    assert check(d, signal("QQQ")) is None


def test_settled_cash_check_includes_fees() -> None:
    s = Selection(signal(), quote("601", "0.18", "0.20", 0.3), 0.3, "broker", 1)
    assert gate.check_selection(s, SettlementLedger(Decimal("20.00")), Decimal("0")) is None
    assert (
        gate.check_selection(s, SettlementLedger(Decimal("20.00")), Decimal("0.05"))
        is Reject.NO_SETTLED_CASH
    )


def test_ledger_sale_proceeds_unusable_until_settled() -> None:
    led = SettlementLedger(Decimal("40"))
    led.record_buy(Decimal("20"))
    led.record_sale(Decimal("35"), date(2026, 10, 5))
    assert led.available() == Decimal("20") and led.unsettled == Decimal("35")
    with pytest.raises(ValueError):
        led.record_buy(Decimal("21"))  # would spend unsettled money → good-faith violation
    led.roll_to(date(2026, 10, 2))
    assert led.available() == Decimal("20")
    led.roll_to(date(2026, 10, 5))
    assert led.available() == Decimal("55") and led.unsettled == 0


@given(st.lists(st.tuples(st.booleans(), st.integers(1, 5000)), max_size=60))
def test_ledger_never_spends_more_than_settled(ops) -> None:
    led = SettlementLedger(Decimal("100.00"))
    for is_buy, cents in ops:
        amt = Decimal(cents) / 100
        if is_buy:
            if led.can_afford(amt):
                led.record_buy(amt)
            else:
                with pytest.raises(ValueError):
                    led.record_buy(amt)
        else:
            led.record_sale(amt, date(2026, 10, 5))
        assert led.settled >= 0
