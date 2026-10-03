"""Pick the option to buy for a signal, and pick between underlyings ("cheapest fit").

Per underlying:
  1. 0DTE chain, calls for UP / puts for DOWN, bid >= min_bid.
  2. Delta from the broker, else the moneyness approximation (|delta| used for puts).
  3. Affordable: floor(budget / (ask × 100)) >= 1.
  4. Prefer the affordable contract nearest the middle of the target band (0.30–0.40);
     if none is in the band, the highest-delta affordable contract with |delta| >= min_delta.
Across underlyings: highest |delta| wins; near-ties (within tolerance) go to the earliest signal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from wall2.config import SelectionConfig
from wall2.core.types import CONTRACT_MULTIPLIER, OptionQuote, Right, Signal
from wall2.selection.delta import moneyness_delta, sigma_from_straddle, sigma_from_vol


@dataclass(frozen=True, slots=True)
class Selection:
    signal: Signal
    quote: OptionQuote
    delta: float  # absolute value
    delta_source: str  # "broker" | "moneyness"
    contracts: int

    @property
    def cost(self) -> Decimal:
        return self.quote.ask * CONTRACT_MULTIPLIER * self.contracts


@dataclass(frozen=True, slots=True)
class NoFit:
    signal: Signal
    reason: str


def contracts_affordable(budget: Decimal, ask: Decimal) -> int:
    if ask <= 0:
        return 0
    return int((budget / (ask * CONTRACT_MULTIPLIER)).to_integral_value(rounding=ROUND_FLOOR))


class ContractSelector:
    def __init__(self, cfg: SelectionConfig) -> None:
        self._cfg = cfg

    def select(
        self,
        signal: Signal,
        chain: Sequence[OptionQuote],
        spot: float,
        budget: Decimal,
        now: datetime,
        close: datetime,
    ) -> Selection | NoFit:
        cfg = self._cfg
        right = Right.for_direction(signal.direction)
        sigma: float | None = None

        def est_sigma() -> float:
            nonlocal sigma
            if sigma is None:
                sigma = sigma_from_straddle(chain, spot) or sigma_from_vol(
                    spot, cfg.fallback_annual_vol.get(signal.underlying, 0.20), now, close
                )
            return sigma

        cands: list[tuple[OptionQuote, float, str, int]] = []
        for q in chain:
            if q.contract.right is not right or q.bid < cfg.min_bid or q.ask <= 0:
                continue
            n = contracts_affordable(budget, q.ask)
            if n < 1:
                continue
            if q.delta is not None:
                d, src = abs(q.delta), "broker"
            else:
                d = abs(moneyness_delta(right, float(q.contract.strike), spot, est_sigma()))
                src = "moneyness"
            cands.append((q, d, src, n))

        if not cands:
            return NoFit(signal, f"no {right} with bid >= {cfg.min_bid} affordable for ${budget}")
        mid_target = (cfg.target_delta_low + cfg.target_delta_high) / 2
        in_band = [c for c in cands if cfg.target_delta_low <= c[1] <= cfg.target_delta_high]
        if in_band:
            q, d, src, n = min(in_band, key=lambda c: abs(c[1] - mid_target))
        else:
            q, d, src, n = max(cands, key=lambda c: c[1])
            if d < cfg.min_delta:
                return NoFit(signal, f"best affordable delta {d:.2f} below floor {cfg.min_delta}")
        return Selection(signal, q, d, src, n)

    def pick_cheapest_fit(self, selections: Sequence[Selection]) -> Selection | None:
        """Highest delta; near-ties (within tolerance of the best) go to the earliest signal."""
        if not selections:
            return None
        best = max(s.delta for s in selections)
        near = [s for s in selections if best - s.delta <= self._cfg.tie_delta_tolerance]
        return min(near, key=lambda s: s.signal.ts)
