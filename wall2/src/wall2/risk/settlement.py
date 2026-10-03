"""Cash-account settlement ledger.

Options settle T+1. Proceeds from a sale today are *unsettled* until the next session. Buying
with unsettled money and then selling before it settles is a good-faith violation, so wall2
only ever spends settled cash. Buys reduce settled cash immediately; sales add unsettled
proceeds that become settled on their settlement date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(slots=True)
class _Pending:
    amount: Decimal
    settles_on: date


@dataclass(slots=True)
class SettlementLedger:
    settled: Decimal
    pending: list[_Pending] = field(default_factory=list)

    @property
    def unsettled(self) -> Decimal:
        return sum((p.amount for p in self.pending), Decimal("0"))

    def available(self) -> Decimal:
        """Cash that can be spent right now without risking a good-faith violation."""
        return self.settled

    def can_afford(self, cost: Decimal) -> bool:
        return cost <= self.settled

    def record_buy(self, cost: Decimal) -> None:
        if cost < 0:
            raise ValueError("cost must be >= 0")
        if cost > self.settled:
            raise ValueError(f"buy of {cost} exceeds settled cash {self.settled}")
        self.settled -= cost

    def record_sale(self, proceeds: Decimal, settles_on: date) -> None:
        if proceeds < 0:
            raise ValueError("proceeds must be >= 0")
        self.pending.append(_Pending(proceeds, settles_on))

    def roll_to(self, today: date) -> None:
        """Move proceeds whose settlement date has arrived into settled cash."""
        due = [p for p in self.pending if p.settles_on <= today]
        self.pending = [p for p in self.pending if p.settles_on > today]
        self.settled += sum((p.amount for p in due), Decimal("0"))

    def resync(self, broker_settled: Decimal) -> None:
        """Adopt the broker's settled-cash figure (pre-open). The broker is the source of truth."""
        self.settled = broker_settled
