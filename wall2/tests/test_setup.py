"""Trend-pullback setup: 15-min trend (VWAP) → 5-min touch of the 9 EMA → 1-min trigger."""

from datetime import timedelta

from conftest import ct

from wall2.config import SignalConfig
from wall2.core.types import Bar, Direction, Signal
from wall2.signals.setup import TrendUpdate, UnderlyingTracker


def warm(t: UnderlyingTracker, price: float) -> None:
    t.warmup(
        [
            Bar(
                "SPY",
                ct(14, 0) - timedelta(days=1, minutes=5 * i),
                5,
                price,
                price,
                price,
                price,
                1,
            )
            for i in range(10, 0, -1)
        ]
    )
    t.start_session()


def feed(t: UnderlyingTracker, start_min: int, bars: list[tuple[float, float, float, float]]):
    out = []
    for i, (o, h, lo, c) in enumerate(bars):
        m = ct(8, 30) + timedelta(minutes=start_min + i)
        out += t.on_minute(Bar("SPY", m, 1, o, h, lo, c, 1000))
    return out


def up_session(t: UnderlyingTracker) -> None:
    # 8:30–8:34 rising, closes on highs → price above VWAP, provisional trend UP.
    feed(
        t,
        0,
        [
            (101.0, 101.2, 100.9, 101.2),
            (101.2, 101.4, 101.1, 101.4),
            (101.4, 101.6, 101.3, 101.6),
            (101.6, 101.8, 101.5, 101.8),
            (101.8, 102.0, 101.7, 102.0),
        ],
    )


def test_signal_on_1m_close_above_pullback_bar_high() -> None:
    t = UnderlyingTracker("SPY", SignalConfig())
    warm(t, 100.0)
    up_session(t)
    assert t.trend is Direction.UP
    # 8:35–8:39 pullback bar: long lower wick down through the 9 EMA, closes stay above VWAP.
    feed(t, 5, [(102.0, 102.1, 100.2, 101.9)] + [(101.9, 102.0, 101.8, 101.9)] * 4)
    assert t.armed_level == 102.1
    out = feed(t, 10, [(101.9, 102.05, 101.85, 102.0), (102.0, 102.3, 101.95, 102.2)])
    sigs = [e for e in out if isinstance(e, Signal)]
    assert len(sigs) == 1 and sigs[0].direction is Direction.UP
    assert sigs[0].ts == ct(8, 41) and sigs[0].trigger_level == 102.1
    assert t.armed_level is None  # one signal per setup


def test_setup_expires_after_two_entry_bars() -> None:
    t = UnderlyingTracker("SPY", SignalConfig())
    warm(t, 100.0)
    up_session(t)
    feed(t, 5, [(102.0, 102.1, 100.2, 101.9)] + [(101.9, 102.0, 101.8, 101.9)] * 4)
    assert t.armed_level == 102.1
    # Two more 5-min bars without a close above 102.1 and without touching the EMA again.
    quiet = [(101.9, 102.05, 101.6, 101.95)] * 10
    out = feed(t, 10, quiet)
    assert t.armed_level is None and not [e for e in out if isinstance(e, Signal)]


def test_no_setup_before_ema_is_warm() -> None:
    t = UnderlyingTracker("SPY", SignalConfig())
    t.start_session()
    up_session(t)
    feed(t, 5, [(102.0, 102.1, 99.0, 101.9)] * 5)
    assert t.armed_level is None


def test_15_min_close_below_vwap_turns_trend_down_and_disarms() -> None:
    t = UnderlyingTracker("SPY", SignalConfig())
    warm(t, 100.0)
    up_session(t)
    feed(t, 5, [(102.0, 102.1, 100.2, 101.9)] + [(101.9, 102.0, 101.8, 101.9)] * 4)
    # 8:40–8:44: collapse; the 15-min bar (8:30–8:44) closes far below VWAP.
    out = feed(t, 10, [(101.0, 101.0, 99.0, 99.0)] * 5)
    upd = [e for e in out if isinstance(e, TrendUpdate)]
    assert upd and upd[-1].direction is Direction.DOWN and upd[-1].ts == ct(8, 30)
    assert t.trend is Direction.DOWN
    # The call setup (level 102.1) is gone; the collapse bar itself is a new put pullback (its low).
    assert t.armed_level == 99.0
