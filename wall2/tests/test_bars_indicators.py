import pytest
from conftest import bar, ct

from wall2.data.bars import BarAggregator, MinuteBarBuilder
from wall2.data.indicators import EMA, SessionVWAP


def test_five_minute_bars_align_to_clock() -> None:
    agg = BarAggregator("SPY", 5)
    out = []
    for i, m in enumerate(range(30, 40)):
        out += agg.on_minute(bar("SPY", 8, m, 100 + i, 101 + i, 99 + i, 100.5 + i))
    assert [b.start for b in out] == [ct(8, 30), ct(8, 35)]
    first = out[0]
    assert (first.open, first.high, first.low, first.close) == (100, 105, 99, 104.5)
    assert first.volume == 5000


def test_missing_minutes_still_emit_previous_bucket() -> None:
    agg = BarAggregator("SPY", 5)
    assert agg.on_minute(bar("SPY", 8, 30, 1, 1, 1, 1)) == []
    out = agg.on_minute(bar("SPY", 8, 36, 2, 2, 2, 2))  # 8:31–8:35 missing
    assert [b.start for b in out] == [ct(8, 30)]


def test_minute_builder_from_trades() -> None:
    b = MinuteBarBuilder("SPY")
    assert b.on_trade(ct(8, 30).replace(second=1), 100, 5) is None
    assert b.on_trade(ct(8, 30).replace(second=30), 101, 5) is None
    done = b.on_trade(ct(8, 31).replace(second=2), 100.5, 1)
    assert done is not None and (done.high, done.low, done.close, done.volume) == (
        101,
        100,
        101,
        10,
    )
    assert b.flush(ct(8, 32)) is not None


def test_ema_seeds_with_sma_then_smooths() -> None:
    e = EMA(3)
    assert e.update(1) is None and e.update(2) is None
    assert e.update(3) == pytest.approx(2.0)
    assert e.update(4) == pytest.approx(3.0)  # 2 + 0.5*(4-2)


def test_vwap_volume_weighted() -> None:
    v = SessionVWAP()
    v.update(bar("SPY", 8, 30, 10, 10, 10, 10, v=100))
    v.update(bar("SPY", 8, 31, 20, 20, 20, 20, v=300))
    assert v.value == pytest.approx(17.5)
    v.reset()
    assert v.value is None
