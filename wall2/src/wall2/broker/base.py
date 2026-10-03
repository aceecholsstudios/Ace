"""Broker interface. The Webull adapter and the built-in simulator both implement it.

Keeping strategy code behind this interface is what lets the whole bot run against the simulator
today, and lets the M1 broker decision (Webull or a fallback) be made without touching the logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from wall2.core.types import OptionContract, OptionQuote


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    WORKING = "working"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        return self is not OrderStatus.WORKING


@dataclass(frozen=True, slots=True)
class OrderRequest:
    contract: OptionContract
    side: OrderSide
    qty: int
    limit_price: Decimal
    client_id: str


@dataclass(frozen=True, slots=True)
class OrderState:
    order_id: str
    request: OrderRequest
    status: OrderStatus
    filled_qty: int = 0
    avg_fill_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    reject_reason: str = ""

    @property
    def remaining(self) -> int:
        return self.request.qty - self.filled_qty


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    contract: OptionContract
    qty: int
    avg_price: Decimal


class BrokerError(Exception):
    pass


class Broker(Protocol):
    async def settled_cash(self) -> Decimal: ...
    async def positions(self) -> list[BrokerPosition]: ...
    async def option_chain(self, underlying: str, expiry: date) -> list[OptionQuote]: ...
    async def option_quote(self, contract: OptionContract) -> OptionQuote: ...
    async def place(self, req: OrderRequest) -> str: ...
    async def cancel(self, order_id: str) -> None: ...
    async def order(self, order_id: str) -> OrderState: ...
    async def wait(self, order_id: str, timeout: float) -> OrderState:
        """Return when the order is terminal or `timeout` seconds pass, whichever is first."""
        ...
