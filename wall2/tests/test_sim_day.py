"""End-to-end: full simulated sessions through the real engine and the simulator broker."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import ct

from wall2.broker.simulator import SimBroker
from wall2.config import OrderConfig, Wall2Config
from wall2.core.clock import SimClock
from wall2.core.types import Bar
from wall2.engine import Engine
from wall2.persistence.db import Store
from wall2.reports.daily import build_report
from wall2.session.calendar import TradingCalendar
from wall2.sim.replay import run_sim_day
from wall2.sim.synthetic import scripted_path

DAY = date(2026, 10, 2)
FAST = OrderConfig(
    fill_wait_sec=0.001, profit_exit_mid_wait_sec=0.001, cancel_confirm_timeout_sec=0.05
)


def make(cash: str = "5000", **cfg_over):
    cfg = Wall2Config(orders=FAST, **cfg_over)
    clock = SimClock(ct(7, 0, DAY))
    broker = SimBroker(Decimal(cash), cfg.fees_per_contract)
    store = Store(":memory:")
    eng = Engine(cfg, broker, store, clock, TradingCalendar(cfg.session))
    return cfg, clock, broker, store, eng


def paths(drift: float, start: dict[str, float]) -> dict[str, list[Bar]]:
    out = {}
    for u, p0 in start.items():
        out[u] = list(scripted_path(u, DAY, ct(8, 30), 390, p0, drift * p0 / 600, 0.0012 * p0, 35))
    return out


def warm(eng: Engine, start: dict[str, float]) -> None:
    for u, p0 in start.items():
        eng.warmup(
            u,
            [
                Bar(u, ct(14, 0, DAY) - timedelta(days=1, minutes=5 * i), 5, p0, p0, p0, p0, 1)
                for i in range(20, 0, -1)
            ],
        )


START = {"SPY": 600.0, "QQQ": 520.0, "IWM": 220.0}


@pytest.mark.parametrize("drift", [0.02, -0.02, 0.0])
async def test_full_day_invariants(drift: float) -> None:
    cfg, clock, broker, store, eng = make()
    warm(eng, START)
    res = await run_sim_day(eng, broker, clock, DAY, paths(drift, START))

    trades = store.rows("SELECT * FROM trades")
    signals = store.rows("SELECT * FROM signals")
    assert res.exercised == []  # nothing was left in the money at expiry
    assert len(trades) <= cfg.risk.max_trades_per_day
    assert all(t["exit_ts"] is not None for t in trades)  # every trade closed or expired
    assert all(s["outcome"] in ("traded", "skipped") for s in signals)
    for t in trades:
        assert Decimal(t["entry_price"]) * 100 * t["qty"] <= cfg.selection.budget_usd
        assert t["entry_ts"] >= ct(8, 35).isoformat()
    # One position per ETF at a time: no overlapping trades on the same underlying.
    by_u: dict[str, list] = {}
    for t in trades:
        by_u.setdefault(t["underlying"], []).append((t["entry_ts"], t["exit_ts"]))
    for spans in by_u.values():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert await broker.settled_cash() >= 0
    html = build_report(store, DAY)
    assert "wall2 daily report" in html and "Skipped signals" in html


async def test_trending_day_trades_and_shadows() -> None:
    cfg, clock, broker, store, eng = make()
    warm(eng, START)
    await run_sim_day(eng, broker, clock, DAY, paths(0.02, START))
    trades = store.rows("SELECT * FROM trades")
    assert len(trades) >= 1
    skipped = store.rows("SELECT * FROM signals WHERE outcome='skipped'")
    if skipped:  # skipped signals with a would-be contract get a shadow result
        with_contract = [s for s in skipped if s["contract"]]
        shadows = store.rows("SELECT * FROM shadow WHERE exit_reason IS NOT NULL")
        assert len(shadows) == len(with_contract)


async def test_no_settled_cash_means_no_trades() -> None:
    cfg, clock, broker, store, eng = make(cash="10")
    warm(eng, START)
    await run_sim_day(eng, broker, clock, DAY, paths(0.02, START))
    assert store.rows("SELECT * FROM trades") == []
    reasons = {r["reject_reason"] for r in store.rows("SELECT reject_reason FROM signals")}
    assert any(r and r.startswith("not enough settled cash") for r in reasons)


async def test_moneyness_fallback_day_runs() -> None:
    cfg, clock, broker, store, eng = make()
    warm(eng, START)
    res = await run_sim_day(eng, broker, clock, DAY, paths(0.02, START), with_greeks=False)
    assert res.exercised == []
    srcs = {
        r["delta_source"]
        for r in store.rows("SELECT delta_source FROM signals WHERE contract IS NOT NULL")
    }
    assert srcs <= {"moneyness"}


async def test_skip_reasons_are_not_mixed() -> None:
    cfg, clock, broker, store, eng = make()
    warm(eng, START)
    await run_sim_day(eng, broker, clock, DAY, paths(0.02, START))
    for r in store.rows("SELECT reject_reason FROM signals WHERE outcome='skipped'"):
        reason = r["reject_reason"]
        assert reason.startswith("no contract fits") or ":" not in reason, reason


async def test_trade_charts_written_and_embedded(tmp_path) -> None:
    cfg = Wall2Config(orders=FAST)
    clock = SimClock(ct(7, 0, DAY))
    broker = SimBroker(Decimal("5000"))
    store = Store(":memory:")
    eng = Engine(cfg, broker, store, clock, TradingCalendar(cfg.session), shots_dir=tmp_path)
    warm(eng, START)
    await run_sim_day(eng, broker, clock, DAY, paths(0.02, START))
    shots = [r["screenshot"] for r in store.rows("SELECT screenshot FROM trades")]
    assert shots and all(s and s.endswith(".svg") for s in shots)
    html = build_report(store, DAY)
    assert html.count("<svg") == len(shots)
    snap = eng.snapshot()
    assert snap.trades_today == len(shots) and len(snap.underlyings) == 3
