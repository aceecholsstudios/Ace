"""Shadow tracking: follow the option a skipped signal *would* have bought, apply the real exit
rules to its live quotes, and record what it would have returned (entry at the ask, exits at the
bid, fees included). Skipped signals with no selectable contract are recorded without an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from wall2.core.types import CONTRACT_MULTIPLIER
from wall2.execution.position import ExitDecision, Position, PositionRules
from wall2.persistence.db import Store


@dataclass(slots=True)
class _Shadow:
    shadow_id: int
    pos: Position


class ShadowTracker:
    def __init__(self, rules: PositionRules, store: Store, fee_per_contract: Decimal) -> None:
        self._rules = rules
        self._store = store
        self._fee = fee_per_contract
        self._open: list[_Shadow] = []

    @property
    def open_positions(self) -> list[Position]:
        return [s.pos for s in self._open]

    def start(self, signal_id: int, day: object, pos: Position) -> None:
        sid = self._store.open_shadow(
            signal_id=signal_id,
            day=day,
            contract=pos.contract.symbol,
            qty=pos.qty,
            entry_ts=pos.opened_at,
            entry_price=pos.entry_price,
        )
        self._open.append(_Shadow(sid, pos))

    def apply(
        self, pos: Position, decision: ExitDecision | None, bid: Decimal, now: datetime
    ) -> None:
        if decision is None:
            return
        for s in list(self._open):
            if s.pos is pos:
                self._close(s, bid, decision.reason.value, now)

    def expire_all(self, now: datetime) -> None:
        """At the close: anything still open expired worthless (ITM ones were sold at close-out)."""
        for s in list(self._open):
            self._close(s, Decimal("0"), "expired", now)

    def _close(self, s: _Shadow, price: Decimal, reason: str, now: datetime) -> None:
        p = s.pos
        fees = self._fee * p.qty * (2 if price > 0 else 1)
        pnl = (price - p.entry_price) * CONTRACT_MULTIPLIER * p.qty - fees
        self._store.close_shadow(
            s.shadow_id,
            exit_ts=now,
            exit_price=price,
            exit_reason=reason,
            pnl=pnl.quantize(Decimal("0.01")),
        )
        self._open.remove(s)
