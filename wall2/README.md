# wall2

Automated 0DTE options bot for Webull: buys same-day calls/puts on SPY, QQQ and IWM on
trend pullbacks. Design: [`docs/wall2/DESIGN.md`](../docs/wall2/DESIGN.md). **All times are US Central.**

## Status

| Part | State |
|---|---|
| Calendar (CT, half-days, T+1), config | ✅ built, tested |
| Bars, VWAP, EMA, trend-pullback signals | ✅ built, tested |
| Contract selection (delta band, $20 fit, 0.10 floor, moneyness fallback, cheapest fit) | ✅ built, tested |
| Risk gate, settled-cash ledger | ✅ built, tested |
| Orders (limit at ask, re-price once, cancel/fill race), exits (trail, salvage, 14:50 ITM) | ✅ built, tested |
| Shadow log, SQLite store, daily HTML report | ✅ built, tested |
| Simulator broker + synthetic full-day replays | ✅ built, tested |
| **Webull adapter** | ⏳ M1, on your PC (`src/wall2/broker/webull.py` lists what to verify) |
| Trade charts (SVG, embedded in the report) | ✅ built, tested |
| PySide6 dashboard (charts, positions, journal, controls) | ✅ built; demo mode only until M1 |

## Setup (Windows or Linux)

```
cd wall2
uv sync                 # Python 3.12 + dependencies
uv run pytest           # tests
uv run wall2 check      # validate config, show today's session plan in CT
uv run wall2 demo       # one SYNTHETIC simulated session → data/demo/<date>.html

uv sync --extra ui      # dashboard dependencies (PySide6, pyqtgraph, qasync)
uv run wall2 ui --demo  # dashboard playing a SYNTHETIC session (--pace = seconds per minute)
```

`wall2 demo` runs the real engine against model (Black-Scholes) prices. It exercises the
machinery; it says nothing about whether the strategy makes money.

Copy `.env.example` to `.env` for Webull credentials (needed from M1). Never commit `.env`.

## Layout

```
src/wall2/
  app.py            CLI
  engine.py         signals → selection → risk → orders → exits → shadow
  config.py         typed config (config/wall2.yaml)
  core/             types, CT clock
  session/          NYSE calendar, session plan, settlement dates
  data/             bar building, VWAP, EMA
  signals/          trend-pullback setup state machine
  selection/        contract selector, delta fallback
  risk/             risk gate, settled-cash ledger
  execution/        order manager, exit rules
  broker/           Broker interface, simulator, Webull placeholder
  shadow/           skipped-signal tracking
  persistence/      SQLite store
  reports/          daily HTML report
  charts/           per-trade SVG charts
  ui/               PySide6 dashboard
  sim/              synthetic markets and day replays
```
