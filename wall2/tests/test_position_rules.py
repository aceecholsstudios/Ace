from decimal import Decimal

from conftest import contract, ct

from wall2.config import ManagementConfig
from wall2.core.types import Right
from wall2.execution.position import ExitReason, Position, PositionRules

rules = PositionRules(ManagementConfig())


def pos(right: Right = Right.CALL, entry: str = "0.20") -> Position:
    return Position(contract("601", right), 1, Decimal(entry), ct(10, 0))


def test_hold_while_trend_intact() -> None:
    assert rules.on_entry_bar_close(pos(), close=602, ema=601, bid=Decimal("0.30")) is None


def test_trail_exit_in_profit_tries_mid() -> None:
    d = rules.on_entry_bar_close(pos(), close=600.5, ema=601, bid=Decimal("0.30"))
    assert d is not None and d.reason is ExitReason.TRAIL and d.profit


def test_puts_break_on_close_above_ema() -> None:
    p = pos(Right.PUT)
    assert rules.on_entry_bar_close(p, close=600, ema=601, bid=Decimal("0.3")) is None
    d = rules.on_entry_bar_close(p, close=601.5, ema=601, bid=Decimal("0.3"))
    assert d is not None and d.reason is ExitReason.TRAIL


def test_losing_trend_break_salvages_at_half_or_more() -> None:
    d = rules.on_entry_bar_close(pos(), close=600, ema=601, bid=Decimal("0.10"))
    assert d is not None and d.reason is ExitReason.SALVAGE and not d.profit


def test_losing_trend_break_below_half_holds_to_expiry() -> None:
    assert rules.on_entry_bar_close(pos(), close=600, ema=601, bid=Decimal("0.09")) is None


def test_one_minute_trail_only_after_100pct_gain() -> None:
    p = pos()
    assert (
        rules.on_minute_close(p, close=600, ema=601, bid=Decimal("0.35")) is None
    )  # +75%: not tightened
    rules.on_minute_close(p, close=602, ema=601, bid=Decimal("0.40"))  # reaches +100%
    d = rules.on_minute_close(p, close=600.9, ema=601, bid=Decimal("0.38"))
    assert d is not None and d.reason is ExitReason.TIGHT_TRAIL and d.profit


def test_closeout_sells_itm_only() -> None:
    assert rules.on_closeout(pos(), underlying_price=600.9, bid=Decimal("0.01")) is None  # OTM call
    d = rules.on_closeout(pos(), underlying_price=601.2, bid=Decimal("0.25"))
    assert d is not None and d.reason is ExitReason.CLOSEOUT_ITM and d.profit
    d = rules.on_closeout(pos(Right.PUT), underlying_price=600.0, bid=Decimal("0.10"))
    assert d is not None and not d.profit
