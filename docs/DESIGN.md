# Ace: Design Document for an Order-Flow Scalping Bot on Tradovate Micros

| | |
|---|---|
| **Status** | Draft v0.2. Rewritten from the requirements Q&A; supersedes v0.1 |
| **Author** | Ace Echols Studios |
| **Last updated** | 2026-09-29 |
| **Broker / API** | Tradovate (REST + WebSocket), market data and orders |
| **Instruments** | MES (Micro S&P 500), MNQ (Micro Nasdaq-100), M2K (Micro Russell 2000) |
| **Account** | Personal cash account, under $5k |
| **Host** | Home PC, Windows 11, with a PySide6 desktop dashboard |

---

## 1. Summary

Ace is a Python program that runs on a home Windows PC. It scalps CME micro
equity index futures through the Tradovate API, reading **order flow**: delta
divergence, absorption, and aggressive sweeps. Trades last about 1–10 minutes.
It trades three windows (Asia, London, and US regular hours), holds **one
position at a time** across the three symbols, and is always flat outside those
windows.

All decisions happen inside the bot: signal detection, scoring, symbol
selection, sizing, risk tiers, and trade management. Tradovate supplies
market data (ticks, quotes, DOM) and executes orders. It also holds each
trade's protective stop on its servers, so a stalled or crashed bot never
leaves a position unprotected.

A PySide6 desktop window, running in the same process as the engine, shows
live charts, the signal scoreboard, risk state, and the trade journal. It also
provides the controls: pause, flatten, symbol toggles, and live parameter
edits.

### 1.1 Decision record (from the requirements Q&A)

| Area | Decision |
|---|---|
| Goal | Income generation |
| Account / capital | Personal Tradovate cash account, under $5k |
| Commission plan | Tradovate Free plan (pay per trade) |
| Instruments | MES, MNQ, M2K (micros only) |
| Trading windows | Asia 19:30–22:00 ET · London 02:30–05:00 ET · RTH 09:30–16:00 ET |
| Window start | Wait 2–5 minutes after each window opens |
| Window end | Flatten any open trade when the window closes |
| Holding period | 1–10 minutes typical, **no** hard time stop |
| Strategy style | Order flow / tape reading |
| Signals | Delta divergence, absorption, aggressive sweeps |
| Signal combination | Weighted score, **equal weights** to start |
| Sweep definition | Any of: levels cleared in time, one-sided volume burst, large prints |
| Absorption definition | Any of: volume at price without a break, DOM refill, delta without price progress (strongest wins) |
| Divergence span | Multi-scale: 1-min and 5-min swings; higher score when both diverge |
| Aggressor inference | Quote rule (at or above ask = buy, at or below bid = sell; midpoint split) |
| DOM | Used for signals in v1 |
| Data feed | Tradovate only, behind a data-source interface so it can be replaced later |
| Threshold tuning | Rolling percentiles, same window, last 5 trading days |
| Entry order | Market |
| Stop placement | Just beyond the flow level that triggered the trade, plus a buffer |
| Stop too wide for budget | Re-score on a cheaper-tick symbol (MNQ/M2K); otherwise skip |
| Profit target | Next flow level (HVN, prior absorption zone, large DOM size, session high/low, VWAP); if none, trail only |
| Trade management | Breakeven, then flow-based trailing |
| Risk per trade | $25–$50 fixed |
| Bad-day handling | Half size after 2 consecutive losses; stop for the window after 4 losses on the day |
| Longer-term limits | Stop at 10% below equity peak (manual review); per-window edge-decay check |
| Trade cap | 15 per day |
| After a trade | 5-minute pause after a loss only |
| Concurrency | One position at a time across all symbols |
| Symbol selection | Best score: signal strength + room to target + recent symbol results |
| News | Ignored (no calendar); spread and liquidity checks still apply |
| Disconnect mid-trade | Rely on Tradovate-held stop and target; reconcile on reconnect |
| Stray position at startup | Adopt it and attach a protective stop |
| Contract roll | Automatic, when the next contract's volume overtakes the front month |
| Storage | SQLite (trades and decisions), 1-min bars plus flow stats, trade screenshots |
| Alerts | Dashboard only |
| Dashboard | PySide6, same process as the engine |
| Dashboard panels | Price and flow chart, signal scoreboard, position and risk, trade journal |
| Dashboard controls | Pause/resume, flatten now, symbol toggles, live parameter edits |
| Windows updates / sleep | Active hours set, sleep disabled |
| Credentials | `.env` file, git-ignored |
| Validation | Forward test on Tradovate demo; go live on manual sign-off |
| Engineering rigor | Production-grade: mypy strict, unit and integration tests, fake Tradovate server, CI |

---

## 2. Goals and non-goals

### Goals
- Scalp MES, MNQ and M2K with order-flow signals during the three active
  windows, one trade at a time.
- Keep each loss small and fixed ($25–$50), and slow down automatically on bad
  days.
- Run reliably on an ordinary home Windows PC with no special hardware.
- Make every decision explainable. Each trade and each skipped signal is
  stored with its score breakdown and reasons, and shown in the dashboard.
- Learn from the data. Rolling percentile thresholds adapt on their own, and
  per-signal and per-window statistics show what is working.

### Non-goals (v1)
- Trading outside the three windows, or holding a position through a window's
  end.
- Holding more than one position at a time, or spread and pair trades.
- News-driven logic.
- Phone or remote alerts.
- Extra redundancy against home power or internet outages beyond the stops
  held at Tradovate (Section 11.3).
- Other instruments, or e-mini contracts.

---

## 3. Instruments and cost reality

| Symbol | Multiplier | Tick | Tick value | Typical role |
|---|---|---|---|---|
| **MES** | $5 × index | 0.25 | $1.25 | Deepest book; the most readable DOM |
| **MNQ** | $2 × index | 0.25 | $0.50 | Fast; many ticks per move, small tick value |
| **M2K** | $5 × index | 0.10 | $0.50 | Thinner; sharper sweeps, wider relative spread |

Contracts are quarterly (H, M, U, Z). The active contract is picked
automatically by volume (Section 9.4).

### 3.1 Costs, the biggest challenge for this design
Three of the choices all push costs up:
- **Market orders** pay the spread on entry (usually 1 tick in RTH, sometimes
  more in the Asia window).
- The **Free commission plan** has the highest per-contract commission.
- **Up to 15 trades a day** multiplies both.

Round-trip cost per contract, commission plus exchange, clearing and NFA fees,
is on the order of $1–$2 on the Free plan. **Use the exact numbers from your
Tradovate statement in `config/fees.yaml`.** In ticks:

| | MES | MNQ | M2K |
|---|---|---|---|
| Fees (~$1.50 RT est.) in ticks | 1.2 | 3.0 | 3.0 |
| + 1-tick market-order spread | 2.2 | 4.0 | 4.0 |
| + ~1 tick slippage on the stop exit | ~3.2 | ~5.0 | ~5.0 |

**What this means:** a trade has to move about 3 ticks on MES, or about 5 on
MNQ/M2K, just to break even. So the design includes:
- A **cost gate** in risk checks (Section 8.1): the distance to the target,
  or for trail-only trades the expected move estimated from signal
  statistics, must be at least `k ×` round-trip cost.
- **Fee tracking everywhere.** P&L is always shown net of fees, and the
  dashboard shows fees as a share of gross P&L.
- A **plan break-even calculator** in the daily report: at your actual trade
  count, how much the Monthly or Lifetime plan would save. Once the bot trades
  most days, switching plans may be the cheapest improvement available.

---

## 4. Architecture

### 4.1 Component overview

```
┌────────────────────────── Ace (one Windows process) ─────────────────────────┐
│                                                                              │
│  Tradovate MD WS ──► MarketData ──► TradeClassifier ──► FlowEngine ──┐       │
│   (ticks, quotes,      Service       (quote rule:        (delta, VP,   │       │
│    DOM)                              buy/sell aggr.)      bars, DOM)   │       │
│                                                                        ▼       │
│  Session Scheduler ───gates───────────────────────────────► Signal Detectors   │
│  (windows, holidays,                                         ├ Divergence      │
│   warmup, flatten)                                           ├ Absorption      │
│        │                                                     └ Sweep           │
│        │                                                         │ scores      │
│        │                                                         ▼             │
│        │                                              Scorer + Symbol Selector │
│        │                                                         │ TradeIntent │
│        ▼                                                         ▼             │
│   Risk Manager ◄──────────────────────────────────────────────────┘             │
│   (tiers, caps, sizing, cost gate, peak DD, edge decay)                       │
│        │ ApprovedOrder                                                         │
│        ▼                                                                       │
│   OMS / Trade Manager ◄──► Account Sync ◄──► Tradovate Trading WS/REST         │
│   (bracket entry, BE, flow trail, target, flatten)                            │
│                                                                              │
│   Persistence (SQLite, Parquet, PNG)      PySide6 Dashboard (qasync)          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Concurrency model: asyncio and Qt in one process
The engine and dashboard share one process, which you chose for simplicity.
**`qasync`** makes the asyncio event loop and the Qt event loop the same loop,
so there are no threads and no locks. Components still talk through typed,
immutable events on an in-process bus.

Running the UI in the same process means the UI can **starve the trading
logic** if it does heavy work. Rules that prevent this:

| Rule | Reason |
|---|---|
| The UI never subscribes to raw ticks. It reads a snapshot that the engine publishes at **5 Hz** (charts) and **2 Hz** (tables). | Tick bursts during sweeps can reach thousands per second. Redrawing on each one would freeze the loop. |
| Charts use **pyqtgraph**, append-only, with a capped history. | pyqtgraph is designed for fast, live plotting in Qt. |
| Screenshots render in a **separate worker process** (`ProcessPoolExecutor`) from saved data. | Rendering a PNG takes 100 ms or more, which is too long to block the event loop. |
| Disk writes are batched and run in a thread via `asyncio.to_thread`. | SQLite commits must never stall order handling. |
| A **loop-lag watchdog** measures event-loop delay every 250 ms. Over 200 ms it logs a warning; over 1 s with an open position it pauses new entries. | Catches a UI or code bug before it affects trades. |
| Every control (Flatten, Pause) goes through the same command queue as automatic actions. | One code path is easier to test and to audit. |

The engine can also start **headless** (`ace run --no-ui`). This is used for
tests and CI, and it keeps the engine independent of the UI code.

---

## 5. Sessions and trading windows

All internal timestamps are UTC. Window logic uses `zoneinfo`
(`America/New_York`), so daylight-saving changes are handled correctly.

| Window | Hours (ET) | No-trade warmup | Notes |
|---|---|---|---|
| **Asia** | 19:30–22:00 | first 2–5 min (config) | Thinner books. Spread checks and percentile thresholds are specific to this window. |
| **London** | 02:30–05:00 | first 2–5 min | Best overnight liquidity. |
| **RTH** | 09:30–16:00 | first 2–5 min | Most activity. The 09:30 open is the most volatile moment of the day. |

### 5.1 Window state machine
```
IDLE ──(T-10m)──► PREPARING ──(open)──► WARMUP ──(+N min)──► ACTIVE ──(close)──► FLATTENING ──► IDLE
```
- **PREPARING:** confirm Tradovate connections, reconcile the account, resolve
  the active contract, and load this window's 5-day percentile tables.
- **WARMUP:** build flow state (VWAP, volume profile, delta) from live data.
  No entries.
- **ACTIVE:** entries allowed, subject to risk checks.
- **FLATTENING:** at window close, cancel working orders, close any position
  at market, and confirm flat. This is a hard rule.
- Holidays and early closes: `exchange_calendars` (CME calendar) decides
  whether a window happens at all. A **live check** also runs: if quotes
  aren't updating during PREPARING, the window is skipped.

### 5.2 News
You chose to ignore scheduled news. No economic calendar is used. Protection
still comes from the always-on checks: a spread limit, a quote-freshness
limit, and the stop set at entry. Releases such as CPI at 08:30 ET and FOMC at
14:00 ET fall inside or near RTH, so expect occasional large slippage on
those days. The journal tags trades taken within ±5 minutes of the top-tier
release times so their effect can be measured later.

---

## 6. Tradovate integration

> Endpoint and payload details reflect Tradovate's public API as currently
> understood. Everything must be verified against the current documentation
> and the **demo** environment during milestone M1.

### 6.1 Prerequisites
- A Tradovate account with **API Access** enabled, and an API key (`cid`,
  `sec`).
- A CME market data subscription that covers API access. Non-professional
  status keeps the cost down.
- Demo (`demo.tradovateapi.com`) is used for all development and the forward
  test. Live (`live.tradovateapi.com`) is used only after sign-off.

### 6.2 Authentication
- `POST /auth/accesstokenrequest` (`name`, `password`, `appId`, `appVersion`,
  `cid`, `sec`, `deviceId`) returns `accessToken`, `mdAccessToken`, `userId`,
  and `expirationTime`.
- Tokens are renewed via `/auth/renewaccesstoken` about 15 minutes before
  they expire.
- The bot requests a fresh token only on startup or after a failed renewal.
  Repeated logins can trigger rate-limit penalties.
- `deviceId` is a UUID generated once and saved to `data/device_id`.

### 6.3 Credentials: `.env`
```
TRADOVATE_USERNAME=...
TRADOVATE_PASSWORD=...
TRADOVATE_CID=...
TRADOVATE_SECRET=...
TRADOVATE_ENV=demo
```
- `.env` is listed in `.gitignore` from the very first commit, and a
  pre-commit hook blocks any file that contains these keys.
- The values are loaded with `pydantic-settings` into `SecretStr` fields, so
  they never appear in logs, repr output, or the dashboard.
- Limitation: any program running under your Windows user can read `.env`.
  Moving to Windows Credential Manager (`keyring`) later only changes one
  loader function.

### 6.4 WebSocket client
Both sockets (trading and market data) use Tradovate's framed protocol:
- `o` means the socket is open. The client then sends
  `authorize\n0\n\n<token>`.
- `h` is a heartbeat. `a[...]` carries data or responses. `c` means the server
  is closing the socket.
- Requests are sent as `endpoint\nid\nquery\nbody`. Each response is matched
  to its request by `id` through a dictionary of `asyncio.Future` objects,
  with a timeout.
- The client sends a heartbeat (`[]`) about every 2.5 s. If no frame arrives
  for more than 10 s, the socket is treated as dead and reconnected with
  backoff. After reconnecting, the bot re-subscribes to everything and runs a
  full reconciliation.
- **Rate-limit penalty responses** (`p-ticket` / `p-time`): the client waits
  the given time and retries with the ticket. A `p-captcha` response pauses
  trading and shows an alert on the dashboard.

### 6.5 Market data used
| Subscription | Use |
|---|---|
| `md/subscribeQuote` (per symbol) | Best bid/ask for the quote rule, spread checks, quote freshness |
| `md/getChart`, tick chart (`underlyingType: "Tick"`, `elementSize: 1`) | Every trade: price, size, and the bid/ask at the time of the trade (verify these fields in M1). Feeds delta, volume profile, sweeps, and absorption. |
| `md/subscribeDOM` (per symbol) | Visible depth for absorption (refill), target levels (large resting size), and liquidity checks. Tradovate provides a limited number of price levels, which is enough for the top of book. |
| `md/getChart`, minute bars (warmup) | Backfill today's context (VWAP, session high and low) after a restart |

All three symbols stream during every active window, because symbol
selection needs live scores for all of them.

### 6.6 Account sync and orders
- `user/syncrequest` gives a snapshot, then streaming `props` updates for
  orders, fills, positions and cash balance. The **AccountState** component
  is the only writer of position state.
- Entries use **`order/placeOSO`**: a market entry with two linked exit
  orders, a stop and a limit target. When one fills, Tradovate cancels the
  other. Trail-only trades send just the stop. All orders set
  `isAutomated: true`.
- `order/modifyorder` handles breakeven and trail moves. `order/cancelorder`
  and `order/liquidateposition` handle flattening.

---

## 7. Signal engine (order flow)

### 7.1 Trade classification: the quote rule
Each trade from the tick stream is labeled by comparing its price with the
bid and ask in effect when it happened:

| Condition | Aggressor | Contribution to delta |
|---|---|---|
| price ≥ ask | Buyer | +size |
| price ≤ bid | Seller | −size |
| bid < price < ask (inside a wide spread) | Unknown | split: +size/2 and −size/2 (no net effect) |

When the tick record includes bid/ask at trade time, those values are used.
Otherwise the latest `subscribeQuote` values are used. Quote timing
mismatches are the main source of misclassified trades, so the bot records
the classification rate (share of trades labeled *unknown*) as a data-quality
metric. If a pro feed with exchange aggressor flags replaces Tradovate later,
only this component changes.

### 7.2 FlowEngine: shared state per symbol
Detectors read from these structures, and the dashboard charts them:
- **Cumulative delta**, reset at each window start.
- **1-min and 5-min bars**, each with OHLC, volume, buy volume, sell volume,
  delta, max single print, and trade count.
- **Volume profile**, volume by price for the current window. Used to find
  high-volume nodes (HVNs).
- **Swing points** on the 1-min and 5-min bars (fractal-style: highs and lows
  confirmed by N bars on each side).
- **Session VWAP** and window high and low; prior day's RTH high and low.
- **DOM snapshot**, including a history of size at each price, used for
  refill detection.
- **Absorption zone registry:** levels where absorption was detected, with
  their side and strength. Also used as target levels.

### 7.3 Adaptive thresholds: rolling percentiles
"Large" and "heavy" are defined relative to recent history of the **same
window over the last 5 trading days**. London volume is compared with past
London sessions, never with RTH.

- At the end of each window, the bot saves compact distributions (histograms
  or quantile sketches) of each raw feature to SQLite, keyed by
  `(symbol, window, feature, date)`. Features include burst volume, print
  size, per-level volume, delta per window, and DOM size.
- During PREPARING, it loads the last 5 days for the coming window and merges
  them into percentile lookups. For example, "burst volume ≥ p95" becomes a
  concrete number of contracts.
- **Cold start:** until 5 days of history exist for a window, config
  defaults are used and the dashboard marks the window as *calibrating*.
  Recording starts in the first demo session.

This works without full tick storage, because the features are summarized as
they arrive.

### 7.4 Detectors
Each detector returns, per symbol, a **direction** (long or short) and a
**strength from 0 to 1**, along with the price level the signal refers to.
That level is used for stop placement.

#### Aggressive sweep: fires on any of these
1. **Levels in time:** one-sided aggression clears at least N price levels
   within T ms (default 4 levels in 500 ms; per-symbol settings).
2. **Volume burst:** one-sided aggressive volume in a rolling 1–5 s window is
   at or above p95 of its 5-day distribution.
3. **Large prints:** a single print, or a cluster with the same timestamp, at
   or above p99 of print size.

Strength is the highest of the three normalized sub-scores, plus a bonus
when more than one fires. Direction follows the sweep. The level is where the
sweep started.

#### Absorption: the strongest of three methods
1. **Volume at price without a break:** aggressive volume at one price is at
   or above p90 of per-level volume, and price fails to trade through it for
   T seconds.
2. **DOM refill:** visible resting size at a level is hit repeatedly and
   refills K or more times within T seconds.
3. **Delta without progress:** strong one-sided delta (≥ p90) over a short
   window while price moves less than X ticks in that direction.

Direction is **against** the aggressor: sellers being absorbed means long.
The level is the absorbed price. Each detection also goes into the
absorption zone registry.

#### Delta divergence: multi-scale
- **1-min scale:** price makes a higher swing high (or lower swing low) while
  cumulative delta at that swing does not.
- **5-min scale:** the same test on 5-min swings.
- Strength is 0.5 for a single scale and 1.0 when both scales diverge in the
  same direction. It is further scaled by how large the gap is between the
  price move and the delta move.
- Direction is a reversal against the swing. The level is the swing extreme.

### 7.5 Scoring
```
score(symbol, dir) = w_div × divergence + w_abs × absorption + w_sweep × sweep
                     (v1: w = 1/3 each; each term in 0..1)
enter only if score ≥ entry_threshold   (default 0.55; editable live)
```
- Signals decay. Each detector's value decreases over a configurable
  half-life (default 60 s), so an absorption from 3 minutes ago counts only
  slightly toward a sweep happening now.
- Opposing signals subtract. A long score uses the long-side strengths minus
  a fraction of the short-side ones.
- Weights stay equal in v1. The **signal analytics** in the journal (P&L
  grouped by which detectors contributed) is the evidence for changing them
  after the demo period.

### 7.6 Symbol selection (one position at a time)
When one or more symbols cross the threshold within a short collection window
(default 250 ms), each candidate gets a selection score:

```
selection = 0.6 × normalized_signal_score
          + 0.3 × reward_to_risk_score      (distance to target ÷ stop distance; trail-only uses a neutral value)
          + 0.1 × recent_symbol_score       (rolling net R of this symbol's last N trades in this window, clipped)
```

The best candidate goes to risk checks. If risk rejects it only because
**the stop is too wide for the budget**, the next-best candidate with a
cheaper tick value (MNQ or M2K) is tried, if it also meets the threshold. If
no candidate fits, the signal is logged as skipped.

---

## 8. Risk management

### 8.1 Pre-trade checks (the first failure rejects the trade)
1. The bot isn't paused, the window is ACTIVE, and the symbol is enabled.
2. No open position (one position at a time) and no working entry order.
3. Not inside the 5-minute cooldown that follows a losing trade.
4. The daily trade count is below 15.
5. The daily loss tier allows trading (Section 8.3).
6. The peak-drawdown and edge-decay locks are clear (Section 8.4).
7. Data is healthy: the last quote is under 2 s old, the spread is within
   limit for this window, the loop-lag watchdog is fine, and the account was
   reconciled recently.
8. Sizing gives at least 1 contract within budget (Section 8.2).
9. Cost gate: the expected move is at least `k` × round-trip cost
   (default `k = 3`).

Every rejection is logged with its reason and the full score breakdown. This
feeds the missed-trade analysis.

### 8.2 Sizing
```
stop_price     = flow_level ∓ buffer_ticks          (beyond the level, per-symbol buffer)
per_contract   = |entry_est − stop_price| × point_value
               + spread_ticks × tick_value          (market entry)
               + slip_ticks × tick_value            (stop exit)
               + round_trip_fees
risk_budget    = base_risk_usd × tier_multiplier    (base $25–$50; tier 1.0 or 0.5)
contracts      = floor(risk_budget / per_contract)
contracts == 0 → "stop too wide": try another symbol (7.6), else skip
```

**Example:** budget $40. An MES absorption at 5712.00, long, 2-tick buffer,
stop at 5711.50. Entry is about 5712.50 (market at the ask), so the stop
distance is 1.00 point, or $5. Add $1.25 spread + $1.25 slippage + $1.50 fees
to get **$9.00 per contract**, which gives 4 contracts. A comparable MNQ setup
with a 16-tick stop: $8 + $0.50 + $0.50 + $1.50 = $10.50, which gives 3
contracts.

Market-order entries fill at an unknown price. Once the fill arrives, the
stop stays at the flow level, so the **real** risk is recomputed. If slippage
pushed it more than 25% over budget, it is logged and counted in the slippage
statistics.

### 8.3 Daily tiers (count-based)
| State | Trigger | Effect |
|---|---|---|
| **Normal** | Start of each trading day (18:00 ET roll) | Full base risk |
| **Tier 1 (half size)** | 2 consecutive losses | `tier_multiplier = 0.5` until a winning trade resets it |
| **Tier 2 (stopped)** | 4 losing trades on the day | No new entries until the next trading day |

Breakeven exits (within ±fees) count as neither a win nor a loss. The day
starts with the Asia window, following CME's trading-day convention.

### 8.4 Longer-term locks
- **Peak drawdown, 10%.** If account equity drops 10% below its all-time
  high, the bot stops all trading and shows a *review required* banner. Only
  an explicit dashboard action, which is logged, re-enables trading.
- **Edge decay, per window.** Net expectancy after fees is tracked separately
  for Asia, London and RTH over the last N trades in each (default 40). If a
  window's expectancy turns negative, **only that window** is disabled, and
  the dashboard shows it. You re-enable it manually after reviewing.

### 8.5 Halt levels
| Level | Examples | Effect |
|---|---|---|
| `PAUSE` | Operator pause, cooldown, loop lag, stale quotes | No new entries; the open trade keeps being managed |
| `FLATTEN` | Window end, operator "Flatten now", Tier 2 hit with a trade open (it finishes first), peak-DD lock | Cancel all orders, close the position, confirm flat |
| `STOP` | Unresolved reconciliation mismatch, repeated order rejects, `p-captcha`, unexpected exception in risk/OMS | Flatten, disable trading, show a red banner; needs manual restart |

---

## 9. Execution and trade management

### 9.1 Order lifecycle
```
NEW → PENDING_SUBMIT → WORKING → FILLED
            │             │
            ▼             ▼
         REJECTED     CANCELLED
```
- Client order IDs use the form `ace-<uuid8>` and are saved **before**
  sending.
- **No blind resends.** If there's no acknowledgment within 5 s, the bot
  checks Tradovate for the order's state before doing anything else.
- **Market entry** is sent via `placeOSO`, with the stop (and the target, if
  there is one) attached. Once the fill arrives, bracket quantities follow
  the filled quantity.

### 9.2 Managing the open trade
1. **Initial:** stop just beyond the flow level. Target at the next flow
   level, or none (trail only).
2. **Breakeven:** when price moves in favor by the initial risk distance
   (+1R, configurable), the stop moves to entry + fees.
3. **Flow trail**, after breakeven. The stop tightens when:
   - delta stalls: cumulative delta in the trade's direction fails to make
     progress for N seconds while price is flat, or
   - an opposing sweep or absorption with strength ≥ θ appears. The stop
     then moves to just beyond the most recent 1-minute swing, or to a
     configurable fraction of open profit.
4. **Targets** are chosen from these candidates, taking the nearest one at a
   meaningful distance (≥ 1R) in the trade's direction:
   - high-volume nodes in the window's volume profile,
   - opposing absorption zones from the registry,
   - large resting DOM size (≥ p95 of this window's DOM size),
   - window high or low, prior RTH high or low, and session VWAP.

   If none qualify, the trade is **trail only**.
5. **Window end:** forced flatten (Section 5.1).
6. There is **no time stop**. A trade exits only through its stop, target,
   trail, window end, or an operator or risk flatten.

Every stop change is sent as `modifyorder`, and the local record updates only
after Tradovate acknowledges it. Stop moves only ever reduce risk, never widen
it.

### 9.3 Reconciliation and stray positions
Reconciliation runs at startup, after every reconnect, and every 30 s while a
trade is open.
- **Local and Tradovate disagree:** Tradovate is treated as correct. The
  local state is corrected and the event is logged.
- **Position with no working stop:** a stop is attached immediately at the
  planned level, or at an ATR-based fallback.
- **Stray position at startup** (not opened by Ace): **adopt it.** The bot
  attaches a protective stop at `max(recent swing, 1.5 × ATR(1m))` away,
  starts managing it with the flow trail, and flags it in the journal as
  adopted. The stop's risk is clipped to the budget only if the position is
  small enough. Otherwise the stop sits at the fallback distance and a
  warning banner appears.
- A mismatch that isn't resolved within 2 cycles triggers `STOP`.

### 9.4 Contract roll (by volume)
Each day during PREPARING, the bot compares the front and next contracts'
volume from the prior session, using Tradovate chart data. On the first day
the next contract's volume is higher, the active contract switches, and the
dashboard shows a banner with both contract names. This normally happens
about 8 days before expiration. The bot is always flat between windows, so a
roll never involves moving a position.

---

## 10. Data, storage and reporting

| Store | Contents | Format |
|---|---|---|
| `data/ace.db` | Signals (with every detector's sub-scores), selection decisions, risk rejections, orders, order events, fills, trades, daily and per-window stats, percentile histograms, halts, operator actions, and parameter changes | SQLite (WAL mode) |
| `data/bars/` | 1-min bars with flow stats (buy/sell volume, delta, max print, DOM imbalance summary) for each symbol | Parquet, partitioned by `symbol/date` |
| `data/shots/` | One PNG per trade: 1-min chart with entry, stop moves, exit, target, and the triggering signals marked, plus a delta subplot | PNG, rendered in a worker process |
| `logs/` | Structured JSON logs (`structlog`), rotated daily | JSONL |

Full tick and DOM recording is **off** (you didn't select it). The 1-min flow
bars plus the percentile histograms are enough for the dashboard, reporting,
and threshold adaptation. A `record_ticks: true` switch can be added later if
you want to build a replay dataset.

**Reports** (dashboard tab and HTML export):
- Daily: net P&L, fees, fees as % of gross, win rate, average R, and
  breakdowns by window and symbol.
- Signal analytics: P&L grouped by detector combination and by score range.
- Missed trades: rejected signals, with the result of a hypothetical fill.
- Commission-plan comparison at the actual trade count.

---

## 11. Dashboard (PySide6)

### 11.1 Layout
```
┌──────────────────────────────────────────────────────────────────────────┐
│ [● DEMO] Window: LONDON  ACTIVE   Tier: Normal   Trades 6/15   [PAUSE] [FLATTEN] │
├───────────────────────────────────────────┬──────────────────────────────┤
│ Price + flow chart (pyqtgraph)            │ Signal scoreboard            │
│  1-min candles · VWAP · HVNs · absorption │  MES  L 0.41 ▓▓▓▓░░  S 0.12   │
│  zones · sweep markers · stop/target lines│  MNQ  L 0.58 ▓▓▓▓▓▓  S 0.05 ◄ │
│  ─────────────────────────────────────── │  M2K  L 0.22 ▓▓░░░░  S 0.30   │
│  cumulative delta subplot                 │  threshold 0.55 · div/abs/swp  │
│  [MES] [MNQ] [M2K] symbol tabs            ├──────────────────────────────┤
│                                           │ Position & risk              │
│                                           │  MNQ long 3 @ 20115.25        │
│                                           │  stop 20111.00 (BE) tgt HVN   │
│                                           │  Day P&L +$62.50 (fees $13.50) │
│                                           │  DD from peak 2.1% · windows ✓✓✓│
├───────────────────────────────────────────┴──────────────────────────────┤
│ Trade journal: time · symbol · side · qty · entry · exit · R · net · reasons · 📷 │
└──────────────────────────────────────────────────────────────────────────┘
```

### 11.2 Controls
| Control | Behavior |
|---|---|
| **Pause / Resume** | Blocks new entries; the open trade is still managed |
| **Flatten now** | Confirmation dialog, then close the position and cancel orders |
| **Symbol toggles** | Enable or disable MES, MNQ, M2K individually (applies to new entries only) |
| **Live parameter edits** | A panel for an allowlisted set of parameters: `entry_threshold`, `base_risk_usd` (capped at $50), `max_trades_per_day`, detector weights, `buffer_ticks`. Every change is validated, logged with old and new values, and persisted. |
| **Review locks** | Re-enable after the peak-DD lock or a window's edge-decay lock (requires typing a confirmation) |

There is deliberately **no manual order entry.** You can stop the bot, but
you can't trade through it.

### 11.3 Alerts
Alerts appear only on the dashboard: a status bar color, a banner, and a
Windows toast while the window is open. Two design choices keep overnight
risk small without phone alerts:
- Every trade's stop sits at Tradovate from the moment of entry.
- Every window ends with a forced flatten, so the bot never carries a
  position through a quiet period.

The dashboard shows a **"since you last looked"** summary: trades, P&L,
halts and reconnects since the window last had focus.

---

## 12. Running on the home PC

- **Windows 11.** Set active hours to cover the trading windows and disable
  sleep and hibernate on AC power. The installer checklist and `ace doctor`
  command check these settings.
- **Startup:** `ace run` opens the dashboard and engine together. A
  shortcut can be added to the Startup folder if wanted.
- **Clock:** `ace doctor` checks that Windows time sync is on and that the
  clock drift against an NTP server is under 250 ms. Bar boundaries and the
  quote rule depend on accurate time.
- **Restart safety:** after any restart, startup reconciliation (Section 9.3)
  runs before anything else.
- **Portability:** the engine has no Windows-only code outside `ops/`, and
  it can run headless. That keeps CI (Linux) and any later move cheap,
  though no move is planned.

---

## 13. Validation and going live

1. **Build and test** against the fake Tradovate server (Section 14.2).
2. **Demo forward test** on Tradovate demo during all three windows.
   - The first 5 trading days are mainly *calibration*: percentile tables fill
     in and the bot trades with default thresholds.
   - Watch these closely: classification rate, fees as % of gross,
     per-detector P&L, slippage on market entries, and loop lag.
   - Demo fills are simulated by Tradovate and tend to be **more generous**
     than live, especially for market orders in the Asia window. Treat demo
     results as an upper bound.
3. **Manual sign-off** (your decision). The dashboard's review report gives
   you: trade count, net expectancy after fees per window and per symbol,
   max drawdown, operational incident count, and the demo slippage
   distribution.
4. **Live** with `TRADOVATE_ENV=live`. The dashboard shows a red **LIVE**
   badge, and the first live day uses the minimum budget ($25) no matter what
   the config says.

---

## 14. Codebase

### 14.1 Layout
```
ace/
├── pyproject.toml            # uv-managed; Python 3.12
├── .env.example              # keys only, no values
├── .gitignore                # .env, data/, logs/
├── config/
│   ├── ace.yaml              # all tunables (Section 15)
│   └── fees.yaml             # Free-plan rates from your statement
├── src/ace/
│   ├── app.py                # wiring, qasync loop, CLI (run / doctor / report)
│   ├── core/                 # event bus, clock, types, commands
│   ├── tradovate/            # auth, socket, md, sync, orders, models (pydantic)
│   ├── flow/                 # classifier (quote rule), engine, bars, profile, swings, dom
│   ├── signals/              # divergence, absorption, sweep, percentiles, scorer, selector
│   ├── session/              # windows, scheduler, calendar, roll
│   ├── risk/                 # checks, sizing, tiers, locks, edge_decay
│   ├── execution/            # oms, order_fsm, trade_manager (BE / trail / target), reconciler
│   ├── persistence/          # db, bars store, journal, screenshots (worker)
│   ├── reports/              # daily, signal analytics, missed trades, plan comparison
│   └── ui/                   # PySide6: main window, chart, scoreboard, risk panel, journal, params
└── tests/
    ├── unit/                 # sizing, tiers, quote rule, detectors, FSMs, window times incl. DST
    ├── property/             # hypothesis: risk invariants
    ├── integration/          # fake Tradovate WS server: full trade lifecycles, disconnects
    └── fixtures/             # synthetic tick/DOM scenarios (sweep, absorption, divergence)
```

### 14.2 Quality bar (production-grade)
- **Typing:** `mypy --strict` on everything. `ruff` for linting and
  formatting. Pre-commit hooks, including a secret scan.
- **Fake Tradovate server:** an asyncio WebSocket server that speaks the
  framed protocol. It accepts orders, simulates fills from a scripted
  price path, and can inject faults: disconnects, penalties, rejects, delayed
  acknowledgments. All engine integration tests run against it.
- **Detector fixtures:** handcrafted tick and DOM sequences that must trigger,
  and must not trigger, each detector. They form a regression suite for the
  signal math.
- **Property tests** (`hypothesis`) over random event sequences check that:
  - there is never more than one position at a time;
  - no order is sent without a stop;
  - planned risk never exceeds the budget;
  - no entry happens outside an ACTIVE window, in cooldown, over the trade
    cap, or in Tier 2;
  - after FLATTEN, the account is flat within T seconds;
  - stop moves never widen risk.
- **CI:** GitHub Actions runs lint, type-check and tests (headless, Linux) on
  every push.

---

## 15. Configuration (`config/ace.yaml`)

```yaml
environment: demo
symbols: [MES, MNQ, M2K]

windows:
  asia:   { start: "19:30", end: "22:00", warmup_min: 3 }
  london: { start: "02:30", end: "05:00", warmup_min: 3 }
  rth:    { start: "09:30", end: "16:00", warmup_min: 3 }
  timezone: America/New_York

signals:
  weights: { divergence: 0.3333, absorption: 0.3333, sweep: 0.3333 }
  entry_threshold: 0.55
  decay_half_life_sec: 60
  percentile_lookback_days: 5
  sweep:      { levels: 4, window_ms: 500, burst_pctl: 95, print_pctl: 99 }
  absorption: { level_vol_pctl: 90, hold_sec: 20, refill_count: 3, delta_pctl: 90, max_progress_ticks: 2 }
  divergence: { swing_bars: 2, scales: ["1m", "5m"] }

selection:
  collect_window_ms: 250
  weights: { signal: 0.6, reward_risk: 0.3, recent: 0.1 }

risk:
  base_risk_usd: 40            # allowed range 25–50
  max_trades_per_day: 15
  loss_cooldown_min: 5
  tier1_consecutive_losses: 2
  tier1_multiplier: 0.5
  tier2_daily_losses: 4
  peak_drawdown_pct: 10
  edge_decay: { per_window: true, lookback_trades: 40 }
  cost_gate_k: 3
  buffer_ticks: { MES: 2, MNQ: 4, M2K: 3 }
  max_spread_ticks:
    rth:    { MES: 1, MNQ: 2, M2K: 2 }
    london: { MES: 2, MNQ: 3, M2K: 3 }
    asia:   { MES: 2, MNQ: 4, M2K: 4 }
  stale_quote_sec: 2

management:
  breakeven_at_r: 1.0
  trail: { delta_stall_sec: 20, opposing_strength: 0.5 }
  target_min_r: 1.0

roll: { method: volume }
ui: { chart_hz: 5, table_hz: 2 }
storage: { record_ticks: false, screenshots: true }
```

Every numeric default above is a **starting point to calibrate during demo**,
not a tested value.

---

## 16. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Repo skeleton, tooling, CI, config models, `.env` handling | CI green; `ace doctor` runs |
| M1 | Tradovate demo connectivity: auth, both sockets, quotes, tick chart, DOM, user sync; fake server | 8-hour soak without leaks; tick/bid/ask fields confirmed |
| M2 | Flow engine: quote rule, bars, delta, volume profile, swings, DOM state; persistence of 1-min flow bars | Bars match Tradovate's charts; classification rate reported |
| M3 | Windows, scheduler, calendar, roll | DST and holiday tests pass |
| M4 | Detectors, percentiles, scorer, selector | Fixture suite passes; live scores visible in logs |
| M5 | Risk (checks, sizing, tiers, locks) and OMS/trade manager on demo | Property tests pass; full trade lifecycle on demo |
| M6 | PySide6 dashboard (all panels and controls), screenshots, reports | UI stays responsive during RTH open bursts (loop lag under 50 ms p99) |
| M7 | Demo forward test | Your sign-off |
| M8 | Live at $25 risk | — |

---

## 17. Open items and known risks

1. **Costs vs edge (highest risk).** Market entries, the Free plan and 15
   trades a day make costs large relative to micro tick values (Section 3.1).
   The demo period has to show a positive expectancy **net of fees**. Plan
   comparison and the cost gate are the main tools for managing this.
2. **Tradovate feed limits.** The quote rule depends on accurate bid/ask
   timing, and Tradovate's DOM depth is limited. If classification quality
   or absorption detection is poor, a pro feed (Databento or Rithmic) can be
   added behind the existing data-source interface.
3. **Demo fill optimism.** Demo market fills are usually better than live.
   Expect worse live slippage, especially in the Asia window.
4. **Calibration period.** Each window needs 5 days before its percentile
   thresholds are meaningful.
5. **Same-process UI.** This is manageable with the rules in Section 4.2. If
   loop lag becomes a recurring problem, splitting the UI into its own
   process is a contained change.
6. **Unanswered topics** (defaults assumed): growth of risk with equity
   (fixed for now), report cadence (daily plus on demand), holiday source
   (calendar library plus a live check).
