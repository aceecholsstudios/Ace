"""Contract specifications for the traded micros and the E-minis read for signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Root(StrEnum):
    """CME product roots Ace knows about."""

    MES = "MES"
    MNQ = "MNQ"
    M2K = "M2K"
    ES = "ES"
    NQ = "NQ"
    RTY = "RTY"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    root: Root
    tick_size: Decimal
    tick_value_usd: Decimal
    tradable: bool
    """True for the micros Ace places orders on; False for signal-only E-minis."""

    @property
    def point_value_usd(self) -> Decimal:
        return self.tick_value_usd / self.tick_size


INSTRUMENTS: dict[Root, InstrumentSpec] = {
    spec.root: spec
    for spec in (
        InstrumentSpec(Root.MES, Decimal("0.25"), Decimal("1.25"), tradable=True),
        InstrumentSpec(Root.MNQ, Decimal("0.25"), Decimal("0.50"), tradable=True),
        InstrumentSpec(Root.M2K, Decimal("0.10"), Decimal("0.50"), tradable=True),
        InstrumentSpec(Root.ES, Decimal("0.25"), Decimal("12.50"), tradable=False),
        InstrumentSpec(Root.NQ, Decimal("0.25"), Decimal("5.00"), tradable=False),
        InstrumentSpec(Root.RTY, Decimal("0.10"), Decimal("5.00"), tradable=False),
    )
}

TRADABLE: frozenset[Root] = frozenset(r for r, s in INSTRUMENTS.items() if s.tradable)
SIGNAL_ONLY: frozenset[Root] = frozenset(r for r, s in INSTRUMENTS.items() if not s.tradable)
