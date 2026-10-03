"""Shared value types. Prices of options and all money are Decimal; indicator math is float."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

CENT = Decimal("0.01")
CONTRACT_MULTIPLIER = 100


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


class Right(StrEnum):
    CALL = "call"
    PUT = "put"

    @staticmethod
    def for_direction(direction: Direction) -> Right:
        return Right.CALL if direction is Direction.UP else Right.PUT


@dataclass(frozen=True, slots=True)
class Bar:
    """An OHLCV bar. `start` is the tz-aware opening time; it covers [start, start + minutes)."""

    symbol: str
    start: datetime
    minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class OptionContract:
    underlying: str
    expiry: date
    strike: Decimal
    right: Right
    symbol: str  # broker/OCC symbol

    def is_itm(self, underlying_price: float) -> bool:
        k = float(self.strike)
        return underlying_price > k if self.right is Right.CALL else underlying_price < k


@dataclass(frozen=True, slots=True)
class OptionQuote:
    contract: OptionContract
    bid: Decimal
    ask: Decimal
    delta: float | None  # broker-provided; puts are negative
    ts: datetime

    @property
    def mid(self) -> Decimal:
        return ((self.bid + self.ask) / 2).quantize(CENT)


@dataclass(frozen=True, slots=True)
class Signal:
    """A triggered trend-pullback setup on one underlying."""

    underlying: str
    direction: Direction
    ts: datetime
    trigger_level: float
    underlying_price: float
    reason: str
