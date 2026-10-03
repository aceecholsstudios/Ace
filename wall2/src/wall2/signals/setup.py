"""Trend-pullback setup detection for one underlying.

    15-min trend (price vs VWAP) → 5-min bar touches the 9 EMA (pullback bar)
    → 1-min close beyond the pullback bar's high (calls) / low (puts) = Signal.

The setup expires if no trigger arrives within `ttl_bars` five-minute bars after the pullback bar,
or if the trend changes. A newer pullback bar replaces the trigger level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from wall2.config import SignalConfig
from wall2.core.types import Bar, Direction, Signal
from wall2.data.bars import BarAggregator
from wall2.data.indicators import EMA, SessionVWAP


@dataclass(frozen=True, slots=True)
class TrendUpdate:
    """A completed 15-min bar closed above (UP) or below (DOWN) VWAP.

    Emitted for every completed trend bar, so the direction lock can lift whenever a 15-min bar
    closes on the other side of VWAP. `changed` is True when the trend differs from before.
    """

    underlying: str
    direction: Direction
    ts: datetime
    changed: bool


@dataclass(slots=True)
class _Armed:
    direction: Direction
    level: float
    pullback_start: datetime
    bars_since: int = 0


class UnderlyingTracker:
    def __init__(self, symbol: str, cfg: SignalConfig) -> None:
        self.symbol = symbol
        self._cfg = cfg
        self.vwap = SessionVWAP()
        self.ema = EMA(cfg.ema_len)  # on entry-timeframe (5-min) closes
        self._agg_entry = BarAggregator(symbol, cfg.entry_minutes)
        self._agg_trend = BarAggregator(symbol, cfg.trend_minutes)
        self.confirmed_trend: Direction | None = None  # from completed 15-min bars
        self._provisional_trend: Direction | None = None  # before the first 15-min bar completes
        self._armed: _Armed | None = None
        self.last_minute: Bar | None = None
        self.last_entry_bar: Bar | None = None

    # ---- lifecycle -------------------------------------------------------------------------
    def warmup(self, entry_bars: list[Bar]) -> None:
        """Seed the EMA from prior sessions' 5-min bars (oldest first)."""
        for b in entry_bars:
            if b.minutes != self._cfg.entry_minutes:
                raise ValueError("warmup expects entry-timeframe bars")
            self.ema.update(b.close)

    def start_session(self) -> None:
        self.vwap.reset()
        self._agg_entry = BarAggregator(self.symbol, self._cfg.entry_minutes)
        self._agg_trend = BarAggregator(self.symbol, self._cfg.trend_minutes)
        self.confirmed_trend = None
        self._provisional_trend = None
        self._armed = None
        self.last_minute = None
        self.last_entry_bar = None

    # ---- state -----------------------------------------------------------------------------
    @property
    def trend(self) -> Direction | None:
        return self.confirmed_trend if self.confirmed_trend is not None else self._provisional_trend

    @property
    def armed_level(self) -> float | None:
        return self._armed.level if self._armed else None

    @property
    def last_price(self) -> float | None:
        return self.last_minute.close if self.last_minute else None

    # ---- events ----------------------------------------------------------------------------
    def on_minute(self, m: Bar) -> list[Signal | TrendUpdate]:
        out: list[Signal | TrendUpdate] = []
        self.last_minute = m
        self.vwap.update(m)
        vwap = self.vwap.value
        if self.confirmed_trend is None and vwap is not None:
            self._set_provisional(
                Direction.UP
                if m.close > vwap
                else Direction.DOWN
                if m.close < vwap
                else self._provisional_trend
            )

        sig = self._check_trigger(m)
        if sig is not None:
            out.append(sig)

        for b in self._agg_entry.on_minute(m):
            self._on_entry_bar(b)
        for b in self._agg_trend.on_minute(m):
            upd = self._on_trend_bar(b)
            if upd is not None:
                out.append(upd)
        return out

    def _set_provisional(self, d: Direction | None) -> None:
        if d != self._provisional_trend:
            self._provisional_trend = d
            self._armed = None  # trend changed: any setup is void

    def _check_trigger(self, m: Bar) -> Signal | None:
        a = self._armed
        if a is None or a.direction != self.trend:
            return None
        hit = m.close > a.level if a.direction is Direction.UP else m.close < a.level
        if not hit:
            return None
        self._armed = None
        side = "above" if a.direction is Direction.UP else "below"
        return Signal(
            underlying=self.symbol,
            direction=a.direction,
            ts=m.start,
            trigger_level=a.level,
            underlying_price=m.close,
            reason=f"1m close {m.close:.2f} {side} pullback bar "
            f"{a.pullback_start:%H:%M} level {a.level:.2f}",
        )

    def _on_entry_bar(self, b: Bar) -> None:
        self.last_entry_bar = b
        ema = self.ema.update(b.close)
        trend = self.trend
        if ema is None or trend is None:
            return
        touched = b.low <= ema if trend is Direction.UP else b.high >= ema
        if touched:
            level = b.high if trend is Direction.UP else b.low
            self._armed = _Armed(trend, level, b.start)
        elif self._armed is not None:
            self._armed.bars_since += 1
            if self._armed.bars_since >= self._cfg.setup_ttl_bars:
                self._armed = None

    def _on_trend_bar(self, b: Bar) -> TrendUpdate | None:
        vwap = self.vwap.value
        if vwap is None or b.close == vwap:
            return None
        d = Direction.UP if b.close > vwap else Direction.DOWN
        prev = self.trend
        self.confirmed_trend = d
        changed = prev is not None and d != prev
        if changed:
            self._armed = None
        return TrendUpdate(self.symbol, d, b.start, changed)
