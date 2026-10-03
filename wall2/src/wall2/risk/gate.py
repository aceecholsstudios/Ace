"""Pre-trade checks. The first failing check rejects the trade with a reason for the shadow log."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from wall2.config import RiskConfig
from wall2.core.types import Direction, Signal
from wall2.risk.settlement import SettlementLedger
from wall2.selection.selector import Selection
from wall2.session.calendar import SessionPlan


class Reject(StrEnum):
    PAUSED = "paused"
    ETF_DISABLED = "etf disabled"
    BEFORE_FIRST_ENTRY = "before first entry"
    AFTER_CLOSEOUT = "after close-out"
    HALTED = "quotes stale / halted"
    TRADE_CAP = "daily trade cap reached"
    POSITION_OPEN = "position already open on this ETF"
    DIRECTION_LOCK = "direction locked until 15-min trend flips"
    NO_SETTLED_CASH = "not enough settled cash"
    NO_FIT = "no contract fits budget / delta floor"
    ORDER_NOT_FILLED = "entry order not filled"


@dataclass(slots=True)
class DayState:
    """Bot-owned trading state for the current session. Adopted positions are never counted here."""

    trades_today: int = 0
    open_etfs: set[str] = field(default_factory=set)
    direction_lock: dict[str, Direction] = field(default_factory=dict)
    paused: bool = False
    disabled_etfs: set[str] = field(default_factory=set)
    halted_etfs: set[str] = field(default_factory=set)

    def on_entry(self, underlying: str, direction: Direction) -> None:
        self.trades_today += 1
        self.open_etfs.add(underlying)
        self.direction_lock[underlying] = direction

    def on_exit(self, underlying: str) -> None:
        self.open_etfs.discard(underlying)

    def on_trend_bar(self, underlying: str, trend: Direction) -> None:
        """A completed 15-min bar closed on the `trend` side of VWAP; lifts an opposite lock."""
        new_trend = trend
        lock = self.direction_lock.get(underlying)
        if lock is not None and lock != new_trend:
            del self.direction_lock[underlying]


class RiskGate:
    def __init__(self, cfg: RiskConfig) -> None:
        self._cfg = cfg

    def check_signal(
        self, sig: Signal, day: DayState, plan: SessionPlan, now: datetime
    ) -> Reject | None:
        """Checks that don't need a selected contract."""
        u = sig.underlying
        if day.paused:
            return Reject.PAUSED
        if u in day.disabled_etfs:
            return Reject.ETF_DISABLED
        if now < plan.first_entry:
            return Reject.BEFORE_FIRST_ENTRY
        if now >= plan.closeout:
            return Reject.AFTER_CLOSEOUT
        if u in day.halted_etfs:
            return Reject.HALTED
        if day.trades_today >= self._cfg.max_trades_per_day:
            return Reject.TRADE_CAP
        if self._cfg.one_position_per_etf and u in day.open_etfs:
            return Reject.POSITION_OPEN
        lock = day.direction_lock.get(u)
        if lock is not None and lock != sig.direction:
            return Reject.DIRECTION_LOCK
        return None

    def check_selection(
        self, sel: Selection, ledger: SettlementLedger, fee_per_contract: Decimal
    ) -> Reject | None:
        cost = sel.cost + fee_per_contract * sel.contracts
        if self._cfg.settled_cash_only and not ledger.can_afford(cost):
            return Reject.NO_SETTLED_CASH
        return None
