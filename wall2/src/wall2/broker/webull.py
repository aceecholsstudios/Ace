"""Webull adapter: placeholder until milestone M1 (run on your PC, with your API access).

M1 must confirm with the official Webull OpenAPI, then implement each method below:
  - auth: app key/secret → token, and token refresh
  - settled_cash: cash-account settled vs unsettled funds
  - positions: open option positions with average price
  - option_chain: same-day (0DTE) chain for SPY/QQQ/IWM with bid/ask and greeks (if provided)
  - option_quote: latest streamed quote for one contract (streaming cache, not a REST poll)
  - place / cancel / order / wait: single-leg option LIMIT orders and their status updates
  - paper trading: whether the API supports a paper account (else use the built-in simulator)
  - rate and subscription limits
If Webull can't do something essential (e.g. 0DTE option orders), the fallback is decided then;
nothing outside this file depends on Webull.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from wall2.broker.base import BrokerPosition, OrderRequest, OrderState
from wall2.core.types import OptionContract, OptionQuote

_M1 = "Webull adapter not implemented yet: milestone M1 (verify the API on your PC)"


class WebullSettings(BaseSettings):
    """Read from the environment / .env. Variable names to confirm in M1."""

    model_config = SettingsConfigDict(env_prefix="WEBULL_", env_file=".env", extra="ignore")
    app_key: SecretStr = SecretStr("")
    app_secret: SecretStr = SecretStr("")
    account_id: str = ""

    @property
    def present(self) -> bool:
        return bool(self.app_key.get_secret_value() and self.app_secret.get_secret_value())


class WebullBroker:
    def __init__(self, settings: WebullSettings) -> None:
        self._s = settings

    async def settled_cash(self) -> Decimal:
        raise NotImplementedError(_M1)

    async def positions(self) -> list[BrokerPosition]:
        raise NotImplementedError(_M1)

    async def option_chain(self, underlying: str, expiry: date) -> list[OptionQuote]:
        raise NotImplementedError(_M1)

    async def option_quote(self, contract: OptionContract) -> OptionQuote:
        raise NotImplementedError(_M1)

    async def place(self, req: OrderRequest) -> str:
        raise NotImplementedError(_M1)

    async def cancel(self, order_id: str) -> None:
        raise NotImplementedError(_M1)

    async def order(self, order_id: str) -> OrderState:
        raise NotImplementedError(_M1)

    async def wait(self, order_id: str, timeout: float) -> OrderState:
        raise NotImplementedError(_M1)
