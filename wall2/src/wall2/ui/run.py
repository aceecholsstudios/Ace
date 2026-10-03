"""Start the dashboard. Today only the SYNTHETIC demo can drive it; the live data feed arrives
with the Webull adapter (M1)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from datetime import date
from pathlib import Path

import qasync
from PySide6 import QtWidgets

from wall2.config import Wall2Config
from wall2.reports.daily import write_report
from wall2.sim.demo import build_demo
from wall2.sim.replay import run_sim_day
from wall2.ui.dashboard import MainWindow, spawner


def run_demo_ui(
    cfg: Wall2Config,
    day: date,
    out: Path,
    drift: float,
    pace_sec: float,
    snap_path: Path | None = None,
    snap_after_min: int = 0,
) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    demo = build_demo(cfg, day, "5000", out, drift)
    win = MainWindow(demo.engine, spawner(loop))
    win.show()
    done = asyncio.Event()
    count = 0

    async def after_minute() -> None:
        nonlocal count
        count += 1
        if snap_path is not None and count == snap_after_min:
            win.refresh()
            if tab := os.environ.get("WALL2_SNAP_TAB"):
                win.tabs.setCurrentIndex(list(win.charts).index(tab))
            cw = win.centralWidget()
            if cw is not None and (lay := cw.layout()) is not None:
                lay.activate()
            win.grab().save(str(snap_path))
            done.set()

    async def drive() -> None:
        await run_sim_day(
            demo.engine,
            demo.broker,
            demo.clock,
            day,
            demo.minutes,
            pace_sec=pace_sec,
            after_minute=after_minute,
            stop=done,
        )
        write_report(demo.store, day, out)
        win.refresh()
        if snap_path is not None:
            done.set()

    app.aboutToQuit.connect(done.set)
    task = loop.create_task(drive())
    loop.run_until_complete(done.wait())  # snapshot taken, or the window was closed
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(task)
    win.close()
    return 0
