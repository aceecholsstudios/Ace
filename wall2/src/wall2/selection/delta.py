"""Delta approximation from moneyness, used only when the broker doesn't supply greeks.

The idea: on expiration day, the underlying's remaining move is roughly normal with standard
deviation σ_move. A call finishes in the money if the move exceeds (K − S), so

    delta_call ≈ P(finish ITM) ≈ Φ((S − K) / σ_move),   delta_put = delta_call − 1.

σ_move comes from the market when possible: an at-the-money straddle is worth about E|S_T − K|,
which for a normal move is σ_move·√(2/π) ≈ 0.798·σ_move. Otherwise it falls back to an annual
volatility scaled to the time left:  σ_move = S·σ_annual·√(t_years).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from wall2.core.types import OptionQuote, Right

_SQRT_2_OVER_PI = math.sqrt(2 / math.pi)
_TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def sigma_from_straddle(chain: Sequence[OptionQuote], spot: float) -> float | None:
    """σ_move implied by the mid of the nearest-to-spot strike that has both a call and a put."""
    calls = {q.contract.strike: q for q in chain if q.contract.right is Right.CALL}
    puts = {q.contract.strike: q for q in chain if q.contract.right is Right.PUT}
    both = sorted(set(calls) & set(puts), key=lambda k: abs(float(k) - spot))
    for k in both[:1]:
        straddle = float(calls[k].mid + puts[k].mid)
        if straddle > 0:
            return straddle / _SQRT_2_OVER_PI
    return None


def sigma_from_vol(spot: float, annual_vol: float, now: datetime, close: datetime) -> float:
    secs = max((close - now).total_seconds(), 60.0)
    return spot * annual_vol * math.sqrt(secs / _TRADING_SECONDS_PER_YEAR)


def moneyness_delta(right: Right, strike: float, spot: float, sigma_move: float) -> float:
    if sigma_move <= 0:
        raise ValueError("sigma_move must be positive")
    d_call = norm_cdf((spot - strike) / sigma_move)
    return d_call if right is Right.CALL else d_call - 1.0
