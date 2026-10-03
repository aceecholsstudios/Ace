"""Exit rules for an open option. Pure decisions; the engine executes them.

  In profit (bid > entry):
    5-min close through the 9 EMA → exit (mid, then bid).
    After the gain has reached +100% (tightened): a 1-min close through the 9 EMA also exits.
  Not in profit, on a trend break:
    bid >= 50% of entry → salvage exit at the bid; otherwise hold to expiry.
  Close-out (14:50 CT, 11:50 on half-days):
    in the money → sell (mid-then-bid if in profit, else bid). Out of the money → let it expire.

"Through the 9 EMA" means: calls → close below it; puts → close above it. The EMA is the 5-min
9 EMA in both cases; the tightened rule just checks it on every 1-min close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from wall2.config import ManagementConfig
from wall2.core.types import OptionContract, Right


class ExitReason(StrEnum):
    TRAIL = "trend break (5-min close through 9 EMA)"
    TIGHT_TRAIL = "tightened trail (1-min close through 9 EMA)"
    SALVAGE = "trend break, salvage >= threshold"
    CLOSEOUT_ITM = "close-out: in the money"
    CLOSE_ALL = "close-all command"


@dataclass(slots=True)
class Position:
    contract: OptionContract
    qty: int
    entry_price: Decimal  # per share
    opened_at: datetime
    adopted: bool = False
    peak_gain_pct: float = 0.0
    trade_id: int | None = None

    @property
    def underlying(self) -> str:
        return self.contract.underlying

    def gain_pct(self, bid: Decimal) -> float:
        if self.entry_price <= 0:
            return 0.0
        return float((bid - self.entry_price) / self.entry_price * 100)


@dataclass(frozen=True, slots=True)
class ExitDecision:
    reason: ExitReason
    profit: bool  # True → try the midpoint first


def trend_broken(right: Right, close: float, ema: float) -> bool:
    return close < ema if right is Right.CALL else close > ema


class PositionRules:
    def __init__(self, cfg: ManagementConfig) -> None:
        self._cfg = cfg

    def mark(self, pos: Position, bid: Decimal) -> None:
        pos.peak_gain_pct = max(pos.peak_gain_pct, pos.gain_pct(bid))

    def tightened(self, pos: Position) -> bool:
        return pos.peak_gain_pct >= self._cfg.tighten_after_gain_pct

    def _on_break(self, pos: Position, bid: Decimal, reason: ExitReason) -> ExitDecision | None:
        if bid > pos.entry_price:
            return ExitDecision(reason, profit=True)
        if bid >= pos.entry_price * Decimal(self._cfg.salvage_min_value_pct) / 100 and bid > 0:
            return ExitDecision(ExitReason.SALVAGE, profit=False)
        return None  # hold to expiry

    def on_entry_bar_close(
        self, pos: Position, close: float, ema: float, bid: Decimal
    ) -> ExitDecision | None:
        self.mark(pos, bid)
        if trend_broken(pos.contract.right, close, ema):
            return self._on_break(pos, bid, ExitReason.TRAIL)
        return None

    def on_minute_close(
        self, pos: Position, close: float, ema: float, bid: Decimal
    ) -> ExitDecision | None:
        self.mark(pos, bid)
        if self.tightened(pos) and trend_broken(pos.contract.right, close, ema):
            return self._on_break(pos, bid, ExitReason.TIGHT_TRAIL)
        return None

    def on_closeout(
        self, pos: Position, underlying_price: float, bid: Decimal
    ) -> ExitDecision | None:
        if pos.contract.is_itm(underlying_price):
            return ExitDecision(ExitReason.CLOSEOUT_ITM, profit=bid > pos.entry_price)
        return None
