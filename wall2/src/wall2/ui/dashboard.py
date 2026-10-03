"""wall2 dashboard (PySide6). Runs in the engine's process and reads `Engine.snapshot()` on a
timer; it never blocks the event loop and never redraws per tick. Controls go through the same
engine methods the bot uses (pause, resume, close all, ETF toggles, budget)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from wall2.core.types import Direction
from wall2.engine import Engine, Snapshot
from wall2.ui.chart import BAR_SECONDS, CandlestickItem, CTAxis

pg.setConfigOptions(antialias=True, background="w", foreground="#333")

TREND_TEXT = {Direction.UP: "▲ UP (calls)", Direction.DOWN: "▼ DOWN (puts)", None: "— no trend"}


class UnderlyingChart(QtWidgets.QWidget):
    def __init__(self, symbol: str) -> None:
        super().__init__()
        self.symbol = symbol
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet("font-weight:600;")
        lay.addWidget(self.status)
        self.plot = pg.PlotWidget(axisItems={"bottom": CTAxis(orientation="bottom")})
        self.plot.showGrid(x=False, y=True, alpha=0.2)
        self.plot.addLegend(offset=(8, 8))
        self.candles = CandlestickItem()
        self.plot.addItem(self.candles)
        self.vwap = self.plot.plot(pen=pg.mkPen("#7a5af8", width=2), name="VWAP")
        self.ema = self.plot.plot(pen=pg.mkPen("#e8a33d", width=2), name="EMA 9 (5m)")
        self.trigger = pg.InfiniteLine(
            angle=0, pen=pg.mkPen("#6e6e73", style=QtCore.Qt.PenStyle.DashLine)
        )
        self.trigger.hide()
        self.plot.addItem(self.trigger)
        self.entries = pg.ScatterPlotItem(symbol="t1", size=14, brush="#0b6bcb", name="buy")
        self.exits = pg.ScatterPlotItem(symbol="t", size=14, brush="#111", name="sell")
        self.plot.addItem(self.entries)
        self.plot.addItem(self.exits)
        lay.addWidget(self.plot)

    def refresh(self, engine: Engine, snap: Snapshot) -> None:
        uv = next(u for u in snap.underlyings if u.symbol == self.symbol)
        price = f"{uv.price:.2f}" if uv.price is not None else "—"
        vwap = f"{uv.vwap:.2f}" if uv.vwap is not None else "—"
        locked = {Direction.UP: "calls", Direction.DOWN: "puts"}
        lock = f" · {locked[uv.direction_lock]} only until trend flips" if uv.direction_lock else ""
        armed = (
            f" · setup armed, trigger {uv.armed_level:.2f}" if uv.armed_level is not None else ""
        )
        flags = "" if uv.enabled else " · DISABLED"
        flags += " · HALTED" if uv.halted else ""
        self.status.setText(
            f"{self.symbol} {price}   VWAP {vwap}   {TREND_TEXT[uv.trend]}{armed}{lock}{flags}"
        )

        bars = engine.history.get(self.symbol, [])
        self.candles.set_bars(bars)
        xs = [b.start.timestamp() + BAR_SECONDS / 2 for b in bars]
        self.vwap.setData(
            [x for x, b in zip(xs, bars, strict=True) if b.vwap is not None],
            [b.vwap for b in bars if b.vwap is not None],
        )
        self.ema.setData(
            [x for x, b in zip(xs, bars, strict=True) if b.ema is not None],
            [b.ema for b in bars if b.ema is not None],
        )
        if uv.armed_level is not None:
            self.trigger.setValue(uv.armed_level)
            self.trigger.show()
        else:
            self.trigger.hide()
        if snap.plan is not None:
            rows = engine.store.rows(
                "SELECT entry_ts, entry_spot, exit_ts, exit_spot FROM trades "
                "WHERE day = ? AND underlying = ?",
                snap.plan.day,
                self.symbol,
            )
            from datetime import datetime as _dt

            ent = [
                (_dt.fromisoformat(r["entry_ts"]).timestamp(), r["entry_spot"])
                for r in rows
                if r["entry_spot"]
            ]
            ext = [
                (_dt.fromisoformat(r["exit_ts"]).timestamp(), r["exit_spot"])
                for r in rows
                if r["exit_ts"] and r["exit_spot"]
            ]
            self.entries.setData([e[0] for e in ent], [e[1] for e in ent])
            self.exits.setData([e[0] for e in ext], [e[1] for e in ext])


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, engine: Engine, spawn: Callable[[Any], None]) -> None:
        super().__init__()
        self.engine = engine
        self._spawn = spawn  # schedules a coroutine on the event loop
        self.setWindowTitle("wall2 · 0DTE trend pullback (times CT)")
        self.resize(1280, 860)
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        # Header -----------------------------------------------------------------------------
        head = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QLabel()
        self.clock = QtWidgets.QLabel()
        self.trades = QtWidgets.QLabel()
        self.cash = QtWidgets.QLabel()
        for w in (self.mode, self.clock, self.trades, self.cash):
            w.setStyleSheet("font-size:14px; padding:2px 10px;")
            head.addWidget(w)
        head.addStretch(1)
        head.addWidget(QtWidgets.QLabel("Budget $"))
        self.budget = QtWidgets.QDoubleSpinBox()
        self.budget.setRange(1, 1000)
        self.budget.setDecimals(0)
        self.budget.setValue(float(engine.budget))
        self.budget.editingFinished.connect(self._budget_changed)
        head.addWidget(self.budget)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.clicked.connect(self._toggle_pause)
        head.addWidget(self.pause_btn)
        close_btn = QtWidgets.QPushButton("Close all")
        close_btn.setStyleSheet("color:#c62828; font-weight:600;")
        close_btn.clicked.connect(self._close_all)
        head.addWidget(close_btn)
        root.addLayout(head)

        # Middle: charts | positions + toggles ---------------------------------------------
        mid = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.tabs = QtWidgets.QTabWidget()
        self.charts = {u: UnderlyingChart(u) for u in engine.trackers}
        for u, c in self.charts.items():
            self.tabs.addTab(c, u)
        mid.addWidget(self.tabs)
        side = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(side)
        sl.addWidget(QtWidgets.QLabel("<b>Open positions</b>"))
        self.pos_table = QtWidgets.QTableWidget(0, 6)
        self.pos_table.setHorizontalHeaderLabels(
            ["Contract", "Qty", "Entry", "Bid", "Gain", "Exit rule"]
        )
        _fit_columns(self.pos_table)
        self.pos_table.verticalHeader().hide()
        sl.addWidget(self.pos_table)
        sl.addWidget(QtWidgets.QLabel("<b>ETFs</b>"))
        self.toggles: dict[str, QtWidgets.QCheckBox] = {}
        for u in engine.trackers:
            cb = QtWidgets.QCheckBox(u)
            cb.setChecked(True)
            cb.toggled.connect(lambda on, sym=u: self.engine.set_etf_enabled(sym, on))
            self.toggles[u] = cb
            sl.addWidget(cb)
        sl.addStretch(1)
        mid.addWidget(side)
        mid.setSizes([880, 400])
        root.addWidget(mid, 3)

        # Journal ------------------------------------------------------------------------------
        root.addWidget(QtWidgets.QLabel("<b>Today's signals</b> (traded and skipped)"))
        self.journal = QtWidgets.QTableWidget(0, 7)
        self.journal.setHorizontalHeaderLabels(
            ["Time", "ETF", "Dir", "Outcome", "Contract", "Δ", "Reason skipped / result"]
        )
        _fit_columns(self.journal)
        self.journal.verticalHeader().hide()
        root.addWidget(self.journal, 2)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    # ---- controls ----------------------------------------------------------------------------
    def _toggle_pause(self) -> None:
        if self.engine.day.paused:
            self.engine.resume()
        else:
            self.engine.pause()
        self.refresh()

    def _close_all(self) -> None:
        if not self.engine.positions:
            return
        if (
            QtWidgets.QMessageBox.question(self, "Close all", "Sell every open option at the bid?")
            == QtWidgets.QMessageBox.StandardButton.Yes
        ):
            self._spawn(self.engine.close_all())

    def _budget_changed(self) -> None:
        new = Decimal(str(int(self.budget.value())))
        if new != self.engine.budget:
            self.engine.set_budget(new)

    # ---- refresh -----------------------------------------------------------------------------
    def refresh(self) -> None:
        snap = self.engine.snapshot()
        badge = "LIVE" if snap.mode == "live" else "PAPER"
        color = "#c62828" if snap.mode == "live" else "#0b6bcb"
        self.mode.setText(
            f'<span style="color:white;background:{color};padding:2px 6px;">{badge}</span>'
        )
        phase = ""
        if snap.plan is not None:
            if snap.now < snap.plan.first_entry:
                phase = "pre-open"
            elif snap.now < snap.plan.closeout:
                phase = "trading"
            elif snap.now < snap.plan.close:
                phase = "close-out (sell ITM)"
            else:
                phase = "closed"
        self.clock.setText(
            f"{snap.now:%a %H:%M} CT · {phase}" + (" · PAUSED" if snap.paused else "")
        )
        self.trades.setText(f"Trades {snap.trades_today}/{snap.max_trades}")
        self.cash.setText(f"Settled ${snap.settled:,.2f} · Unsettled ${snap.unsettled:,.2f}")
        self.pause_btn.setText("Resume" if snap.paused else "Pause")
        for u, cb in self.toggles.items():
            uv = next(x for x in snap.underlyings if x.symbol == u)
            if cb.isChecked() != uv.enabled:
                cb.blockSignals(True)
                cb.setChecked(uv.enabled)
                cb.blockSignals(False)

        self.pos_table.setRowCount(len(snap.positions))
        for i, p in enumerate(snap.positions):
            gain = f"{p.gain_pct:+.0f}%" if p.gain_pct is not None else "—"
            rule = "1-min close vs EMA" if p.tightened else "5-min close vs EMA"
            vals = [
                f"{p.underlying} {p.strike} {p.right}" + (" (adopted)" if p.adopted else ""),
                str(p.qty),
                f"{p.entry:.2f}",
                f"{p.bid:.2f}" if p.bid is not None else "—",
                gain,
                rule,
            ]
            for j, v in enumerate(vals):
                self.pos_table.setItem(i, j, QtWidgets.QTableWidgetItem(v))

        rows = self.engine.store.rows(
            "SELECT s.ts, s.underlying, s.direction, s.outcome, s.contract, s.delta, "
            "s.reject_reason, t.pnl, t.exit_reason FROM signals s "
            "LEFT JOIN trades t ON t.signal_id = s.id WHERE s.day = ? ORDER BY s.ts DESC",
            snap.plan.day if snap.plan else snap.now.date(),
        )
        self.journal.setRowCount(len(rows))
        for i, r in enumerate(rows):
            if r["outcome"] == "traded":
                result = f"P&L {r['pnl']} · {r['exit_reason']}" if r["pnl"] is not None else "open"
            else:
                result = r["reject_reason"] or ""
            vals = [
                r["ts"][11:16],
                r["underlying"],
                r["direction"],
                r["outcome"],
                r["contract"] or "—",
                f"{r['delta']:.2f}" if r["delta"] is not None else "—",
                result,
            ]
            for j, v in enumerate(vals):
                self.journal.setItem(i, j, QtWidgets.QTableWidgetItem(v))

        for c in self.charts.values():
            c.refresh(self.engine, snap)


def _fit_columns(table: QtWidgets.QTableWidget) -> None:
    """Size columns to their contents; the last one takes the remaining width."""
    h = table.horizontalHeader()
    h.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
    h.setStretchLastSection(True)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)


def spawner(loop: asyncio.AbstractEventLoop) -> Callable[[Any], None]:
    tasks: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> None:
        t = loop.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    return spawn
