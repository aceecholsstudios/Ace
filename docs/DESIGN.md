# Ace: Design Document for an Automated Micro Equity Index Futures Bot

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Author** | Ace Echols Studios |
| **Last updated** | 2026-09-28 |
| **Broker / API** | Tradovate (REST + WebSocket) |
| **Instruments** | MNQ (Micro Nasdaq-100), MES (Micro S&P 500), M2K (Micro Russell 2000) |
| **Primary goal** | Income generation: steady intraday P&L with tight drawdown control |

---

## 1. Summary

Ace is a standalone Python process that trades CME micro equity index futures
through the Tradovate API. It runs unattended while the futures market is open.
All trading logic (signals, sizing, risk, order management) lives inside the bot.
Tradovate supplies market data, routes orders, and holds protective brackets on
its servers.

The design follows three principles:

1. **The broker is the source of truth.** The bot keeps a local model of orders
   and positions, but it reconciles that model against Tradovate constantly and
   treats any disagreement as an emergency.
2. **Every open position is protected on the server.** Each entry goes out as a
   bracket (entry + stop + target). If the bot crashes, loses its connection, or
   the host dies, the stop is already working at Tradovate.
3. **One codebase for research and live trading.** Strategies never call the API
   directly. They see an abstract clock, a data feed, and a broker, so the same
   strategy code runs in backtest, Tradovate market replay, demo, and live.

---

## 2. Goals and non-goals

### Goals
- Trade MNQ, MES and M2K intraday, following an explicit session policy
  (Section 5).
- Aim for consistent daily results. Risk limits come first: a daily loss cap,
  a per-trade risk budget, and portfolio exposure limits that account for
  correlation.
- Stay flat outside allowed trading windows. By default the bot is flat before
  the day-margin cutoff, so it never pays overnight margin or carries gap risk.
- Run unattended: reconnect by itself, renew tokens, handle contract rolls and
  exchange holidays, alert a human on anomalies.
- Keep a full audit trail. Every decision, order, fill and risk veto is
  persisted with the inputs that produced it.

### Non-goals (v1)
- Latency-sensitive scalping or HFT. Python over a retail API is not built for
  it, and micro contract fees make it uneconomic (Section 7.4).
- Instruments other than the three micros.
- Holding positions overnight or over weekends.
- A GUI. Monitoring happens through logs, metrics and alerts. A read-only
  dashboard can come later.
- Discretionary override inside the bot. A human can only **pause**,
  **flatten**, or **kill** it (Section 11.4).

---

## 3. Instruments

| Symbol | Underlying | Multiplier | Tick size | Tick value | 1-point value |
|---|---|---|---|---|---|
| **MES** | S&P 500 | $5 × index | 0.25 | $1.25 | $5.00 |
| **MNQ** | Nasdaq-100 | $2 × index | 0.25 | $0.50 | $2.00 |
| **M2K** | Russell 2000 | $5 × index | 0.10 | $0.50 | $5.00 |

**Contract months:** quarterly (H = Mar, M = Jun, U = Sep, Z = Dec). On
2026-09-28 the front month is **Z6** (Dec 2026), so the symbols are `MESZ6`,
`MNQZ6` and `M2KZ6`.

**Expiration:** the third Friday of the contract month, at the 9:30 ET open (a
special opening quotation). Liquidity moves to the next contract about 8 days
earlier, around the Thursday before expiration week. The bot rolls on a
configurable schedule (Section 9.3).

### 3.1 The three contracts are one bet
The three index futures are highly correlated. Intraday return correlation is
usually 0.8–0.95. Long MES + long MNQ + long M2K is essentially one large long
position in US equities, not three independent trades. This fact shapes the
risk design (Section 8.3):

- Exposure is measured in **beta-weighted dollars** (normalized to MES), not
  contract counts.
- By default, when several symbols signal the same direction at once, the bot
  takes only the best candidate (relative-strength selection, Section 7.3).

---

## 4. System architecture

### 4.1 Component overview

```
                       ┌──────────────────────────────────────────────┐
                       │                  Ace process                 │
                       │                (Python asyncio)              │
┌──────────────┐       │                                              │
│  Tradovate   │  WS   │  ┌────────────┐     ┌──────────────────┐     │
│ Market Data  ├───────┼─►│ MarketData │────►│   Bar Builder     │     │
│  WebSocket   │       │  │  Service   │     │ (tick → 1m/5m…)   │     │
└──────────────┘       │  └────────────┘     └────────┬─────────┘     │
                       │                              │ Bar/Quote     │
                       │                              ▼ events        │
                       │  ┌────────────┐     ┌──────────────────┐     │
                       │  │  Session   │────►│  Strategy Engine  │     │
                       │  │ Scheduler  │gates│ (N strategies)    │     │
                       │  │ + Calendar │     └────────┬─────────┘     │
                       │  └─────┬──────┘              │ TradeIntent   │
                       │        │                     ▼               │
                       │        │            ┌──────────────────┐     │
                       │        └───────────►│   Risk Manager    │     │
                       │   flatten/halt      │ (pre-trade, sizing│     │
                       │                     │  limits, kill sw.)│     │
                       │                     └────────┬─────────┘     │
                       │                              │ ApprovedOrder │
                       │                              ▼               │
┌──────────────┐       │  ┌────────────┐     ┌──────────────────┐     │
│  Tradovate   │  WS   │  │  Account   │◄───►│  Execution / OMS  │     │
│ Trading API  │◄──────┼─►│  State +   │     │ (bracket orders,  │     │
│ (user sync,  │ REST  │  │ Reconciler │     │  lifecycle FSM)   │     │
│  orders)     │       │  └────────────┘     └──────────────────┘     │
└──────────────┘       │                                              │
                       │  ┌────────────┐ ┌──────────┐ ┌───────────┐   │
                       │  │ Persistence│ │ Metrics/ │ │  Alerts   │   │
                       │  │ (SQLite/PG)│ │  Logs    │ │(Telegram…)│   │
                       │  └────────────┘ └──────────┘ └───────────┘   │
                       └──────────────────────────────────────────────┘
```

### 4.2 Why asyncio, single process
- The workload is I/O-bound: two WebSockets, occasional REST calls, disk writes.
  Strategy math on 1–5 minute bars for three symbols takes microseconds.
- One event loop keeps the ordering of events **deterministic**. A fill, a bar
  close and a risk check never race each other across threads. This matters
  most for correctness and for replaying a day's events exactly.
- CPU-heavy work, if any appears later (e.g. model inference), goes to a
  `ProcessPoolExecutor` so it never blocks the loop.

### 4.3 Internal event bus
Components talk through typed, immutable events on an in-process bus (a small
publish/subscribe layer over `asyncio.Queue`). Event types:

| Event | Producer | Consumers |
|---|---|---|
| `Quote(symbol, bid, ask, last, ts)` | MarketData | BarBuilder, Risk (stale-data watchdog), OMS |
| `Bar(symbol, tf, o,h,l,c,v, ts)` | BarBuilder | Strategies, Risk (volatility) |
| `SessionChanged(state)` | Scheduler | Strategies, Risk, OMS |
| `TradeIntent(...)` | Strategy | Risk |
| `OrderRequest(...)` | Risk | OMS |
| `OrderUpdate / Fill / PositionUpdate` | Account sync | OMS, Risk, Strategies, Persistence |
| `Halt(reason, level)` | Risk / Watchdog / Operator | Everything |

Every event is appended to an **event journal**. Replaying the journal through
the same components reproduces every decision the bot made, which is the most
useful tool for debugging after an incident.

---

## 5. Session model: "trade while the market is open"

### 5.1 CME equity index futures hours (all times ET)
- Globex trades **Sunday 18:00 through Friday 17:00**, with a daily
  **17:00–18:00 maintenance halt**.
- Regular trading hours (RTH), which match the cash equity session, run
  **09:30–16:00**. Most liquidity and the cleanest intraday structure occur
  here.
- Holidays and early closes follow the CME holiday calendar (e.g. an early halt
  on the day after Thanksgiving). The bot uses `exchange_calendars` (the
  `CMES` calendar) plus a manually maintained override file for exceptions.

Internally, all timestamps are **UTC**. Session logic converts to
`America/New_York` (or `America/Chicago`, the exchange's own zone) through
`zoneinfo`, so DST changes are handled correctly.

### 5.2 Session state machine

```
 CLOSED ──(T-15m)──► WARMUP ──(open)──► ETH ──(09:30)──► RTH_OPENING ──(09:45)──► RTH
   ▲                                                                              │
   │                                                                          (15:45)
   │                                                                              ▼
   └──(17:00 halt / Fri close / holiday)── POST_RTH ◄──(16:00)── WIND_DOWN ◄───────┘
```

| State | Default behavior |
|---|---|
| `CLOSED` | No trading. Market-data sockets may disconnect; tokens are still renewed. |
| `WARMUP` | Connect, authenticate, reconcile account, load history, rebuild indicators. |
| `ETH` | **Disabled by default.** When enabled: half size, ETH-specific strategies only. |
| `RTH_OPENING` | Build the opening range. Only strategies that declare `trades_open=True` may act. |
| `RTH` | Main trading window. |
| `WIND_DOWN` | No new entries. Existing positions are managed to exit. |
| `POST_RTH` | Flatten all positions by the configurable cutoff (default 15:55 ET), cancel all orders, confirm flat. |

**Why flat by default:** Tradovate's reduced **day-trade margin** for micros
(often around $50 per contract, depending on account type) applies only during
set hours. Holding past the broker's cutoff requires full exchange initial
margin, which is many times larger. An overnight position also carries gap
risk that an intraday stop cannot limit. For an income-focused bot, flat
overnight is the safe default. Check Tradovate's current margin schedule, since
the cutoff time and amounts change.

### 5.3 Event blackouts
A calendar of scheduled high-impact releases blocks new entries from T-2 min to
T+5 min (configurable) and can tighten stops on open positions. Covered
releases: CPI, NFP, FOMC decision and press conference, PCE, GDP, and major
Treasury refunding. Source: a maintained YAML file, optionally fed by an
economic-calendar API.

---

## 6. Tradovate integration

> Endpoint names and payloads below reflect Tradovate's public API as
> understood at the time of writing. The integration layer must be verified
> against the current docs (`api.tradovate.com`) and tested in the **demo**
> environment before any live use.

### 6.1 Prerequisites
- A funded Tradovate account with the **API Access** add-on enabled.
- API credentials (`cid` and `sec`) created in Tradovate's API key management
  screen.
- CME market data entitlement. Non-professional status keeps fees low.
  API-delivered data may need its own subscription.
- Environments:
  - Demo REST: `https://demo.tradovateapi.com/v1`
  - Live REST: `https://live.tradovateapi.com/v1`
  - Trading WS: `wss://demo.tradovateapi.com/v1/websocket` or `wss://live.tradovateapi.com/v1/websocket`
  - Market data WS: `wss://md.tradovateapi.com/v1/websocket`
  - Replay WS (market replay for testing): `wss://replay.tradovateapi.com/v1/websocket`

### 6.2 Authentication and token lifecycle
1. `POST /auth/accesstokenrequest` with `name`, `password`, `appId`,
   `appVersion`, `cid`, `sec`, `deviceId` (a stable UUID stored locally). The
   response contains `accessToken`, `mdAccessToken`, `expirationTime`, and
   `userId`.
2. Tokens last roughly 90 minutes. The `AuthManager` renews through
   `GET /auth/renewaccesstoken` when about 15 minutes remain, then updates
   every consumer. Renewal does not create a new session; repeatedly requesting
   new tokens does and can trigger rate limits.
3. Secrets come from environment variables or a secret manager. They are never
   stored in config files or logs. `deviceId` is stored so Tradovate always sees
   one consistent device.

### 6.3 WebSocket protocol (both sockets)
Tradovate uses a SockJS-style framing:

| Frame from server | Meaning |
|---|---|
| `o` | Socket open. Client must send `authorize\n0\n\n<token>` next. |
| `h` | Server heartbeat. |
| `a[...]` | JSON array of messages: responses (`{"i": id, "s": status, "d": data}`) or events (`{"e": "props" / "md" / "chart" ..., "d": ...}`). |
| `c[code, reason]` | Server is closing the socket. |

Client requests are text frames of the form `endpoint\nrequestId\nquery\nbody`.

The `TradovateSocket` class handles:
- **Request/response correlation:** a monotonically increasing `requestId`
  mapped to an `asyncio.Future` in a dict. Each request has a timeout.
- **Heartbeats:** the client sends `[]` about every 2.5 s. If no server frame
  arrives for more than 10 s, the socket is declared dead.
- **Reconnect:** exponential backoff with jitter (1, 2, 4 … 30 s). After a
  reconnect: re-authorize, re-subscribe, then **reconcile** (Section 9.2)
  before trading resumes.
- **Rate-limit penalties:** Tradovate can answer with a penalty (`p-ticket`,
  `p-time`, sometimes `p-captcha`). The client waits `p-time` seconds, then
  retries with the ticket. A captcha response is a hard halt that needs a
  human.

```python
# Sketch of the core send/receive loop
class TradovateSocket:
    async def request(self, endpoint: str, body: dict | None = None,
                      query: str = "", timeout: float = 5.0) -> dict:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps(body) if body is not None else ""
        await self._ws.send(f"{endpoint}\n{rid}\n{query}\n{payload}")
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    def _on_frame(self, raw: str) -> None:
        kind, rest = raw[0], raw[1:]
        if kind == "a":
            for msg in json.loads(rest):
                if "i" in msg and msg["i"] in self._pending:
                    self._pending[msg["i"]].set_result(msg)
                elif "e" in msg:
                    self._bus.publish_raw(msg["e"], msg["d"])
        elif kind == "h":
            self._last_heartbeat = time.monotonic()
        elif kind == "c":
            self._schedule_reconnect()
```

### 6.4 Market data
- `md/subscribeQuote {symbol}` streams best bid/ask, last and volume. It feeds
  the stale-data watchdog and the OMS (for slippage accounting).
- `md/getChart` with `chartDescription: {underlyingType: "Tick", elementSize: 1,
  elementSizeUnit: "UnderlyingUnits"}` streams tick-level trades. The
  **BarBuilder** aggregates them into 1-minute bars (and 5-minute, etc.). The
  bot builds its own bars from ticks so bar boundaries and VWAP are computed
  exactly the same way in live trading and in backtests.
- On warmup, `md/getChart` with `MinuteBar` and a `timeRange` backfills enough
  history (e.g. 20 sessions of 1-minute bars) to seed indicators such as ATR
  and opening-range percentiles.
- `md/subscribeDOM` is optional and off in v1. Order-book strategies are out of
  scope.

### 6.5 Account state: user sync
`user/syncrequest {users: [userId]}` on the trading socket returns an initial
snapshot (accounts, positions, orders, fills, cash balances) and then streams
`props` events as entities are created or updated. The `AccountState`
component:
- Keeps the canonical in-memory copy of orders, fills, positions and cash
  balance.
- Publishes typed `OrderUpdate`, `Fill` and `PositionUpdate` events.
- Is the **only** writer of position state. Strategies and risk read it; they
  never assume a fill happened because an order was sent.

### 6.6 Order placement
Every automated order sets **`isAutomated: true`** (CME requires automated
orders to be tagged). Entries go out as **OSO brackets** through
`order/placeOSO`:

```json
{
  "accountSpec": "<account name>",
  "accountId": 123456,
  "action": "Buy",
  "symbol": "MESZ6",
  "orderQty": 3,
  "orderType": "Limit",
  "price": 5712.25,
  "isAutomated": true,
  "bracket1": { "action": "Sell", "orderType": "Stop",  "stopPrice": 5706.25 },
  "bracket2": { "action": "Sell", "orderType": "Limit", "price": 5724.25 }
}
```

The two bracket legs form an OCO pair: when one fills, Tradovate cancels the
other. Because Tradovate holds this linkage server-side, **the position stays
protected even if Ace disappears.**

Other operations:
- `order/modifyorder` for stop trailing or moving the stop to breakeven.
- `order/cancelorder` for unfilled entries after a timeout.
- `order/liquidateposition` for emergency flattening (cancels working orders
  and exits at market).

---

## 7. Strategy layer

### 7.1 Strategy interface
Strategies are pure decision functions. They get state and events, and they
return **intents**. They never place orders, size positions, or know about
Tradovate.

```python
class Strategy(Protocol):
    name: str
    symbols: tuple[str, ...]          # subset of {"MES", "MNQ", "M2K"}
    timeframes: tuple[str, ...]       # e.g. ("1m", "5m")
    active_states: frozenset[SessionState]

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[TradeIntent]: ...
    def on_fill(self, fill: Fill, ctx: StrategyContext) -> list[TradeIntent]: ...
    def on_session(self, state: SessionState, ctx: StrategyContext) -> list[TradeIntent]: ...

@dataclass(frozen=True)
class TradeIntent:
    strategy: str
    symbol: str                       # root, e.g. "MES"; OMS resolves to MESZ6
    side: Side                        # LONG / SHORT / FLAT
    entry: EntrySpec                  # market | limit@price | stop@price, + expiry
    stop_price: float                 # REQUIRED; no stop, no trade
    target_price: float | None
    confidence: float = 1.0           # 0..1, used for size scaling and ranking
    reason: str = ""                  # human-readable, persisted
```

`StrategyContext` gives read-only access to indicators, the current position,
session information and today's P&L. **The required `stop_price` is the most
important design choice in this layer.** A stop is part of the trade idea
itself, and risk-based sizing (Section 8.2) is impossible without one.

### 7.2 Starter strategy set
These are well-understood intraday structures chosen as a starting point to
validate the machinery. They are **hypotheses**, not proven edges. Each must
pass the validation process in Section 10 before it trades real money.

#### S1: Opening Range Breakout with a volatility filter (RTH trend days)
- **Opening range (OR):** high and low from 09:30 to 09:45 ET.
- **Filter:** trade only if OR width falls between the 20th and 80th percentile
  of its last 20 sessions. A very narrow range often leads to false breakouts;
  a very wide range means the day's move may already be spent.
- **Entry:** a 5-minute bar closes beyond the OR, and price is on the same side
  of session VWAP. Enter with a stop-limit one tick beyond the bar's extreme.
- **Stop:** the OR midpoint or 1.0 × ATR(14, 5m), whichever is tighter.
- **Exit:** 50% at 1.5R, then trail the rest behind the last completed 5-minute
  swing. Close any remainder at WIND_DOWN.
- **Frequency:** at most one attempt per direction per day, and at most one
  symbol per direction (Section 7.3).

#### S2: VWAP reversion (range days, midday)
- **Active:** 10:30–15:00 ET, only when the day has **not** been classified as
  a trend day (e.g. price has crossed VWAP at least N times, or the
  ADX-equivalent is below a threshold).
- **Entry:** price closes outside the ±2σ session VWAP band, and the next bar
  shows rejection (closes back inside the band).
- **Stop:** beyond the ±3σ band or the rejection bar's extreme.
- **Target:** session VWAP.
- **Guardrail:** disabled for the rest of the day once S1 has triggered and
  worked. The two strategies express opposite views of the same day, and a
  trend-day classification should silence mean reversion.

#### S3 (later): Overnight-range or gap strategies for ETH
Left out of v1. ETH liquidity in the micros is thin enough that slippage
assumptions need their own study.

### 7.3 Choosing among correlated instruments
When a strategy produces the same directional intent on more than one symbol
within a short window, the **Signal Arbiter** keeps one of them:
- For longs, prefer the symbol with the strongest relative performance since
  the open, measured in ATR units rather than percent. For shorts, prefer the
  weakest.
- Ties go to the symbol with the tighter spread relative to the stop distance
  (usually MES).

The idea is to express a directional view in the index where it is
strongest, rather than tripling one bet.

### 7.4 Cost economics of micros
Commission plus exchange and NFA fees on micros run roughly **$0.50–$1.50 per
side** depending on the Tradovate plan. Compare that with the tick values:

| | MES | MNQ | M2K |
|---|---|---|---|
| Tick value | $1.25 | $0.50 | $0.50 |
| Round-trip fees (~$1.50 est.) in ticks | ~1.2 | ~3.0 | ~3.0 |
| + 1 tick slippage on stop exits | 2.2 ticks | 4.0 ticks | 4.0 ticks |

Fees alone can cost several ticks per trade on MNQ and M2K. As a rule, **the
average win must be large relative to costs.** Target at least 20 ticks on
MNQ/M2K and 8 on MES. This is why v1 rules out scalping and why every backtest
must include realistic fees and slippage. `config/fees.yaml` holds the real
numbers from the Tradovate plan in use.

---

## 8. Risk management

Risk management is the most important component. It turns every intent into a
sized, approved order or a logged veto, and it can halt the whole system.

### 8.1 Pre-trade checks (in order; the first failure vetoes)
1. System not halted, and session state allows entries for this strategy.
2. No event blackout active.
3. Market data fresh: last quote under 3 s old during RTH, and the spread
   within normal range (≤ 2 ticks for MES, ≤ 4 for MNQ/M2K).
4. Account state reconciled within the last N seconds.
5. Daily loss limit not reached, and the worst case of this trade (a stop hit)
   would not breach it.
6. Per-symbol and portfolio exposure limits hold after the trade (Section 8.3).
7. Trade-count and consecutive-loss limits not reached.
8. Stop distance is sane: between 0.25× and 3× the current ATR.
9. Cost check: the expected target distance is at least `k` × round-trip cost.

### 8.2 Position sizing
Sizing is based on **fixed fractional risk**: the loss if the stop is hit
equals a set dollar amount, whatever the instrument or volatility.

```
risk_budget_$      = min(equity × risk_pct, max_risk_per_trade_$) × confidence
per_contract_risk  = |entry − stop| × point_value + slippage_allow_$ + round_trip_fees_$
contracts          = floor(risk_budget_$ / per_contract_risk)
contracts          = min(contracts, max_contracts[symbol])
if contracts == 0: veto ("stop too wide for budget")
```

**Worked example:** equity $10,000, risk 0.75% gives $75.
MES long at 5712.25 with stop 5706.25, a 6-point stop:
`6 × $5 = $30`, plus 1 tick of slippage ($1.25), plus about $1.50 in fees,
gives **$32.75 per contract**. Floor(75 / 32.75) = **2 contracts**.
The same trade on MNQ with a 24-point stop: `24 × $2 = $48 + $0.50 + $1.50 =
$50`, which gives 1 contract.

Sizing in dollars of risk, not contracts, is what makes three instruments with
different volatility comparable.

### 8.3 Portfolio and correlation limits
- **Beta-weighted exposure:** each position is converted to MES-equivalent
  notional:
  `exposure = qty × price × multiplier × β_vs_SPX`.
  β is estimated daily from 60 days of returns (MNQ is typically about 1.2,
  M2K about 1.1–1.3). The absolute net exposure is capped
  (e.g. ≤ 2× equity notional).
- **Correlated open risk:** the sum of open stop-risk across same-direction
  positions is capped (e.g. 1.5 × the single-trade budget).
- **Per-symbol caps:** maximum contracts per symbol, set in config.

### 8.4 Daily and session limits (the income-preservation layer)
| Limit | Default | Action |
|---|---|---|
| Daily max loss (realized + open) | 2% of equity (or a fixed $) | Flatten, halt until the next session |
| Daily profit lock (optional) | After +X%, cut risk_pct in half; after giving back 50% of peak, stop | Protects good days |
| Max consecutive losses | 3 | Pause 60 min, then half size for the rest of the day |
| Max trades per day | 6 | No new entries |
| Weekly max drawdown | 5% | Halt until a human re-enables |
| Account equity floor | Configured $ | Kill; manual restart required |

### 8.5 Kill switch and halt levels
| Level | Trigger examples | Effect |
|---|---|---|
| `PAUSE` | Consecutive losses, blackout, operator command | No new entries; existing brackets stay |
| `FLATTEN` | Daily loss limit, end of session, stale data for more than 30 s with an open position | Cancel all orders, liquidate positions, confirm flat |
| `KILL` | Position mismatch not resolved, repeated order rejects, equity floor, captcha penalty, unexpected exception in OMS/Risk | Flatten, then stop the process and page a human. Restart requires manual action. |

---

## 9. Execution and order management (OMS)

### 9.1 Order lifecycle state machine

```
 NEW ─► PENDING_SUBMIT ─► WORKING ─► PARTIALLY_FILLED ─► FILLED
             │               │               │
             ▼               ▼               ▼
         REJECTED        CANCELLED     CANCELLED (remainder)
             │               ▲
             └── (timeout) ──┘   EXPIRED (entry not filled within its TTL)
```

- Every bot order carries a **client order ID** (`ace-<strategy>-<uuid8>`),
  saved before sending. This connects Tradovate order IDs back to the intent
  that produced them.
- **Partial fills:** bracket quantities follow the filled quantity. Unfilled
  entries are cancelled at TTL, and the position runs with brackets sized to
  what actually filled.
- **Timeouts:** if a placement gets no response in 5 s, the OMS **does not
  resend**. It queries order state first. A blind resend is the classic way to
  double a position.
- **Breakeven and trail moves** are sent as `modifyorder` on the stop leg. The
  new stop is kept only after acknowledgment.

### 9.2 Reconciliation
Runs at startup, after every reconnect, and every 30 s during trading:
1. Pull positions, working orders and fills from Tradovate.
2. Compare them with local state.
3. Resolve differences:
   - **Unknown position, no local record:** by default, flatten and alert.
     (Config option: adopt it and attach a protective stop at X × ATR.)
   - **Local position that Tradovate does not show:** trust Tradovate, mark the
     local record closed, alert.
   - **Position without a working stop:** attach an emergency stop
     immediately, alert.
   - **Anything unresolved after 2 cycles:** `KILL`.

### 9.3 Contract roll
- `ContractResolver` maps root symbols (`MES`) to the active contract
  (`MESZ6`) using the expiration calendar and a roll offset (default: roll 8
  calendar days before expiration, at the start of the session).
- Since the bot is flat every night, rolling only means switching which
  contract new subscriptions and orders use. No spread trades are needed.
- Indicator history splices across the roll using ratio or difference
  back-adjustment, so ATR and levels do not jump.
- A pre-roll check confirms that the new contract resolves via `contract/find`
  and has streaming quotes before switching.

---

## 10. Research, backtesting and validation

### 10.1 Data
- **Historical:** Tradovate's chart API only offers limited history. For
  research, buy CME Globex tick or 1-minute data for MES, MNQ, M2K and
  (for longer history) ES, NQ, RTY. Vendors include Databento, FirstRate
  Data, and others. Micros launched in May 2019; before that, the e-minis are
  a proxy with the same price series and 10× the multiplier.
- **Storage:** Parquet files partitioned by `symbol/date`, read through
  Polars.
- **Live capture:** the bot records every tick it receives to Parquet. Over
  time this becomes a dataset that exactly matches what the live system saw.

### 10.2 Backtest engine
An event-driven engine that **reuses the production Strategy, Risk and
BarBuilder code** and swaps in:
- `SimClock` in place of the wall clock.
- `SimBroker` in place of the Tradovate OMS adapter, with this fill model:
  - Market and stop orders: fill at next trade price + N ticks of slippage.
    N is configurable per symbol and higher around the open and news.
  - Limit orders: fill only when price trades **through** the limit (a touch
    is not enough), which approximates queue position conservatively.
  - Fees from `fees.yaml`.

### 10.3 Validation procedure (required before live)
1. **In-sample development** on 2019–2023.
2. **Walk-forward optimization:** re-fit parameters on a rolling 12-month
   window and test on the following 3 months. Report only the stitched
   out-of-sample results.
3. **Parameter robustness:** results must hold across a neighborhood of
   parameter values. A sharp performance peak signals overfitting.
4. **Monte Carlo on the trade sequence:** resample trades to estimate the
   distribution of max drawdown and losing streaks. Set the daily and weekly
   limits from these numbers, not from intuition.
5. **Holdout:** the most recent 6+ months are touched only once, at the end.
6. **Tradovate Market Replay:** run the full live stack against replayed
   market days to exercise real API behavior.
7. **Demo (paper) trading:** at least 4–6 weeks. Compare live fills against
   the backtest's predicted fills for the same days; the gap is the real
   slippage.
8. **Live at minimum size:** 1 contract, with the smallest risk budget, for
   4+ weeks before scaling up.

**Go-live criteria (per strategy):** positive out-of-sample expectancy after
costs; profit factor > 1.3; out-of-sample Sharpe > 1.0 on daily returns; a
Monte Carlo 95th-percentile drawdown within the risk budget; demo results
within the backtest's expected range.

---

## 11. Operations

### 11.1 Deployment
- **Host:** a small Linux VPS near Chicago/Aurora, or cloud in `us-east-2` /
  `us-central`. Latency is not critical, but a stable network is.
- **Runtime:** Docker container under `systemd` (or Docker's
  `restart: unless-stopped`). Time synced through `chrony`.
- **Daily lifecycle:** the process runs continuously and follows the session
  state machine. A weekly restart (Saturday) picks up new configuration and
  calendars.

### 11.2 Persistence
- **SQLite** (WAL mode) in v1, with the option to move to Postgres. Tables:
  `intents`, `risk_decisions`, `orders`, `order_events`, `fills`,
  `positions_snapshots`, `daily_pnl`, `halts`.
- **Event journal:** append-only JSONL, rotated daily.
- **Tick capture:** Parquet.

### 11.3 Observability
- **Logs:** `structlog`, JSON, one line per event, with correlation IDs
  (`intent_id` → `client_order_id` → `tradovate_order_id`).
- **Metrics:** Prometheus client covering P&L, open risk, exposure, latency
  (signal → ack, ack → fill), quote age, reconnect count and reject count.
  Optional Grafana on top.
- **Alerts** (Telegram, Discord or Pushover):
  - Info: session start/end summary, daily P&L report.
  - Warn: reconnects, soft limits hit, rejects.
  - Critical: halts, reconciliation mismatch, missing stop, process crash.
- **External dead-man's switch:** the bot pings a monitoring service (e.g.
  Healthchecks.io) every minute during trading hours. Missed pings page the
  operator, which catches the case where the host itself is dead.

### 11.4 Operator controls
A small authenticated control channel, either Telegram bot commands restricted
to one user ID or a local CLI over a Unix socket:
`status`, `pause`, `resume`, `flatten`, `kill`, `set-risk <pct>`, `disable <strategy>`.
Every command is logged. There is intentionally **no** command to open a trade
manually.

### 11.5 Failure-mode table

| Failure | Detection | Response |
|---|---|---|
| Market-data socket drops | No frames for more than 10 s | Reconnect; PAUSE entries; FLATTEN if still down after 30 s with an open position |
| Trading socket drops | Heartbeat timeout | Reconnect and reconcile; brackets on the server protect positions meanwhile |
| Token expires | Expiry timer, or a 401 response | Renew; on failure, re-authenticate; if that fails, KILL |
| Order placement times out | No ack within 5 s | Query state; never blind-resend |
| Fill without a stop | Reconciliation | Attach an emergency stop; alert |
| Price-limit halt or exchange halt | Quote stall plus exchange status | PAUSE; brackets remain; alert |
| Bot process crash | systemd plus dead-man's switch | Auto-restart, then startup reconciliation; server brackets cover the gap |
| Host or VPS dies | Dead-man's switch | Human steps in (Tradovate app/web); server brackets cover the gap |
| Rate-limit penalty | `p-ticket` response | Wait `p-time`, retry; `p-captcha` means KILL |
| Bad tick (price spike) | Deviation more than X × ATR from the last N ticks | Drop it from bar building; log |
| Clock drift | chrony status check | Alert when drift exceeds 250 ms |

---

## 12. Configuration

```yaml
# config/ace.yaml (secrets come from environment variables, never this file)
environment: demo            # demo | live | replay
account_spec: "DEMO1234567"
symbols: [MES, MNQ, M2K]

session:
  timezone: America/New_York
  trade_eth: false
  flatten_at: "15:55"
  wind_down_at: "15:45"
  blackout_calendar: config/events.yaml
  blackout_window: { before_min: 2, after_min: 5 }

roll:
  days_before_expiry: 8

risk:
  risk_pct_per_trade: 0.0075
  max_risk_per_trade_usd: 150
  daily_max_loss_pct: 0.02
  weekly_max_dd_pct: 0.05
  max_consecutive_losses: 3
  max_trades_per_day: 6
  max_contracts: { MES: 4, MNQ: 4, M2K: 4 }
  max_beta_weighted_exposure_x_equity: 2.0
  max_spread_ticks: { MES: 2, MNQ: 4, M2K: 4 }
  stale_quote_sec: 3

execution:
  entry_ttl_sec: 120
  ack_timeout_sec: 5
  slippage_allow_ticks: { MES: 1, MNQ: 2, M2K: 2 }

strategies:
  orb:
    enabled: true
    symbols: [MES, MNQ, M2K]
    or_minutes: 15
    width_pct_band: [20, 80]
    stop_atr_mult: 1.0
    partial_r: 1.5
  vwap_reversion:
    enabled: false           # enable after validation
    entry_sigma: 2.0
    stop_sigma: 3.0
```

Config is loaded into **Pydantic** models, so a typo or an out-of-range value
fails at startup rather than at 10:03 on a trading day.

---

## 13. Project layout and tech stack

```
ace/
├── pyproject.toml
├── config/                  # ace.yaml, fees.yaml, events.yaml, holidays_override.yaml
├── src/ace/
│   ├── app.py               # wiring and entrypoint
│   ├── core/                # events, bus, clock, types (Side, Bar, TradeIntent…)
│   ├── tradovate/           # auth.py, socket.py, rest.py, md.py, sync.py, orders.py, models.py
│   ├── data/                # bar_builder.py, indicators.py, tick_recorder.py, history.py
│   ├── session/             # calendar.py, scheduler.py, blackout.py, contracts.py (roll)
│   ├── strategies/          # base.py, orb.py, vwap_reversion.py, arbiter.py
│   ├── risk/                # manager.py, sizing.py, limits.py, exposure.py, killswitch.py
│   ├── execution/           # oms.py, order_fsm.py, reconciler.py
│   ├── persistence/         # db.py, journal.py
│   ├── ops/                 # alerts.py, metrics.py, control.py, healthcheck.py
│   └── backtest/            # engine.py, sim_broker.py, sim_clock.py, reports.py
└── tests/
    ├── unit/                # sizing, FSM transitions, bar building, session edges
    ├── property/            # hypothesis: risk invariants
    ├── integration/         # fake Tradovate WS server
    └── replay/              # recorded sessions → expected decisions
```

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Async I/O | `asyncio`, `websockets`, `httpx` |
| Models / config | `pydantic` v2, `pydantic-settings` |
| Data | `polars`, `numpy`, `pyarrow` |
| Calendars | `exchange_calendars`, `zoneinfo` |
| Storage | SQLite (via `sqlalchemy` or `sqlite3`), Parquet |
| Logging / metrics | `structlog`, `prometheus-client` |
| Testing | `pytest`, `pytest-asyncio`, `hypothesis` |
| Tooling | `uv`, `ruff`, `mypy --strict` for `risk/` and `execution/` |

### 13.1 Testing invariants worth enforcing
Property-based tests (`hypothesis`) over random event sequences check that:
- No order is ever sent without an attached stop.
- Position size never exceeds `max_contracts`, and per-trade risk never
  exceeds the budget.
- After `FLATTEN`, position is 0 and there are no working orders within T
  seconds (against the fake broker).
- No entry is ever submitted in `WIND_DOWN`, `POST_RTH`, `CLOSED`, or during
  a blackout.
- Replaying a recorded journal produces identical intents
  (determinism).

---

## 14. Delivery milestones

| # | Milestone | Exit criteria |
|---|---|---|
| M0 | Skeleton: config, event bus, clock, logging | CI green, `mypy` clean |
| M1 | Tradovate connectivity (demo): auth, sockets, quotes, tick chart, user sync | 8-hour soak run with reconnects, no leaks |
| M2 | BarBuilder, indicators, session scheduler, calendar, contract resolver | Bars match vendor data within tolerance; DST and holiday tests pass |
| M3 | OMS, brackets, reconciliation, kill switch (demo) | Chaos tests: kill the process mid-trade, drop sockets; still recovers to a safe state |
| M4 | Backtest engine plus ORB strategy; research dataset | Walk-forward report produced |
| M5 | Full risk manager plus arbiter | Property tests pass |
| M6 | Replay, then 4–6 weeks of demo trading | Demo vs backtest drift within tolerance |
| M7 | Live at 1 contract | 4 weeks without operational incidents |
| M8 | Scale plus S2 (VWAP reversion) | Its own validation track |

---

## 15. Open questions

1. **Account size and risk appetite:** the actual starting equity sets
   `risk_pct`, the daily loss limit, and whether 3 symbols × multiple contracts
   is even feasible.
2. **Is this a prop firm (funded) account?** If so, the firm's
   trailing-drawdown and consistency rules must be built into the risk layer
   and would override Section 8.4.
3. **ETH trading:** is overnight session trading worth enabling later, or is
   the bot RTH-only permanently?
4. **Historical data vendor and budget** for the research dataset.
5. **Alert channel preference:** Telegram, Discord, SMS or email.
6. **Hosting:** VPS provider and region.
7. **Tradovate plan:** the commission tier determines `fees.yaml`, which
   directly affects which strategies are viable (Section 7.4).

---

## Appendix A: Glossary

- **RTH / ETH:** Regular / Extended Trading Hours.
- **OR:** Opening Range, the high and low of the first N minutes of RTH.
- **VWAP:** Volume-Weighted Average Price for the session; σ-bands are
  volume-weighted standard deviations around it.
- **ATR:** Average True Range, a volatility measure used for stop and size
  normalization.
- **R:** the risk unit of a trade (entry-to-stop distance). A 1.5R target is
  1.5 × that distance.
- **OSO / OCO:** Order-Sends-Order (the entry triggers its brackets) and
  One-Cancels-Other (the stop and target cancel each other).
- **Beta-weighted exposure:** position notional scaled by its sensitivity to
  the S&P 500, which makes MES, MNQ and M2K exposure additive.
