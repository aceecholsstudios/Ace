"""Synthetic markets for tests, demos and replays without a broker: a Black-Scholes 0DTE chain
around the current price, and a scripted underlying path.

These are tools for exercising the bot's machinery. Their prices are model prices, not market
prices, so nothing measured on them says anything about the strategy's real edge.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from wall2.core.types import CENT, Bar, OptionContract, OptionQuote, Right
from wall2.selection.delta import norm_cdf

_YEAR_SECONDS = 252 * 6.5 * 3600


def bs_price_delta(
    right: Right, spot: float, strike: float, t_years: float, vol: float
) -> tuple[float, float]:
    if t_years <= 0 or vol <= 0:
        intrinsic = max(spot - strike, 0.0) if right is Right.CALL else max(strike - spot, 0.0)
        itm = intrinsic > 0
        return intrinsic, (1.0 if itm else 0.0) if right is Right.CALL else (-1.0 if itm else 0.0)
    sd = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    call = spot * norm_cdf(d1) - strike * norm_cdf(d2)
    if right is Right.CALL:
        return call, norm_cdf(d1)
    return call - spot + strike, norm_cdf(d1) - 1.0


def occ_symbol(underlying: str, expiry: date, right: Right, strike: Decimal) -> str:
    return (
        f"{underlying}{expiry:%y%m%d}{'C' if right is Right.CALL else 'P'}{int(strike * 1000):08d}"
    )


def _px(x: float) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def synthetic_chain(
    underlying: str,
    expiry: date,
    spot: float,
    now: datetime,
    close: datetime,
    vol: float = 0.18,
    strike_step: float = 1.0,
    n_strikes: int = 15,
    half_spread: Decimal = Decimal("0.01"),
    with_greeks: bool = True,
) -> list[OptionQuote]:
    t = max((close - now).total_seconds(), 0.0) / _YEAR_SECONDS
    atm = round(spot / strike_step) * strike_step
    out: list[OptionQuote] = []
    for i in range(-n_strikes, n_strikes + 1):
        k = atm + i * strike_step
        if k <= 0:
            continue
        strike = Decimal(str(k)).quantize(CENT)
        for right in (Right.CALL, Right.PUT):
            price, delta = bs_price_delta(right, spot, k, t, vol)
            mid = max(_px(price), Decimal("0.01"))
            bid = max(mid - half_spread, Decimal("0"))
            ask = mid + half_spread
            c = OptionContract(
                underlying, expiry, strike, right, occ_symbol(underlying, expiry, right, strike)
            )
            out.append(OptionQuote(c, bid, ask, delta if with_greeks else None, now))
    return out


def scripted_path(
    symbol: str,
    day: date,
    open_: datetime,
    minutes: int,
    start_price: float,
    drift_per_min: float,
    wave_amp: float,
    wave_period_min: float,
    volume: float = 10_000.0,
) -> Iterator[Bar]:
    """A trend plus a sine wave: produces regular pullbacks to a moving average."""
    prev = start_price
    for i in range(minutes):
        t0 = open_ + timedelta(minutes=i)
        price = (
            start_price
            + drift_per_min * (i + 1)
            + wave_amp * math.sin(2 * math.pi * (i + 1) / wave_period_min)
        )
        hi = max(prev, price) + 0.02
        lo = min(prev, price) - 0.02
        yield Bar(
            symbol, t0, 1, round(prev, 2), round(hi, 2), round(lo, 2), round(price, 2), volume
        )
        prev = price
