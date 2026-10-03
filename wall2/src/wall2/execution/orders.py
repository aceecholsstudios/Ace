"""Order placement with the cancel/fill race handled.

Rule: after requesting a cancel, ALWAYS wait for the order's final state before sending anything
else. A cancel and a fill can cross in flight; acting on the cancel request alone is how bots end
up with double positions.

Entry: limit at the ask → wait → cancel+confirm → re-select (strike may change) → try again
(`reprice_attempts` times). Exit: profit exits try the midpoint first, then the bid; other exits
go straight to the bid. Exits re-price at the latest bid until filled or retries run out.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from wall2.broker.base import Broker, BrokerError, OrderRequest, OrderSide, OrderState, OrderStatus
from wall2.config import OrderConfig
from wall2.core.types import OptionContract
from wall2.selection.selector import NoFit, Selection

_ids = itertools.count(1)


def _client_id(prefix: str) -> str:
    return f"w2-{prefix}-{next(_ids)}"


class OrderStuck(BrokerError):
    """An order did not reach a final state after a cancel. Trading must pause."""


@dataclass(frozen=True, slots=True)
class EntryFill:
    selection: Selection
    qty: int
    price: Decimal
    fees: Decimal
    attempts: int


@dataclass(frozen=True, slots=True)
class EntryFailed:
    reason: str
    last_selection: Selection | None
    attempts: int


@dataclass(frozen=True, slots=True)
class ExitFill:
    qty: int
    avg_price: Decimal
    fees: Decimal
    unsold: int  # contracts that could not be sold (e.g. no bid)


class OrderManager:
    def __init__(self, broker: Broker, cfg: OrderConfig) -> None:
        self._b = broker
        self._cfg = cfg

    async def _cancel_and_confirm(self, oid: str) -> OrderState:
        await self._b.cancel(oid)
        st = await self._b.wait(oid, self._cfg.cancel_confirm_timeout_sec)
        if not st.status.terminal:
            raise OrderStuck(
                f"order {oid} not final {self._cfg.cancel_confirm_timeout_sec}s after cancel"
            )
        return st

    async def _work(self, req: OrderRequest, wait_sec: float) -> OrderState:
        """Place, wait, and if still working cancel and confirm. Returns the final state."""
        oid = await self._b.place(req)
        st = await self._b.wait(oid, wait_sec)
        if st.status is OrderStatus.WORKING:
            st = await self._cancel_and_confirm(oid)
        return st

    async def buy(
        self, first: Selection, reselect: Callable[[], Awaitable[Selection | NoFit]]
    ) -> EntryFill | EntryFailed:
        sel: Selection = first
        attempts = 0
        for attempt in range(1 + self._cfg.reprice_attempts):
            attempts = attempt + 1
            if attempt > 0:
                nxt = await reselect()
                if isinstance(nxt, NoFit):
                    return EntryFailed(f"re-select: {nxt.reason}", sel, attempts)
                sel = nxt
            req = OrderRequest(
                sel.quote.contract, OrderSide.BUY, sel.contracts, sel.quote.ask, _client_id("buy")
            )
            st = await self._work(req, self._cfg.fill_wait_sec)
            if st.filled_qty > 0:
                return EntryFill(sel, st.filled_qty, st.avg_fill_price, st.fees, attempts)
            if st.status is OrderStatus.REJECTED:
                return EntryFailed(f"rejected: {st.reject_reason}", sel, attempts)
        return EntryFailed("not filled at ask", sel, attempts)

    async def sell(self, contract: OptionContract, qty: int, profit: bool) -> ExitFill:
        remaining = qty
        notional = Decimal("0")
        fees = Decimal("0")

        def take(st: OrderState) -> None:
            nonlocal remaining, notional, fees
            remaining -= st.filled_qty
            notional += st.avg_fill_price * st.filled_qty
            fees += st.fees

        if profit:
            q = await self._b.option_quote(contract)
            if q.mid > 0:
                take(
                    await self._work(
                        OrderRequest(
                            contract, OrderSide.SELL, remaining, q.mid, _client_id("sell-mid")
                        ),
                        self._cfg.profit_exit_mid_wait_sec,
                    )
                )
        tries = 0
        while remaining > 0 and tries < self._cfg.exit_bid_retries:
            tries += 1
            q = await self._b.option_quote(contract)
            if q.bid <= 0:
                break  # nothing to sell into; worthless
            take(
                await self._work(
                    OrderRequest(
                        contract, OrderSide.SELL, remaining, q.bid, _client_id("sell-bid")
                    ),
                    self._cfg.fill_wait_sec,
                )
            )
        sold = qty - remaining
        avg = (notional / sold).quantize(Decimal("0.0001")) if sold else Decimal("0")
        return ExitFill(sold, avg, fees, remaining)
