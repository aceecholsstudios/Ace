# Ace: Research Notes and Suggested Improvements

*2026-09-29. Companion to `docs/DESIGN.md` v0.2. Findings come from web
research. Tradovate's own pages were not reachable from the research
environment, so figures marked **(verify)** come from secondary sources and
must be confirmed before they are relied on.*

---

## 1. Critical finding: live market data through the API costs extra

| Item | Cost (verify) | Notes |
|---|---|---|
| Tradovate API Access add-on | ~$25/month | Covers orders and account data **only** |
| Minimum balance to create an API key | ~$1,000 live funded | |
| Real-time market data **through the API** | CME sub-vendor license (ILA), **~$290–$500/month** | Needed to stream quotes, ticks or DOM over the API websocket |
| Databento CME Globex (alternative data source) | "Standard" plan **~$179/month** | Direct CME feed; replaced usage-based live pricing in April 2025 |

**Why this matters:** on a sub-$5k account, $200–$300/month of fixed cost
equals 4–6% of the account each month, before a single trade. The bot has to
earn that back first. It is the largest single threat to the "income" goal.

**Options:**
1. **Databento for data, Tradovate for orders** (recommended to evaluate).
   Likely cheaper than the ILA, and the data is better (Section 3).
2. **Tradovate data with the ILA.** One vendor, simpler integration, but
   higher cost and weaker data.
3. Confirm with Tradovate whether the **demo** environment also requires the
   ILA for API market data. This decides the cost of the demo phase.

Design impact: none structurally, because DESIGN.md already puts the data
source behind an interface. What changes is *which implementation is built
first*.

---

## 2. Commission plan: the math favors switching early

Commission per side on micros (verify): **Free $0.39 · Monthly $0.29
($99/month) · Lifetime $0.09**. Exchange, clearing and NFA fees come on top
under every plan. CME also announced fee changes **effective 2026-10-01**, so
`fees.yaml` should be updated from a real statement.

- Monthly vs Free saves $0.20 per round turn per contract. Break-even is
  $99 / $0.20 = **~495 round turns a month (~24 a day)**.
- The design allows up to 15 trades a day at 2–4 contracts each, which is
  30–60 contract round turns a day. At that activity the **Monthly plan is
  cheaper than Free**, and Lifetime saves $0.60 per round turn compared with
  Free.
- Suggestion: the daily report's plan comparison (DESIGN §10) should use the
  demo trade log to recommend a plan before going live.

---

## 3. Improvement ideas, ranked by how promising they look

### 3.1 Read the E-mini flow, trade the micro ★★★ (most promising)
MES prices are derived from ES and kept aligned by arbitrage. At the top of
the book, ES typically shows 1,000–5,000 contracts against 100–500 for MES.
Large participants trade the E-minis (ES, NQ, RTY). Much of what appears in
the micro books is market makers and arbitrage bots copying the E-mini.
Sweeps and absorption in the **E-mini** books therefore show where real size
is trading.

- **Change:** compute every order-flow signal on ES/NQ/RTY and execute on
  MES/MNQ/M2K.
- **Cost:** none extra. The same CME Globex data covers both.
- **Bonus:** more data means less noise in the rolling percentiles, and the
  E-mini DOM is far more meaningful for absorption and target levels.

### 3.2 Use a data feed with the true aggressor side and order-level depth ★★★
The quote rule is an approximation. Studies of classification algorithms find
quote-based methods correctly classify about 90% of volume in equities, with
worse results under fast quoting, which is exactly when sweeps happen.
Exchange data such as Databento's CME MBP/MBO carries the **real aggressor
side**, so no inference is needed.

Order-by-order (MBO) data also enables **iceberg detection**. Academic work
on ES (Zotikov, 2019) detects native CME icebergs from the full order log.
Estimates put icebergs at 15–25% of resting volume in liquid contracts. An
iceberg being filled repeatedly is the most direct evidence of absorption,
and a much stronger signal than inferring absorption from volume at price.

### 3.3 Add order-flow imbalance and microprice as a fourth signal ★★☆
The strongest academic evidence in short-horizon microstructure:
- **Cont, Kukanov and Stoikov** show that price changes over short
  intervals are close to **linear in order-flow imbalance (OFI)**. OFI is the
  net of additions, cancellations and trades at the best bid and ask, and
  its effect on price is scaled by depth.
- **Stoikov's micro-price** (and queue imbalance) predicts the next mid-price
  move, and works best in **large-tick** instruments such as ES and MES,
  where the spread is usually one tick.

**Caveat:** this edge lasts seconds and is heavily competed by HFT firms. On
its own it cannot pay for micro-contract costs. Its value here is as:
- a **confirmation term** in the weighted score, and
- an **entry-timing tool** (Section 3.4).

It is cheap to compute from top-of-book data.

### 3.4 Smarter entries: limit order when the microprice allows ★★☆
Market entries pay the spread on every trade: about 1 tick, or $0.50 per
MNQ contract. At 15 trades × 3 contracts a day that is roughly $20+ a day on
MNQ alone.

- **Idea:** when the microprice leans in the trade's direction, post a limit
  order at the touch for up to N seconds, then fall back to a market order if
  the signal is still valid.
- **Risk:** adverse selection. Passive orders tend to fill when the market is
  about to move against you. Measure this with an A/B flag in demo: half the
  signals use pure market entries, half use the hybrid.

### 3.5 A signal lab: log forward returns for every candidate ★★★ (cheap, high value)
For every detector firing, record the feature vector and the forward returns
at +30 s, +1 m, +3 m and +10 m, whether or not a trade was taken. After a few
weeks this answers the important questions with data:
- Does each signal predict anything at the 1–10 minute horizon?
- Which percentile thresholds matter?
- Should the weights stay equal?

It is also the dataset a later ML filter would need. It extends the
missed-trade log already in the design, and adds about 1 table.

### 3.6 Backtest before the demo phase ★★☆
The decision was a demo forward test only. With historical CME data (e.g.
Databento, usage-based historical pricing), several **years** of ES/MES tick
and book data can be replayed through the same engine before the demo
starts. A few weeks of demo can't separate a real edge from luck at 15
trades a day. A multi-year backtest can at least rule out the ideas that
never work. Tradovate itself keeps only about a week of 1-tick history, so it
cannot serve as the historical source.

### 3.7 Cross-index lead-lag ★☆☆ (speculative)
NQ and RTY sometimes lead ES around sector-driven moves (technology,
small-cap rotation). A "flow confirmation" term could check whether the other
two indexes' order flow agrees with the traded symbol's signal. Low cost to
add once 3.1 exists. The evidence is anecdotal, so test it through the
signal lab before trusting it.

### 3.8 A second, low-frequency strategy for diversification ★☆☆
**Intraday momentum** (Gao, Han, Li and Zhou, *Journal of Financial
Economics* 2018): the first half-hour return of the S&P 500 predicts the last
half-hour return. The effect is stronger on volatile, high-volume and news
days. It trades once a day with low costs and doesn't depend on order flow,
which makes it a natural complement. The design already supports multiple
strategies. Worth a backtest; later research finds the effect has weakened
since publication.

---

## 4. Useful facts for implementation

- **Tradovate tick-chart packets** carry a base price (`bp`), base timestamp
  (`bt`) and tick size (`ts`), plus relative per-tick fields. Each tick
  includes relative bid/ask prices (`b`, `a`) and optionally bid/ask sizes
  (`bs`, `as`). The quote rule can therefore use the bid/ask recorded **with
  each trade**, avoiding the quote-timing mismatch DESIGN §7.1 worried about.
  Bar charts also expose `bidVolume` / `offerVolume`.
- **Only about a week of 1-tick history** is available from Tradovate.
  Anything longer must be recorded by the bot or bought.
- **Python Tradovate libraries are immature.** TradovatePy notes that its
  websocket and market-data parts are unfinished. The design's decision to
  build its own client is correct. The official JS examples
  (`tradovate/example-api-js`) are the best protocol reference.

---

## 5. An honest assessment

- The academic evidence for order flow is strongest at **seconds** horizons
  (OFI, queue imbalance). At the design's **1–10 minute** horizon it is
  thinner, and signals like delta divergence are widely used by retail
  traders but rarely validated in peer-reviewed work. That doesn't make them
  worthless, but it makes the signal lab (3.5) and a backtest (3.6) far more
  important than further tuning.
- Fixed costs (Section 1) plus per-trade costs (DESIGN §3.1) set a high bar
  for a sub-$5k account. Before building the whole system, estimate the
  **monthly break-even**: fixed costs ÷ expected trades ÷ expected net profit
  per trade.

## 6. Suggested changes to DESIGN.md (pending your approval)

| # | Change | Section |
|---|---|---|
| 1 | Add a data-source cost decision: Databento vs Tradovate+ILA | §6.1, §17 |
| 2 | Compute signals on ES/NQ/RTY, execute on micros | §6.5, §7 |
| 3 | Add OFI/microprice as a fourth scored signal | §7.4–7.5 |
| 4 | Hybrid entry (microprice-gated limit, market fallback) as an A/B option | §9.1 |
| 5 | Signal lab table with forward returns | §10 |
| 6 | Optional historical backtest step before the demo | §13 |
| 7 | Recommend the Monthly plan based on expected volume | §3.1 |

---

## Sources
- [Tradovate API Access (support)](https://support.tradovate.com/s/article/Tradovate-API-Access?language=en_US)
- [Tradovate forum: CME sub-vendor requirement ~$290/month](https://community.tradovate.com/t/is-cme-sub-vendor-requirement-for-api-access-is-290-per-month/6215)
- [PickMyTrade: Tradovate API fee and CME license](https://blog.pickmytrade.trade/tradovate-automation-skip-the-api-fee-and-cme-license/)
- [Tradovate pricing](https://www.tradovate.com/pricing/) · [Damn Prop Firms: Tradovate fees explained](https://damnpropfirms.com/trading-guides/tradovate-fees-explained-commissions-data-plans/)
- [CME Group: exchange fees](https://www.cmegroup.com/company/clearing-fees.html)
- [Databento: new CME pricing plans](https://databento.com/blog/introducing-new-cme-pricing-plans) · [Databento GLBX.MDP3](https://databento.com/datasets/GLBX.MDP3)
- [Tradovate example-api-js: tick charts](https://github.com/tradovate/example-api-js/tree/main/tutorial/WebSockets/EX-11-Tick-Charts)
- [Tradovate forum: 1-tick history limits](https://community.tradovate.com/t/anyway-to-get-1-tick-bar-data-for-more-than-a-week/4456)
- [TradovatePy](https://github.com/antonio-hickey/TradovatePy)
- [Cont, Kukanov, Stoikov: The Price Impact of Order Book Events (arXiv)](https://arxiv.org/pdf/1011.6402)
- [Stoikov: The Micro-Price (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694) · [Gould & Bonart: Queue Imbalance (arXiv)](https://arxiv.org/pdf/1512.03492)
- [Chakrabarty, Pascual, Shkilko: Evaluating trade classification algorithms](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2182819)
- [Zotikov: CME Iceberg Order Detection and Prediction (arXiv)](https://arxiv.org/pdf/1909.09495)
- [CrossTrade: ES vs MES](https://crosstrade.io/learn/futures-trading/es-vs-mes)
- [Gao, Han, Li, Zhou: Market Intraday Momentum (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)
