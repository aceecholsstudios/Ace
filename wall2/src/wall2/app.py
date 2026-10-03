"""Command line: `wall2 check`, `wall2 demo`, `wall2 report`, `wall2 run`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from wall2.broker.webull import WebullSettings
from wall2.config import Wall2Config, load_config
from wall2.core.clock import CT, RealClock
from wall2.persistence.db import Store
from wall2.reports.daily import write_report
from wall2.session.calendar import TradingCalendar
from wall2.sim.demo import build_demo
from wall2.sim.replay import run_sim_day

ROOT = Path(__file__).resolve().parents[2]


def _cfg(path: str | None) -> Wall2Config:
    return load_config(path or ROOT / "config" / "wall2.yaml")


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _cfg(args.config)
    cal = TradingCalendar(cfg.session)
    day = date.fromisoformat(args.date) if args.date else RealClock().now().date()
    print(
        f"Config OK · mode={cfg.mode} · underlyings={', '.join(cfg.underlyings)} "
        f"· budget ${cfg.selection.budget_usd}"
    )
    plan = cal.plan(day)
    if plan is None:
        print(f"{day}: no session (weekend or NYSE holiday). Next session: {cal.next_session(day)}")
    else:
        kind = "HALF-DAY" if plan.half_day else "full day"
        print(f"{day} ({kind}), all times CT:")
        for name, t in [
            ("open", plan.open),
            ("first entry", plan.first_entry),
            ("close-out (sell ITM)", plan.closeout),
            ("close", plan.close),
            ("report", plan.report_at),
        ]:
            print(f"  {name:<22}{t:%H:%M}")
        print(f"  options bought today settle {cal.settlement_date(day)}")
    s = WebullSettings()
    print(f"Webull credentials in .env: {'present' if s.present else 'missing (needed from M1)'}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """A synthetic simulated session through the real engine. Model prices, not market data."""
    cfg = _cfg(args.config)
    day = date.fromisoformat(args.date)
    out = Path(args.out)
    try:
        demo = build_demo(cfg, day, args.cash, out, args.drift)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    res = asyncio.run(
        run_sim_day(demo.engine, demo.broker, demo.clock, day, demo.minutes, vol=args.vol)
    )
    path = write_report(demo.store, day, out)
    trades = demo.store.rows("SELECT * FROM trades WHERE day = ?", day)
    net = sum((Decimal(t["pnl"]) for t in trades if t["pnl"]), Decimal(0))
    print(
        f"SYNTHETIC demo {day}: {len(trades)} trades, net {net:+.2f}, "
        f"exercised={res.exercised or 'none'}"
    )
    print(f"report: {path}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    try:
        from wall2.ui.run import run_demo_ui
    except ImportError as e:
        print(f"Dashboard needs the UI extra: `uv sync --extra ui` ({e})", file=sys.stderr)
        return 2
    if not args.demo:
        print(
            "The live dashboard needs the Webull adapter (M1). Use `wall2 ui --demo`.",
            file=sys.stderr,
        )
        return 2
    return run_demo_ui(
        _cfg(args.config),
        date.fromisoformat(args.date),
        Path(args.out),
        args.drift,
        args.pace,
        Path(args.snap) if args.snap else None,
        args.snap_after,
    )


def cmd_report(args: argparse.Namespace) -> int:
    store = Store(args.db)
    day = date.fromisoformat(args.date) if args.date else datetime.now(CT).date()
    print(write_report(store, day, args.out))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    print(
        "Live/paper trading needs the Webull adapter (milestone M1, on your PC). "
        "Use `wall2 demo` to exercise the engine on synthetic data.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wall2", description="0DTE trend-pullback bot (times in CT)")
    ap.add_argument("--config", help="path to wall2.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate config and show a day's session plan")
    c.add_argument("--date")
    c.set_defaults(fn=cmd_check)
    d = sub.add_parser("demo", help="run one SYNTHETIC simulated session and write a report")
    d.add_argument("--date", default="2026-10-02")
    d.add_argument("--drift", type=float, default=0.02, help="trend per minute (SPY dollars)")
    d.add_argument("--vol", type=float, default=0.18, help="synthetic implied volatility")
    d.add_argument("--cash", default="5000")
    d.add_argument("--out", default="data/demo")
    d.set_defaults(fn=cmd_demo)
    r = sub.add_parser("report", help="write the daily HTML report from the database")
    r.add_argument("--date")
    r.add_argument("--db", default="data/wall2.db")
    r.add_argument("--out", default="reports")
    r.set_defaults(fn=cmd_report)
    ui = sub.add_parser("ui", help="open the dashboard (today: --demo plays a SYNTHETIC session)")
    ui.add_argument("--demo", action="store_true")
    ui.add_argument("--date", default="2026-10-02")
    ui.add_argument("--drift", type=float, default=0.02)
    ui.add_argument("--pace", type=float, default=0.25, help="seconds per simulated minute")
    ui.add_argument("--out", default="data/demo-ui")
    ui.add_argument("--snap", help=argparse.SUPPRESS)  # save a screenshot and exit (testing)
    ui.add_argument("--snap-after", type=int, default=120, help=argparse.SUPPRESS)
    ui.set_defaults(fn=cmd_ui)
    sub.add_parser("run", help="trade (paper/live) — available after M1").set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
