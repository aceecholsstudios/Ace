"""Build a SYNTHETIC demo session (simulator + scripted prices) for `wall2 demo` and `wall2 ui`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from wall2.broker.simulator import SimBroker
from wall2.config import Wall2Config
from wall2.core.clock import SimClock
from wall2.core.types import Bar
from wall2.engine import Engine
from wall2.persistence.db import Store
from wall2.session.calendar import TradingCalendar
from wall2.sim.synthetic import scripted_path

START_PRICES = {"SPY": 600.0, "QQQ": 520.0, "IWM": 220.0}


@dataclass(slots=True)
class Demo:
    engine: Engine
    broker: SimBroker
    clock: SimClock
    store: Store
    day: date
    minutes: dict[str, list[Bar]]


def build_demo(cfg: Wall2Config, day: date, cash: str, out: Path, drift: float) -> Demo:
    cal = TradingCalendar(cfg.session)
    plan = cal.plan(day)
    if plan is None:
        raise ValueError(f"{day} is not a trading session")
    out.mkdir(parents=True, exist_ok=True)
    store = Store(out / "demo.db")
    clock = SimClock(plan.open - timedelta(hours=1))
    broker = SimBroker(Decimal(cash), cfg.fees_per_contract)
    eng = Engine(cfg, broker, store, clock, cal, shots_dir=out / "shots")
    n = int((plan.close - plan.open).total_seconds() // 60)
    minutes: dict[str, list[Bar]] = {}
    for u in cfg.underlyings:
        p0 = START_PRICES[u]
        prior = plan.open - timedelta(days=1)
        eng.warmup(
            u, [Bar(u, prior + timedelta(minutes=5 * i), 5, p0, p0, p0, p0, 1) for i in range(20)]
        )
        minutes[u] = list(
            scripted_path(u, day, plan.open, n, p0, drift * p0 / 600, 0.0012 * p0, 35)
        )
    return Demo(eng, broker, clock, store, day, minutes)
