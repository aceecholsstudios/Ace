# wall2: Design Document for a 0DTE Options Bot on Webull

| | |
|---|---|
| **Status** | Draft v0.1, from the requirements Q&A (still in progress) |
| **Author** | Ace Echols Studios |
| **Last updated** | 2026-10-03 |
| **Broker / API** | Webull, official OpenAPI (options support to verify in M1) |
| **Instruments** | Same-day-expiry (0DTE) calls and puts on SPY, QQQ, IWM |
| **Account** | Webull **cash** account, under $5k |
| **Host** | Home PC, Windows 11, with a PySide6 desktop dashboard |
| **Time zone** | **All times in US Central (CT)**, `America/Chicago` |
| **Relation to Ace** | Fully independent codebase. Lives in `wall2/` in the same repo. |

---

## 1. Summary

wall2 is a fully automatic Python bot that **buys** same-day-expiry (0DTE)
calls and puts on SPY, QQQ and IWM through Webull. It trades **trend
pullbacks**. The trend is set by price vs VWAP on the 15-minute chart. Entries
come on a pullback to the 9 EMA on the 5-minute chart, triggered by a 1-minute
close that breaks the pullback bar.

Each trade spends a fixed **$20 of premium**. That premium is the entire risk:
there is no stop-loss. A winning trade is held while the trend holds and sold
when price closes through the 9 EMA. A losing trade is usually held to
expiration, unless it can still be sold for at least half of what it cost. Any
in-the-money option is sold at **14:50 CT** so it is never exercised.

The bot trades at most **3 times a day**, holds at most **one position per
ETF**, and only ever spends **settled cash**.

### 1.1 Decision record

| Area | Decision |
|---|---|
| Goal | 0DTE / same-day trading |
| Underlyings | SPY, QQQ, IWM |
| Account | Webull cash account, under $5k |
| Autonomy | Fully automatic |
| Structure | Long calls and puts only |
| Strategy | VWAP / trend pullback, multi-timeframe |
| Trend (15-min) | Price above VWAP = up (calls); below = down (puts) |
| Pullback (5-min) | A 5-min bar touches the 9 EMA |
| Trigger | 1-min close beyond the pullback bar's high (calls) or low (puts) |
| Setup lifetime | Trigger must come within 2 five-minute bars (10 min) |
| Direction | Both calls and puts |
| Re-entry on an ETF | Same direction only, until the 15-min trend flips across VWAP |
| Risk per trade | Full premium is the risk; **$20** per trade |
| Sizing growth | Manual only (budget editable in the dashboard) |
| Strike target | Slightly OTM, ~0.30–0.40 delta |
| If $20 can't buy the target | Go further OTM, down to a **0.10 delta floor** |
| Greeks | From Webull; if missing, approximate delta from moneyness |
| Liquidity filter | Bid ≥ $0.05 |
| ETF choice | "Cheapest fit": highest delta that $20 buys; ties go to the first signal |
| No 0DTE expiry for an ETF | Skip that ETF for the day |
| Entry order | Limit at the ask; re-price once if unfilled; re-select strike if the ask jumps |
| Winning exit | Trail: 5-min close through the 9 EMA. After +100%, tighten to a 1-min close. |
| Profit exit order | Midpoint first, then the bid |
| Losing trades | Hold to expiry, **unless** a trend break comes while the option is still worth ≥ 50% of entry. Then sell at the bid. |
| End of day | Sell any **in-the-money** option at **14:50 CT** (11:50 CT on half-days). Let OTM options expire. |
| Trades per day | Up to 3 |
| Concurrency | One position per ETF |
| Trading window | First entry **8:35 CT**; no separate entry cutoff (entries stop when the 14:50 close-out begins) |
| Loss limits | The 3-trade cap only (max $60/day). No daily-loss or long-term stop. |
| Settlement | Spend settled cash only. Not enough cash → skip, and record it in the shadow log. |
| News days | Trade normally |
| Holidays / half-days | `exchange_calendars` (NYSE) |
| Halts / frozen quotes | Pause entries until quotes are healthy for 2+ minutes |
| Disconnect | Reconnect, reconcile, resume managing |
| Stray positions | Adopt and manage, without counting them toward bot limits |
| Webull API | Official OpenAPI; confirm 0DTE chains and orders in M1. Fallback decided then. |
| Market data | Webull only, streaming |
| Validation | Webull paper trading for 4 weeks; go-live is your call. Built-in simulator if the API has no paper mode. |
| Simulator fills | Buy at the ask, sell at the bid |
| Host | Home Windows PC; auto-start 8:00 CT, shut down after 15:15 CT |
| Pre-open checks | API and login, settled cash, 0DTE chains, clock and calendar |
| Indicator warmup | Load the prior 2–3 days plus today |
| Dashboard | PySide6, same process. Charts with signals, positions and cash, trade journal, controls. |
| Controls | Pause/resume, close all now, ETF toggles, budget edit |
| Alerts / report | Daily HTML report saved locally at 15:15 CT |
| Report contents | Trades and P&L (with screenshots), shadow signals, cash and settlement, running stats |
| Storage | SQLite (trades and decisions), underlying 1-min bars, trade screenshots |
| Shadow log | Skipped signals with outcomes, by streaming the would-be contract's quotes |
| Fees | Config file, taken from your Webull statement |
| Retention | Keep everything |
| Credentials | `.env`, git-ignored |
| Stack | Python 3.12, asyncio, pydantic, SQLite, PySide6, uv |
| Engineering rigor | Core tested: risk, settlement, orders, signals |

---

## 2. Goals and non-goals

### Goals
- Trade 0DTE trend pullbacks on SPY, QQQ and IWM with **no human
  involvement**, from pre-open checks to the end-of-day report.
- Cap every trade's loss at its premium ($20) and every day's loss at $60.
- Never break cash-account rules: no good-faith violations, never risk
  exercise.
- Record enough data, including skipped signals, to judge whether the
  strategy works.

### Non-goals (v1)
- Selling options or spreads.
- Holding anything overnight.
- Instruments other than SPY, QQQ and IWM 0DTE options.
- Phone alerts.
- Sharing code with Ace.

---

## 3. Background: what makes this design different

This section explains the market mechanics behind the decisions.

### 3.1 0DTE options decay fast
An option's price is intrinsic value (how far it is in the money) plus time
value. On expiration day, time value disappears quickly, and fastest in the
last hours. An OTM option has **only** time value, so it loses value every
minute the underlying doesn't move toward its strike.

**Consequence:** a long 0DTE option needs the move to happen **soon**. That
is why wall2 trades only with the trend and enters on a confirmed resumption
(the trigger), not on the pullback itself.

### 3.2 Delta, and what $20 buys
Delta is roughly how much the option's price moves per $1 move in the
underlying, and loosely the market's estimate of the chance it finishes in
the money.
- A 0.35-delta 0DTE SPY call typically costs well over $20 (often $30–$150),
  especially in the morning.
- At $20, the bot will often buy 0.10–0.25 delta options. These are cheap and
  move a lot in percentage terms when the trend continues. Most of them
  expire worthless when it doesn't.
- The 0.10 floor keeps it away from true lottery tickets.
- Premiums fall during the day, so **afternoon** trades will more often reach
  the 0.30–0.40 target.

### 3.3 Cash-account rules
- **No pattern-day-trader limit.** This is why a cash account works for
  daily trading under $25k.
- **Settlement:** options settle **T+1**. Money from selling an option today
  can't be used until the next business day. Buying with unsettled money and
  then selling that position before the money settles is a **good-faith
  violation**, and repeated violations get the account restricted. wall2
  avoids this completely by spending **settled cash only**.
- **Exercise:** an in-the-money long option at expiration is normally
  exercised automatically. 100 SPY shares cost tens of thousands of dollars,
  far more than the account holds. The broker would have to step in, which
  can be costly. That is why ITM options are sold at **14:50 CT**.

### 3.4 Why the ETF with the cheapest fit
With a fixed $20, the ETF whose option chain offers the **highest delta for
$20** gives the most exposure to the move. IWM often wins because its share
price is lower, so its options are cheaper.

---

## 4. Architecture

```
┌──────────────────────────── wall2 (one Windows process) ───────────────────────────┐
│                                                                                    │
│  Webull streaming ──► MarketData ──► BarBuilder (1m/5m/15m) ──► Indicators         │
│  (quotes, option       Service        + VWAP                     (VWAP, EMA9)      │
│   quotes, greeks)         │                                          │              │
│                           │                                          ▼              │
│  Session Clock (CT) ──────┼────────gates──────────────────► Setup Detector          │
│  (calendar, 8:35 start,   │                                 (trend → pullback →     │
│   14:50 close-out)        │                                  1-min trigger)         │
│                           │                                          │ Signal       │
│                           ▼                                          ▼              │
│                    Option Chain ◄──────────── Contract Selector (delta ≥ 0.10,      │
│                    Service (0DTE)              $20 fit, bid ≥ $0.05, cheapest fit)  │
│                                                              │ Candidate           │
│                                                              ▼                      │
│   Cash & Settlement Ledger ◄──────────────────────── Risk Gate                      │
│   (settled cash, T+1 tracking)                        (3/day, 1 per ETF, cash,      │
│                                                        direction rule, halts)       │
│                                                              │ Approved            │
│                                                              ▼                      │
│   Webull Broker Adapter ◄──────────────────────────── Order Manager                 │
│   (official OpenAPI)                                  (limit at ask, re-price once) │
│                                                              │                      │
│                                                       Position Manager              │
│                                                       (trail, salvage rule,         │
│                                                        14:50 ITM sell)              │
│                                                                                     │
│   Shadow Tracker   Persistence (SQLite, Parquet, PNG)   Report (15:15)   PySide6 UI │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

**Concurrency:** a single asyncio event loop, shared with Qt through
`qasync`. The data rate is low (three underlyings plus a few option
contracts), so one process is comfortable. The UI refreshes from snapshots at
2–5 Hz and never redraws on every tick.

**Broker adapter:** the Webull code sits behind a small `Broker` interface
(`quotes`, `chain`, `place`, `cancel`, `positions`, `balances`). This isolates
the API details, enables the built-in simulator and fake-broker tests, and
keeps the M1 fallback decision (Section 10) cheap to act on.

---

## 5. Trading day (all times CT)

| Time | Phase | What happens |
|---|---|---|
| 8:00 | **Startup** | Windows Task Scheduler launches wall2 on trading days |
| 8:00–8:30 | **Pre-open checks** | Webull login and token; settled cash; today's 0DTE chain exists for each ETF (an ETF without one is disabled for the day); PC clock drift; full day vs half-day vs holiday (`exchange_calendars`, NYSE); load the prior 2–3 days plus today's 1-min bars to warm up the indicators |
| 8:30 | **Open** | Streaming starts; VWAP anchors at the open |
| 8:35 | **Entries allowed** | The first 5-min bar is complete |
| 8:35–14:50 | **Trading** | Setups, entries, trade management |
| **14:50** | **Close-out** | No new entries. Sell every **in-the-money** option (mid, then bid). OTM options are left to expire. |
| 15:00 | Close | Options stop trading. OTM options expire worthless. |
| 15:15 | **Report and shutdown** | Write the daily HTML report and shut down |

**Half-days (12:00 CT close):** close-out at **11:50 CT**.

> **Exercise risk at the boundary:** an option that is just out of the money
> at 14:50 can still finish in the money at 15:00. Section 13 lists this as an
> open item, with a suggested fix.

---

## 6. Signal logic

### 6.1 Trend: 15-minute, price vs VWAP
- **Up** (calls only) when the last completed 15-min bar closed **above** the
  session VWAP.
- **Down** (puts only) when it closed **below**.
- VWAP is anchored at 8:30 CT and built from the 1-min bars.

### 6.2 Pullback: 5-minute, touch of the 9 EMA
- In an uptrend, a 5-min bar whose **low** touches or crosses the 9 EMA
  (calculated on 5-min closes, warmed up from the prior days) is a
  **pullback bar**.
- In a downtrend, a 5-min bar whose **high** touches or crosses the 9 EMA.
- The newest pullback bar sets the **trigger level**: its high (calls) or
  low (puts).

### 6.3 Trigger: 1-minute close beyond the pullback bar
- **Calls:** a 1-min bar closes **above** the pullback bar's high.
- **Puts:** a 1-min bar closes **below** the pullback bar's low.
- The setup **expires** if no trigger comes within **2 five-minute bars**
  (10 minutes) after the pullback bar, or if the 15-min trend flips first.

```
15m trend UP ──► 5m bar touches EMA9 ──► watch pullback-bar high ──► 1m close above it ──► SIGNAL (call)
                        │                         │
                        └── new pullback bar ─────┘ (resets the level)
                                                  └── 10 min pass or trend flips ──► setup expires
```

### 6.4 Direction rule (re-entry)
- After a trade on an ETF, new entries on that ETF must be in the **same
  direction**.
- That restriction lifts when the 15-min trend **flips**: a 15-min bar closes
  on the other side of VWAP.

### 6.5 Signals the bot cannot take
Every signal that is rejected goes to the **shadow log** (Section 9.2), with
its reason:
- no option fits ($20 at ≥ 0.10 delta);
- not enough settled cash;
- 3-trade cap reached;
- ETF already holding a position;
- direction rule;
- halt pause;
- ETF disabled.

---

## 7. Contract selection and orders

### 7.1 Picking the option
For each signalling ETF:
1. Take today's (0DTE) chain: calls for an uptrend, puts for a downtrend.
2. Keep contracts with **bid ≥ $0.05**.
3. Get delta from Webull. If Webull didn't provide it, **approximate it from
   moneyness** (distance from strike in standard deviations of the remaining
   day's expected move).
4. Affordability: `contracts = floor(budget / (ask × 100))`, which must be
   ≥ 1. Today's budget is $20.
5. Prefer the affordable contract **closest to 0.35 delta**, within
   0.30–0.40. If none is affordable there, take the **highest-delta**
   affordable contract, provided it's **≥ 0.10**. Otherwise there's no fit.

**Across ETFs ("cheapest fit"):** when more than one ETF signals on the same
1-min close, the bot trades the one whose chosen contract has the **highest
delta**. Ties go to the ETF whose signal arrived first. Others are logged as
shadow signals, or may trade separately if the caps still allow.

### 7.2 Buying
1. Place a **limit order at the current ask**.
2. **Ask jumped** between signal and order (fast market): re-run selection,
   which may pick a different strike that still fits $20 at ≥ 0.10 delta.
3. **Not filled** within a few seconds (default 5 s): cancel, **wait for the
   cancel confirmation**, re-read the ask, re-select if needed, and **try
   once more**. A second failure skips the trade and logs it to the shadow
   log.

Waiting for the cancel confirmation matters. A cancel and a fill can cross,
and sending a second order before knowing the first one's final state can
double the position.

### 7.3 Selling
| Exit reason | Order |
|---|---|
| Profit exit (option above entry) | Limit at **mid** for a few seconds (default 5 s), then the **bid** |
| Losing exit (salvage rule, or ITM close-out on a loser) | Limit at the **bid** |
| ITM close-out at 14:50, in profit | Mid, then bid |
| Close-all button | Bid |

---

## 8. Managing the open position

```
                 ┌───────────────────────────────────────────────┐
                 │ Option in profit (bid > entry)?               │
                 └───────────────┬───────────────────────────────┘
                     yes         │          no
          ┌──────────────────────┘          └─────────────────────────┐
          ▼                                                           ▼
 Gain ≥ +100%?                                         Trend break (5m close through EMA9)?
   ├─ no  → exit on a 5-min close through EMA9            ├─ no  → hold
   └─ yes → exit on a 1-min close through EMA9            └─ yes → option worth ≥ 50% of entry?
          (mid, then bid)                                           ├─ yes → sell at the bid (salvage)
                                                                    └─ no  → hold to expiry
 Always: at 14:50 CT, sell if ITM; let OTM expire.
```

- **Trend break:** for calls, a close **below** the 5-min 9 EMA; for puts, a
  close **above** it.
- **Tightened trail:** once the option has gained at least 100%, the exit
  check moves from the 5-min close to the **1-min** close through the 9 EMA.
  This locks in more of a fast spike.
- "Option worth" is measured with the **bid**, which is what a sale would
  actually get.
- **Adopted positions** (found at startup, not opened by wall2) get the same
  rules but **don't count** toward the 3-trade cap or the one-per-ETF rule.

---

## 9. Cash, records and reporting

### 9.1 Cash and settlement ledger
- At pre-open, the bot reads **settled cash** from Webull.
- It tracks each sale's settlement date (T+1, skipping weekends and
  holidays), so it always knows what cash becomes available when.
- A trade requires `premium + fees ≤ settled cash − cash already committed
  today`. Otherwise it is skipped and shadow-logged.
- The dashboard shows settled cash, unsettled proceeds, and the budget left
  for today.

### 9.2 Shadow log (skipped signals with outcomes)
For each skipped signal, the bot picks the contract it **would** have bought.
It then **streams that contract's quotes** and applies the full exit rules to
it: the trail, the salvage rule, and the 14:50 close-out. The result,
assuming entry at the ask and exit at the bid, goes into the database.

This answers questions like "is the 0.10 delta floor costing us winners?" and
"what do the trades skipped by the 3-trade cap actually do?". Streaming limits
on the Webull API are checked in M1. If they are tight, the newest shadow
contracts take priority.

### 9.3 Storage (kept forever)
| Store | Contents |
|---|---|
| `wall2/data/wall2.db` (SQLite) | Signals, selections, risk decisions, orders, fills, positions, shadow results, cash ledger, settings changes |
| `wall2/data/bars/` (Parquet) | 1-min bars for SPY, QQQ, IWM per day |
| `wall2/data/shots/` (PNG) | One chart per trade: 5-min bars, VWAP, 9 EMA, pullback bar, trigger, entry and exit |
| `wall2/logs/` | Structured JSON logs |

**Fees:** `wall2/config/fees.yaml` holds per-contract fees from your Webull
statement. They're included in all P&L.

### 9.4 Daily HTML report (15:15 CT)
Saved to `wall2/reports/YYYY-MM-DD.html` and openable from the dashboard:
- **Trades and P&L:** each trade with ETF, call/put, strike, delta, entry and
  exit, the reason for each, net P&L, and its screenshot.
- **Shadow signals:** skipped signals, the reason, and their simulated
  outcome.
- **Cash and settlement:** settled cash, unsettled proceeds, and tomorrow's
  available budget.
- **Running stats:** win rate, average win and loss, expectancy, split by
  ETF, call vs put, and time of day.

---

## 10. Webull integration

> To verify in **M1**, on your PC. Webull's documentation wasn't checked
> during design, and broker sites were partly unreachable from the cloud
> environment.

M1 must confirm, using the official Webull OpenAPI:
1. **Access:** how to apply, plus the app key/secret and token flow.
2. **Options:** fetching **0DTE chains** for SPY, QQQ and IWM, with bid/ask
   and greeks.
3. **Orders:** placing and cancelling **single-leg option limit orders**,
   order status updates, and fills with fees.
4. **Account:** positions, **settled vs unsettled cash** in a cash account.
5. **Streaming:** real-time quotes for the underlyings and for option
   contracts, and how many symbols can be subscribed at once.
6. **Paper trading:** whether the API supports a paper account. If not, the
   **built-in simulator** is used (buy at ask, sell at bid, against live
   quotes).
7. **Rate limits**, and how token expiry is handled.

**If something essential is missing,** for example no 0DTE option orders, the
fallback is **decided at that point**. The `Broker` interface keeps any
choice open: another broker, or signals-only.

**Disconnects:** reconnect with backoff, reconcile positions and orders with
Webull, then resume managing. The premium already caps the loss, so no
emergency selling is needed.

**Halts and frozen quotes:** if an underlying's quotes stop updating, new
entries on it pause. Entries resume after quotes have been healthy for 2
minutes. Open positions keep being managed.

---

## 11. Dashboard (PySide6, same process)

```
┌───────────────────────────────────────────────────────────────────────────┐
│ [PAPER] 10:42 CT  Trades 1/3  Settled $4,812  Budget $20   [PAUSE] [CLOSE ALL] │
├──────────────────────────────────────────────┬────────────────────────────┤
│ Chart: [SPY] [QQQ] [IWM]                      │ Positions                  │
│  5-min candles · VWAP · EMA9 · pullback bar · │  IWM 0DTE 218C ×1 @ 0.19   │
│  trigger level · entry/exit markers           │  bid 0.31 (+63%)  Δ 0.24   │
│  15-min trend badge: UP ▲                     │  exit: 5m close < EMA9     │
│                                               ├────────────────────────────┤
│                                               │ ETFs: SPY ✓  QQQ ✓  IWM ✓   │
│                                               │ Direction lock: IWM calls  │
├───────────────────────────────────────────────┴────────────────────────────┤
│ Journal: time · ETF · C/P · strike · Δ · entry · exit · reason · P&L · 📷 · shadow ▸ │
└───────────────────────────────────────────────────────────────────────────┘
```

| Control | Effect |
|---|---|
| Pause / Resume | Stop or allow new entries; open trades are still managed |
| Close all now | Sell every open option at the bid (with confirmation) |
| ETF toggles | Enable or disable SPY, QQQ, IWM |
| Budget edit | Change the per-trade premium budget (logged) |

---

## 12. Codebase

```
wall2/
├── pyproject.toml            # uv, Python 3.12
├── .env.example              # WEBULL_APP_KEY, WEBULL_APP_SECRET, ... (names to confirm in M1)
├── config/
│   ├── wall2.yaml            # all settings (Section 12.1)
│   └── fees.yaml
├── src/wall2/
│   ├── app.py                # qasync loop, CLI: run / check / report
│   ├── core/                 # events, clock (CT), types
│   ├── broker/               # Broker interface, webull adapter, simulator
│   ├── data/                 # streaming, bar builder, indicators (VWAP, EMA), chain service
│   ├── signals/              # trend, pullback, trigger, setup state machine
│   ├── selection/            # contract selector, delta fallback, cheapest-fit
│   ├── risk/                 # risk gate, settlement ledger
│   ├── execution/            # order manager, position manager (trail, salvage, close-out)
│   ├── shadow/               # shadow tracker
│   ├── persistence/          # sqlite, parquet, screenshots
│   ├── reports/              # daily HTML
│   └── ui/                   # PySide6 dashboard
└── tests/                    # core tested: risk, settlement, orders, signals
```

**Testing (core tested):** unit and property tests cover the parts where a
bug costs money:
- **Settlement:** T+1 across weekends and holidays; never spends unsettled
  cash.
- **Risk gate:** the 3/day cap, one per ETF, the direction lock and when it
  lifts.
- **Order manager:** limit at ask, re-price once, and the cancel/fill race
  never producing a double position.
- **Position manager:** trail vs tightened trail, the salvage threshold,
  14:50 ITM close-out, half-day 11:50.
- **Signals:** fixture bar sequences that must trigger, and must not trigger,
  each setup step.
- **Contract selection:** delta targeting, the floor, budget math, and the
  moneyness fallback.

These run against the **simulator** broker. UI and plumbing get lighter
coverage.

### 12.1 Configuration (`config/wall2.yaml`)
```yaml
timezone: America/Chicago
underlyings: [SPY, QQQ, IWM]
mode: paper                     # paper | live (paper = Webull paper or built-in simulator)

session:
  first_entry: "08:35"
  closeout: "14:50"             # half-days: "11:50"
  report_at: "15:15"
  autostart: "08:00"

signals:
  trend_tf: 15m                 # price vs VWAP
  entry_tf: 5m                  # touch of EMA
  ema_len: 9
  trigger_tf: 1m                # close beyond pullback bar
  setup_ttl_bars: 2             # 5-min bars
  warmup_days: 3

selection:
  budget_usd: 20
  target_delta: [0.30, 0.40]
  min_delta: 0.10
  min_bid: 0.05
  delta_source: webull          # fallback: moneyness

orders:
  entry: limit_at_ask
  fill_wait_sec: 5
  reprice_attempts: 1
  profit_exit_mid_wait_sec: 5

management:
  trail_tf: 5m
  tighten_after_gain_pct: 100
  tightened_trail_tf: 1m
  salvage_min_value_pct: 50

risk:
  max_trades_per_day: 3
  one_position_per_etf: true
  settled_cash_only: true
  halt_resume_healthy_sec: 120
```

---

## 13. Open items and known risks

1. **Near-the-money exercise risk (recommended fix).** An OTM option left to
   expire at 14:50 can move into the money by the 15:00 close. Prices can
   also move after hours before the exercise decision. In a cash account,
   the broker would then have to step in. **Suggestion:** at 14:50, also sell
   options within a small distance of the strike (e.g. $0.50 on SPY/QQQ,
   $0.25 on IWM), not just ITM ones. They're cheap by then, so this costs
   little. Needs your decision.
2. **The odds of cheap OTM 0DTE options.** At $20, many trades will be
   0.10–0.25 delta. By delta alone, most will expire worthless. The strategy
   only works if the winners are large enough to pay for the many total
   losses. The shadow log and running stats will show whether they are.
3. **Worst-case month.** 3 trades × $20 × ~21 trading days is up to about
   **$1,260 a month**, or 25% of a $5k account, with no longer-term stop.
   You chose no extra limits. Consider at least watching the expectancy in
   the running stats.
4. **Webull API unknowns** (Section 10): 0DTE support, settled-cash fields,
   streaming limits, and paper trading.
5. **0DTE availability:** SPY and QQQ list same-day expirations every
   weekday. Confirm IWM's schedule. The pre-open chain check disables any ETF
   without a same-day expiry either way.
6. **Delta fallback quality:** the moneyness approximation is rough. If
   Webull often omits greeks, a Black-Scholes calculation would be more
   accurate.
7. **Still open from the Q&A:** more topics may be added as questions
   continue.

---

## 13a. Implementation notes (interpretations made while building)

These came up in code where your answers didn't fully specify the behavior. Each one is easy
to change.

| Topic | What the code does | Why |
|---|---|---|
| Trend before 8:45 CT | Until the first 15-min bar completes, the trend is the latest **1-min close vs VWAP** (provisional) | You chose first entry at 8:35, before any 15-min bar exists |
| Direction lock | Lifts when **any** completed 15-min bar closes on the other side of VWAP | Matches "until the trend flips across VWAP" |
| Tightened trail (after +100%) | **1-min closes** checked against the **5-min 9 EMA** | One EMA for both rules; the tighter rule just checks it more often |
| Delta fallback | Moneyness delta = Φ((S−K)/σ_move), with σ_move taken from the ATM straddle (≈ 0.8·σ), else from a configured annual volatility | Uses the market's own expected move when available |
| Cheapest fit | Applies to signals on the **same 1-min close**; after the best one, others still trade if caps and cash allow | "Cheapest fit" is about choosing among simultaneous signals |
| Shadow log | Skipped signals are shadow-tracked only when a contract was selectable; others are logged with the reason only | There's no "would-be" option to follow otherwise |
| Exits with no bid | Contracts with no bid stay open and expire worthless | A worthless option can't be sold |
| Stray positions | Adopted only on SPY, QQQ, IWM (the bot has no chart data for anything else); others are logged and ignored | Trailing rules need the underlying's bars |
| Order stuck after cancel | Trading pauses and an event is logged | A position of unknown size must not be traded around |

**Simulation finding:** in synthetic sessions (model prices with 18% volatility), $20 rarely
bought a SPY or QQQ option at or above the 0.10 delta floor; IWM usually was the only fit.
Real prices will differ, but expect IWM to dominate at this budget.

---

## 14. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 ✅ | Skeleton, config, CT clock, calendar, simulator broker | Tests run; `wall2 check` works |
| M1 | **Webull API verification** (on your PC): chains, greeks, orders, cash, streaming, paper | All items in Section 10 answered; fallback decided if needed |
| M2 | Data: streaming, bars, VWAP/EMA, warmup | Indicators match a reference chart |
| M3 ✅ | Signals and contract selection | Fixture tests pass; signals visible in logs |
| M4 ✅ | Risk gate, settlement ledger, order and position managers | Core tests pass on the simulator |
| M5 | Dashboard, screenshots, shadow tracker, daily report | Full paper day runs unattended |
| M6 | 4 weeks of paper trading | Your go-live decision |
| M7 | Live at $20 per trade | — |
