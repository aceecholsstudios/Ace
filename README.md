# Ace

An order-flow scalping bot for CME micro equity index futures (MES, MNQ, M2K).
It reads order flow from Databento, places orders through Tradovate, and runs on
a home Windows PC with a PySide6 dashboard.

- Design: [`docs/DESIGN.md`](docs/DESIGN.md)
- Research notes: [`docs/RESEARCH.md`](docs/RESEARCH.md)

**Status:** milestone M0 (skeleton, tooling, CI, config models, `.env`
handling). The bot does not connect to anything or trade yet. See Section 16
of the design doc for the milestone plan.

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 if needed.

```sh
uv sync                        # create .venv with runtime + dev dependencies
cp .env.example .env           # then fill in your credentials
uv run pre-commit install      # lint, type-check and secret scan on commit
uv run ace doctor              # check config, credentials and host settings
```

On Windows, use `copy .env.example .env` instead of `cp`.

## Commands

| Command | What it does |
|---|---|
| `ace doctor` | Validates `config/ace.yaml`, `config/fees.yaml` and `.env`; checks `data/` is writable and clock drift is under 250 ms; on Windows, checks sleep, hibernate, time sync and active hours. Add `--offline` to skip the NTP check. Exits non-zero on any FAIL. |
| `ace run` | Engine and dashboard. Not implemented yet (M1-M6). |
| `ace report` | Reports. Not implemented yet (M6). |

## Development

```sh
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy                    # strict type-check
uv run pytest                  # tests
```

CI runs the same four checks on Linux and Windows on every push.

## Configuration

- `config/ace.yaml`: every tunable. Unknown keys and out-of-range values are
  rejected at startup. All numbers are starting points to calibrate on demo.
- `config/fees.yaml`: per-side fees. The shipped values are **estimates**;
  copy the real ones from your Tradovate statement and set `verified: true`.
- `.env`: credentials. Git-ignored, and a pre-commit hook blocks any file that
  assigns a value to a credential variable.
