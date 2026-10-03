from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from wall2.config import Wall2Config
from wall2.core.clock import CT
from wall2.core.types import Bar, Direction, OptionContract, OptionQuote, Right, Signal

DAY = date(2026, 10, 2)  # a Friday, full session


def ct(h: int, m: int, d: date = DAY) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=CT)


def bar(
    sym: str,
    h: int,
    m: int,
    o: float,
    hi: float,
    lo: float,
    c: float,
    v: float = 1000,
    minutes: int = 1,
) -> Bar:
    return Bar(sym, ct(h, m), minutes, o, hi, lo, c, v)


def contract(
    strike: str, right: Right = Right.CALL, u: str = "SPY", d: date = DAY
) -> OptionContract:
    return OptionContract(u, d, Decimal(strike), right, f"{u}-{right}-{strike}")


def quote(
    strike: str, bid: str, ask: str, delta: float | None, right: Right = Right.CALL, u: str = "SPY"
) -> OptionQuote:
    return OptionQuote(contract(strike, right, u), Decimal(bid), Decimal(ask), delta, ct(10, 0))


def signal(
    u: str = "SPY", d: Direction = Direction.UP, h: int = 10, m: int = 0, price: float = 600.0
) -> Signal:
    return Signal(u, d, ct(h, m), price - 0.1, price, "test")


@pytest.fixture
def cfg() -> Wall2Config:
    return Wall2Config()
