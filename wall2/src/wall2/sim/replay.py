"""Drive the engine through a whole session on the simulator: 1-min bars per underlying, with a
synthetic option chain re-priced every minute. Used by tests and the `wall2 demo` command."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from wall2.broker.simulator import SimBroker
from wall2.core.clock import SimClock
from wall2.core.types import Bar
from wall2.engine import Engine
from wall2.sim.synthetic import synthetic_chain

STRIKE_STEP = {"SPY": 1.0, "QQQ": 1.0, "IWM": 1.0}


@dataclass(frozen=True, slots=True)
class SimDayResult:
    day: date
    exercised: list[str]  # ITM at expiry — must always be empty


async def run_sim_day(
    engine: Engine,
    broker: SimBroker,
    clock: SimClock,
    day: date,
    minutes: dict[str, list[Bar]],
    vol: float = 0.18,
    with_greeks: bool = True,
    pace_sec: float = 0.0,
    after_minute: Callable[[], Awaitable[None]] | None = None,
    stop: asyncio.Event | None = None,
) -> SimDayResult:
    plan = engine.calendar.plan(day)
    if plan is None:
        raise ValueError(f"{day} is not a trading session")

    def reprice(u: str, spot: float) -> None:
        for q in synthetic_chain(
            u,
            day,
            spot,
            clock.now(),
            plan.close,
            vol=vol,
            strike_step=STRIKE_STEP.get(u, 1.0),
            with_greeks=with_greeks,
        ):
            broker.set_quote(q)

    clock.set(plan.open - timedelta(minutes=30))
    for u, bars in minutes.items():
        reprice(u, bars[0].open)
    await engine.preopen(day)

    by_time: dict[datetime, dict[str, Bar]] = {}
    for u, bars in minutes.items():
        for b in bars:
            by_time.setdefault(b.start, {})[u] = b
    last: dict[str, float] = {}
    for start in sorted(by_time):
        row = by_time[start]
        clock.set(start + timedelta(minutes=1))
        for u, b in row.items():
            reprice(u, b.close)
            last[u] = b.close
        await engine.on_minute(row)
        if after_minute is not None:
            await after_minute()
        if stop is not None and stop.is_set():
            break
        if pace_sec:
            await asyncio.sleep(pace_sec)
    clock.set(max(clock.now(), plan.close))
    await engine.end_of_day()
    exercised = broker.expire(day, last)
    broker.settle_all()
    return SimDayResult(day, exercised)
