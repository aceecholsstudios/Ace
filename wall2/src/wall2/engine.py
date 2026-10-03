"""The trading engine: wires signals → selection → risk → orders → position management → shadow.

The engine is driven from outside: a data source calls `on_minute()` once per completed minute
(with that minute's 1-min bar for each underlying), after setting the clock to the minute's end.
That keeps the engine identical in live trading, paper trading, replays and tests.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from wall2.broker.base import Broker
from wall2.config import Wall2Config
from wall2.core.clock import Clock
from wall2.core.types import CONTRACT_MULTIPLIER, Bar, Signal
from wall2.execution.orders import EntryFill, OrderManager, OrderStuck
from wall2.execution.position import ExitDecision, ExitReason, Position, PositionRules
from wall2.persistence.db import Store
from wall2.risk.gate import DayState, Reject, RiskGate
from wall2.risk.settlement import SettlementLedger
from wall2.selection.selector import ContractSelector, NoFit, Selection
from wall2.session.calendar import SessionPlan, TradingCalendar
from wall2.shadow.tracker import ShadowTracker
from wall2.signals.setup import TrendUpdate, UnderlyingTracker

log = logging.getLogger("wall2.engine")


@dataclass(slots=True)
class _Pending:
    signal: Signal
    signal_id: int


class Engine:
    def __init__(
        self,
        cfg: Wall2Config,
        broker: Broker,
        store: Store,
        clock: Clock,
        calendar: TradingCalendar,
    ) -> None:
        self.cfg = cfg
        self.broker = broker
        self.store = store
        self.clock = clock
        self.calendar = calendar
        self.trackers = {u: UnderlyingTracker(u, cfg.signals) for u in cfg.underlyings}
        self.selector = ContractSelector(cfg.selection)
        self.gate = RiskGate(cfg.risk)
        self.rules = PositionRules(cfg.management)
        self.orders = OrderManager(broker, cfg.orders)
        self.fee = cfg.fees_per_contract
        self.shadow = ShadowTracker(self.rules, store, self.fee)
        self.ledger = SettlementLedger(Decimal("0"))
        self.day = DayState()
        self.budget = cfg.selection.budget_usd
        self.plan: SessionPlan | None = None
        self.positions: list[Position] = []
        self._last_entry_bar: dict[str, datetime] = {}
        self._closed_out = False

    # ---- session lifecycle -----------------------------------------------------------------
    def warmup(self, underlying: str, entry_bars: list[Bar]) -> None:
        self.trackers[underlying].warmup(entry_bars)

    async def preopen(self, day: date) -> SessionPlan | None:
        """Pre-open checks: calendar, settled cash, 0DTE chains, stray positions."""
        self.plan = self.calendar.plan(day)
        if self.plan is None:
            self.store.event(self.clock.now(), "no-session", str(day))
            return None
        self.day = DayState(paused=self.day.paused, disabled_etfs=set(self.day.disabled_etfs))
        self._closed_out = False
        self._last_entry_bar.clear()
        self.ledger.roll_to(day)
        self.ledger.resync(await self.broker.settled_cash())
        self.store.cash(day, settled_open=self.ledger.settled)
        for u, t in self.trackers.items():
            t.start_session()
            chain = await self.broker.option_chain(u, day)
            if not chain:
                self.day.disabled_etfs.add(u)
                self.store.event(self.clock.now(), "etf-disabled", f"{u}: no 0DTE chain for {day}")
        await self._adopt_strays()
        return self.plan

    async def _adopt_strays(self) -> None:
        known = {p.contract.symbol for p in self.positions}
        for bp in await self.broker.positions():
            if bp.contract.symbol in known or bp.qty <= 0:
                continue
            if bp.contract.underlying not in self.trackers:
                self.store.event(
                    self.clock.now(), "stray-ignored", f"{bp.contract.symbol}: untracked underlying"
                )
                continue
            pos = Position(bp.contract, bp.qty, bp.avg_price, self.clock.now(), adopted=True)
            pos.trade_id = self.store.open_trade(
                day=self.clock.now().date(),
                underlying=pos.underlying,
                contract=bp.contract.symbol,
                right_=bp.contract.right,
                strike=bp.contract.strike,
                qty=bp.qty,
                adopted=1,
                entry_ts=pos.opened_at,
                entry_price=bp.avg_price,
                entry_fees=Decimal("0"),
            )
            self.positions.append(pos)
            self.store.event(self.clock.now(), "stray-adopted", bp.contract.symbol)

    # ---- per-minute driver -----------------------------------------------------------------
    async def on_minute(self, bars: dict[str, Bar]) -> None:
        assert self.plan is not None, "call preopen() first"
        now = self.clock.now()
        signals: list[Signal] = []
        new_entry_bar: set[str] = set()
        for u, bar in bars.items():
            t = self.trackers[u]
            for ev in t.on_minute(bar):
                if isinstance(ev, TrendUpdate):
                    self.day.on_trend_bar(u, ev.direction)
                    if ev.changed:
                        self.store.event(now, "trend-flip", f"{u} {ev.direction}")
                else:
                    signals.append(ev)
            eb = t.last_entry_bar
            if eb is not None and self._last_entry_bar.get(u) != eb.start:
                self._last_entry_bar[u] = eb.start
                new_entry_bar.add(u)

        try:
            await self._manage_exits(new_entry_bar)
            if now >= self.plan.closeout and not self._closed_out:
                await self.closeout()
            if signals:
                await self._handle_signals(signals)
        except OrderStuck as e:
            self.day.paused = True
            self.store.event(now, "order-stuck", str(e))
            log.error("pausing: %s", e)

    # ---- exits -----------------------------------------------------------------------------
    async def _manage_exits(self, new_entry_bar: set[str]) -> None:
        for pos in [*self.positions, *self.shadow.open_positions]:
            t = self.trackers[pos.underlying]
            ema = t.ema.value
            if ema is None or t.last_minute is None:
                continue
            q = await self.broker.option_quote(pos.contract)
            decision: ExitDecision | None = None
            if pos.underlying in new_entry_bar and t.last_entry_bar is not None:
                decision = self.rules.on_entry_bar_close(pos, t.last_entry_bar.close, ema, q.bid)
            if decision is None:
                decision = self.rules.on_minute_close(pos, t.last_minute.close, ema, q.bid)
            if decision is None:
                continue
            if pos in self.positions:
                await self._exit(pos, decision)
            else:
                self.shadow.apply(pos, decision, q.bid, self.clock.now())

    async def _exit(self, pos: Position, decision: ExitDecision) -> None:
        fill = await self.orders.sell(pos.contract, pos.qty, decision.profit)
        now = self.clock.now()
        if fill.qty:
            proceeds = fill.avg_price * CONTRACT_MULTIPLIER * fill.qty - fill.fees
            self.ledger.record_sale(proceeds, self.calendar.settlement_date(now.date()))
        if fill.unsold:
            # No bid: the remainder is worthless and will expire; keep it for the close-out.
            pos.qty = fill.unsold
            self.store.event(now, "exit-partial", f"{pos.contract.symbol}: {fill.unsold} unsold")
            if fill.qty == 0:
                return
        self._record_close(pos, fill.qty, fill.avg_price, fill.fees, decision.reason.value, now)
        if not fill.unsold:
            self.positions.remove(pos)
            if not pos.adopted:
                self.day.on_exit(pos.underlying)

    def _record_close(
        self,
        pos: Position,
        qty: int,
        price: Decimal,
        exit_fees: Decimal,
        reason: str,
        now: datetime,
    ) -> None:
        if pos.trade_id is None:
            return
        row = self.store.rows("SELECT entry_fees FROM trades WHERE id = ?", pos.trade_id)[0]
        pnl = (
            (price - pos.entry_price) * CONTRACT_MULTIPLIER * qty
            - exit_fees
            - Decimal(row["entry_fees"])
        )
        self.store.close_trade(
            pos.trade_id,
            exit_ts=now,
            exit_price=price,
            exit_fees=exit_fees,
            exit_reason=reason,
            unsold=pos.qty - qty,
            pnl=pnl.quantize(Decimal("0.01")),
        )

    async def closeout(self) -> None:
        """14:50 CT (11:50 on half-days): sell anything in the money; leave OTM to expire."""
        self._closed_out = True
        now = self.clock.now()
        for pos in [*self.positions, *self.shadow.open_positions]:
            spot = self.trackers[pos.underlying].last_price
            if spot is None:
                continue
            q = await self.broker.option_quote(pos.contract)
            d = self.rules.on_closeout(pos, spot, q.bid)
            if d is None:
                continue
            if pos in self.positions:
                await self._exit(pos, d)
            else:
                self.shadow.apply(pos, d, q.bid, now)
        self.store.event(
            now, "closeout", f"open after close-out: {[p.contract.symbol for p in self.positions]}"
        )

    async def end_of_day(self) -> None:
        """After the close: remaining options expired worthless (ITM ones were sold earlier)."""
        now = self.clock.now()
        for pos in list(self.positions):
            self._record_close(pos, pos.qty, Decimal("0"), Decimal("0"), "expired", now)
            self.positions.remove(pos)
            if not pos.adopted:
                self.day.on_exit(pos.underlying)
        self.shadow.expire_all(now)
        self.store.cash(
            now.date(), settled_close=self.ledger.settled, unsettled_close=self.ledger.unsettled
        )

    # ---- entries ---------------------------------------------------------------------------
    async def _select(self, sig: Signal) -> Selection | NoFit:
        assert self.plan is not None
        t = self.trackers[sig.underlying]
        spot = t.last_price or sig.underlying_price
        chain = await self.broker.option_chain(sig.underlying, self.plan.day)
        return self.selector.select(
            sig, chain, spot, self.budget, self.clock.now(), self.plan.close
        )

    def _reselector(self, sig: Signal) -> Callable[[], Awaitable[Selection | NoFit]]:
        async def again() -> Selection | NoFit:
            return await self._select(sig)

        return again

    async def _handle_signals(self, signals: list[Signal]) -> None:
        assert self.plan is not None
        now = self.clock.now()
        candidates: list[tuple[Selection, int]] = []
        for sig in signals:
            sid = self.store.add_signal(
                ts=sig.ts,
                day=now.date(),
                underlying=sig.underlying,
                direction=sig.direction,
                trigger_level=sig.trigger_level,
                price=sig.underlying_price,
                reason=sig.reason,
                outcome="pending",
            )
            rej = self.gate.check_signal(sig, self.day, self.plan, now)
            sel = await self._select(sig)
            if rej is not None:
                self._skip(sid, rej, sel if isinstance(sel, Selection) else None)
                continue
            if isinstance(sel, NoFit):
                self._skip(sid, Reject.NO_FIT, None, detail=sel.reason)
                continue
            candidates.append((sel, sid))

        # Cheapest fit first (highest delta; near-ties → earliest), then the rest if caps allow.
        ordered: list[tuple[Selection, int]] = []
        pool = list(candidates)
        while pool:
            best = self.selector.pick_cheapest_fit([s for s, _ in pool])
            item = next(c for c in pool if c[0] is best)
            ordered.append(item)
            pool.remove(item)

        for sel, sid in ordered:
            rej = self.gate.check_signal(sel.signal, self.day, self.plan, now)
            if rej is None:
                rej = self.gate.check_selection(sel, self.ledger, self.fee)
            if rej is not None:
                self._skip(sid, rej, sel)
                continue
            result = await self.orders.buy(sel, self._reselector(sel.signal))
            if isinstance(result, EntryFill):
                self._open(sid, result)
            else:
                self._skip(
                    sid, Reject.ORDER_NOT_FILLED, result.last_selection, detail=result.reason
                )

    def _open(self, sid: int, f: EntryFill) -> None:
        now = self.clock.now()
        sel = f.selection
        cost = f.price * CONTRACT_MULTIPLIER * f.qty + f.fees
        self.ledger.record_buy(cost)
        self.day.on_entry(sel.signal.underlying, sel.signal.direction)
        c = sel.quote.contract
        pos = Position(c, f.qty, f.price, now)
        pos.trade_id = self.store.open_trade(
            signal_id=sid,
            day=now.date(),
            underlying=c.underlying,
            contract=c.symbol,
            right_=c.right,
            strike=c.strike,
            qty=f.qty,
            adopted=0,
            entry_ts=now,
            entry_price=f.price,
            entry_fees=f.fees,
            delta=sel.delta,
        )
        self.positions.append(pos)
        self.store.update_signal(
            sid,
            outcome="traded",
            contract=c.symbol,
            delta=sel.delta,
            delta_source=sel.delta_source,
            ask=sel.quote.ask,
            contracts=f.qty,
        )

    def _skip(self, sid: int, rej: Reject, sel: Selection | None, detail: str = "") -> None:
        reason = f"{rej.value}: {detail}" if detail else rej.value
        if sel is None:
            self.store.update_signal(sid, outcome="skipped", reject_reason=reason)
            return
        self.store.update_signal(
            sid,
            outcome="skipped",
            reject_reason=reason,
            contract=sel.quote.contract.symbol,
            delta=sel.delta,
            delta_source=sel.delta_source,
            ask=sel.quote.ask,
            contracts=sel.contracts,
        )
        now = self.clock.now()
        pos = Position(sel.quote.contract, sel.contracts, sel.quote.ask, now)
        self.shadow.start(sid, now.date(), pos)

    # ---- operator commands -----------------------------------------------------------------
    def pause(self) -> None:
        self.day.paused = True
        self.store.event(self.clock.now(), "pause")

    def resume(self) -> None:
        self.day.paused = False
        self.store.event(self.clock.now(), "resume")

    def set_etf_enabled(self, underlying: str, enabled: bool) -> None:
        (self.day.disabled_etfs.discard if enabled else self.day.disabled_etfs.add)(underlying)
        self.store.event(self.clock.now(), "etf-toggle", f"{underlying}={enabled}")

    def set_budget(self, usd: Decimal) -> None:
        if usd <= 0:
            raise ValueError("budget must be positive")
        self.store.event(self.clock.now(), "budget", f"{self.budget} -> {usd}")
        self.budget = usd

    def set_halted(self, underlying: str, halted: bool) -> None:
        (self.day.halted_etfs.add if halted else self.day.halted_etfs.discard)(underlying)
        self.store.event(self.clock.now(), "halt" if halted else "halt-cleared", underlying)

    async def close_all(self) -> None:
        for pos in list(self.positions):
            await self._exit(pos, ExitDecision(ExitReason.CLOSE_ALL, profit=False))
