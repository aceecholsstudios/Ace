"""In-process paper broker. Fills are conservative: buys fill at the ask, sells at the bid.

A buy limit fills when limit >= ask (at the ask); a sell limit fills when limit <= bid (at the
bid). So a sell at the midpoint only fills if the bid rises to it. Quotes are pushed in by the
caller (live Webull quotes in paper mode, synthetic quotes in tests and replays).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from dataclasses import replace
from datetime import date
from decimal import Decimal

from wall2.broker.base import (
    BrokerError,
    BrokerPosition,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderStatus,
)
from wall2.core.types import CONTRACT_MULTIPLIER, OptionContract, OptionQuote


class SimBroker:
    def __init__(self, settled_cash: Decimal, fee_per_contract: Decimal = Decimal("0")) -> None:
        self._settled = settled_cash
        self._unsettled = Decimal("0")
        self._fee = fee_per_contract
        self._quotes: dict[str, OptionQuote] = {}
        self._orders: dict[str, OrderState] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._pos: dict[str, BrokerPosition] = {}
        self._ids = itertools.count(1)
        # Test hooks
        self.never_fill = False
        self.fill_on_cancel = False

    # ---- market data -----------------------------------------------------------------------
    def set_quote(self, q: OptionQuote) -> None:
        self._quotes[q.contract.symbol] = q
        for oid, st in list(self._orders.items()):
            if st.status is OrderStatus.WORKING and st.request.contract.symbol == q.contract.symbol:
                self._try_fill(oid)

    async def option_chain(self, underlying: str, expiry: date) -> list[OptionQuote]:
        return [
            q
            for q in self._quotes.values()
            if q.contract.underlying == underlying and q.contract.expiry == expiry
        ]

    async def option_quote(self, contract: OptionContract) -> OptionQuote:
        try:
            return self._quotes[contract.symbol]
        except KeyError:
            raise BrokerError(f"no quote for {contract.symbol}") from None

    # ---- account ---------------------------------------------------------------------------
    async def settled_cash(self) -> Decimal:
        return self._settled

    def settle_all(self) -> None:
        """Advance a day: yesterday's proceeds settle."""
        self._settled += self._unsettled
        self._unsettled = Decimal("0")

    async def positions(self) -> list[BrokerPosition]:
        return [p for p in self._pos.values() if p.qty != 0]

    def add_position(self, p: BrokerPosition) -> None:
        """Test hook: a position the bot didn't open."""
        self._pos[p.contract.symbol] = p

    # ---- orders ----------------------------------------------------------------------------
    async def place(self, req: OrderRequest) -> str:
        if req.qty < 1 or req.limit_price <= 0:
            raise BrokerError("bad order")
        oid = f"SIM{next(self._ids)}"
        if req.side is OrderSide.BUY:
            cost = req.limit_price * CONTRACT_MULTIPLIER * req.qty + self._fee * req.qty
            if cost > self._settled:
                st = OrderState(
                    oid, req, OrderStatus.REJECTED, reject_reason="insufficient settled cash"
                )
                self._orders[oid] = st
                self._events[oid] = asyncio.Event()
                self._events[oid].set()
                return oid
        else:
            held = self._pos.get(req.contract.symbol)
            if held is None or held.qty < req.qty:
                raise BrokerError("cannot sell more than held (no short options)")
        self._orders[oid] = OrderState(oid, req, OrderStatus.WORKING)
        self._events[oid] = asyncio.Event()
        self._try_fill(oid)
        return oid

    async def cancel(self, order_id: str) -> None:
        st = self._orders[order_id]
        if st.status.terminal:
            return
        if self.fill_on_cancel:
            q = self._quotes.get(st.request.contract.symbol)
            price = (
                (q.ask if st.request.side is OrderSide.BUY else q.bid)
                if q
                else st.request.limit_price
            )
            self._fill(order_id, price)
            return
        self._orders[order_id] = replace(st, status=OrderStatus.CANCELLED)
        self._events[order_id].set()

    async def order(self, order_id: str) -> OrderState:
        return self._orders[order_id]

    async def wait(self, order_id: str, timeout: float) -> OrderState:
        ev = self._events[order_id]
        if not ev.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout)
        return self._orders[order_id]

    # ---- internals -------------------------------------------------------------------------
    def _try_fill(self, oid: str) -> None:
        if self.never_fill:
            return
        st = self._orders[oid]
        q = self._quotes.get(st.request.contract.symbol)
        if q is None:
            return
        r = st.request
        if r.side is OrderSide.BUY and r.limit_price >= q.ask > 0:
            self._fill(oid, q.ask)
        elif r.side is OrderSide.SELL and q.bid > 0 and r.limit_price <= q.bid:
            self._fill(oid, q.bid)

    def _fill(self, oid: str, price: Decimal) -> None:
        st = self._orders[oid]
        r = st.request
        fees = self._fee * r.qty
        gross = price * CONTRACT_MULTIPLIER * r.qty
        sym = r.contract.symbol
        held = self._pos.get(sym)
        if r.side is OrderSide.BUY:
            self._settled -= gross + fees
            if held is None:
                self._pos[sym] = BrokerPosition(r.contract, r.qty, price)
            else:
                n = held.qty + r.qty
                avg = (held.avg_price * held.qty + price * r.qty) / n
                self._pos[sym] = BrokerPosition(r.contract, n, avg)
        else:
            self._unsettled += gross - fees
            assert held is not None
            self._pos[sym] = replace(held, qty=held.qty - r.qty)
        self._orders[oid] = replace(
            st, status=OrderStatus.FILLED, filled_qty=r.qty, avg_fill_price=price, fees=fees
        )
        self._events[oid].set()

    def expire(self, expiry: date, underlying_close: dict[str, float]) -> list[str]:
        """Expire positions. Return the ITM ones (would be exercised: must never happen)."""
        exercised: list[str] = []
        for sym, p in list(self._pos.items()):
            if p.qty and p.contract.expiry == expiry:
                if p.contract.is_itm(underlying_close[p.contract.underlying]):
                    exercised.append(sym)
                self._pos[sym] = replace(p, qty=0)
        return exercised
