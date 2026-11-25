# NIFTY Tail-Risk Scanner — Operator Guide

A plain-English manual for running and using this project day to day. For the
quick overview and deploy steps, see [README.md](README.md). This guide is the
"how do I actually use it" companion.

> **Honest framing (read once):** this is an educational, loss-budgeted
> experiment, not investment advice. SEBI FY26: ~87.7% of individual F&O traders
> lost money and option *buyers* lost most. Trade small, treat option buying as
> paper-first, and always check the live bid-ask in Groww before any order.

---

## 1. What it is

A free, fully-automated system that each trading evening:
- pulls NSE end-of-day data + India VIX,
- runs several **deterministic** strategies (fixed rules, no AI in the signal),
- publishes a **dashboard** and pushes a **Telegram alert**,
- and answers questions via a **Telegram bot** (free open-source LLM).

You read the signals and place any trades **manually** in Groww. Nothing here
touches your broker or places orders.

---

## 2. Architecture (how the pieces fit)

```
          ┌─────────────── GitHub Actions (free cron, 18:30 & 20:00 IST) ───────────────┐
          │  python -m scripts.run_scan                                                 │
          │    ├─ NSE bhavcopy (option chain)      ── nsearchives (works everywhere)    │
          │    ├─ India VIX + NIFTY history        ── Yahoo Finance (datacenter-safe)   │
          │    ├─ deterministic math: IV, skew, VRP, gates, exits                       │
          │    ├─ strategies: tail-risk • credit spread • calendar • ETF momentum       │
          │    ├─ paper journal (options)  +  real ETF holdings mark-to-market          │
          │    └─ writes JSON → docs/data/*.json  +  Telegram push                      │
          │  git commit + push (rebase-retry)                                           │
          └────────────────────────────────┬───────────────────────────────────────────┘
                                            │
              ┌─────────────────────────────┼─────────────────────────────┐
              ▼                             ▼                             ▼
     GitHub Pages dashboard        Cloudflare Worker bot          data/eod/*.json archive
     (docs/, static HTML/JS)       (Telegram webhook + Llama)     (grows the backtest)
```

**Everything is free:** GitHub Actions (cron), GitHub Pages (dashboard),
Cloudflare Workers + Workers AI (bot + LLM), Yahoo/NSE (data). No broker API, no
paid data.

### Key files
| Path | What it does |
|---|---|
| `config.yaml` | Every threshold/parameter. Tune here; nothing is hardcoded. |
| `scanner/blackscholes.py` | Option pricing, implied vol, delta (pure stdlib) |
| `scanner/metrics.py` | realized vol, percentile, skew, VRP, trade stats |
| `scanner/rules.py` | tail-risk entry gates + exit rules (deterministic) |
| `scanner/nse_data.py` | data fetchers (NSE bhavcopy + Yahoo VIX/index/ETF) |
| `scanner/scan.py` | the tail-risk daily scan |
| `scanner/strategies.py` | credit spread, calendar, ETF momentum + holdings |
| `scanner/journal.py` | options paper-trade journal + loss-budget lock |
| `scanner/backtest.py` | replays `data/eod/*` to backtest the tail strategy |
| `scripts/run_scan.py` | daily entrypoint (the cron runs this) |
| `scripts/run_backtest.py` | runs the backtest |
| `scripts/fetch_history.py` | seeds `data/eod/` with past days (for backtest) |
| `scripts/holdings.py` | log real ETF buys/sells |
| `docs/` | the dashboard (index.html, app.js, styles.css, data/*.json) |
| `worker/` | the Telegram bot (Cloudflare Worker) |

---

## 3. The strategies (what each one is)

| Strategy | Direction | Edge (the math) | Capital | Where it shows |
|---|---|---|---|---|
| **Tail-risk put buy** | Buy puts | Fat tails: models underprice rare crashes. Win big rarely, lose small often. | ~₹1–3k per bet | top of dashboard + `/today` `/why` |
| **Put credit spread** | Sell premium | Volatility risk premium + theta. The statistically winning side. Defined max loss. | ~₹5–6k defined | "Other strategies" + `/credit` |
| **Calendar spread** | Vol term structure | Sell rich near-dated IV, buy cheaper far-dated. | low (debit) | "Other strategies" + `/calendar` |
| **ETF sector momentum** | Buy ETFs | Momentum + **200-SMA trend filter** (else cash) + **low-vol tilt** (inverse-vol weighting). No leverage. | lowest (₹ you choose) | "ETF sector momentum" card + `/etf` |

Each shows **● SETUP/ACT** (green) when its rules fire, or **○ wait** when they
don't — with the exact reason. "Wait" is the normal, correct state most days.
Realized vol uses the **Yang–Zhang** OHLC estimator (sharper VRP / IV-RV).

---

## 4. Where to check everything

### Dashboard — `https://yogeshwaran2002.github.io/nifty-tail-scanner/`
It's a **command center**, top to bottom:
- **Run scan now / Reload** buttons
- **Today's playbook** — one card per strategy: **ACT / wait**, the exact action, and the exit. This is your "what do I do today" view.
- **Positions & P&L** — your real ETF holdings (live P&L), the options paper journal, the loss-budget bar, and how to log trades
- **Market snapshot** — spot, VIX, VIX percentile, realized vol (Yang–Zhang), ATM IV, VRP
- **Tail-risk** — banner, VIX-regime bar, candidate strikes with pass/fail reasons
- **Volatility smile** — IV per strike (the skew) + VRP read
- **Options strategies** — put credit spread + calendar, each with full trade construction
- **ETF sector momentum** — ranked table (momentum, vol, trend), the low-vol-weighted trade plan, and the plain-vs-trend-filtered backtest
- **Tail-risk backtest** — stats over archived days
- **Learn the mechanics**

### Telegram bot
Message your bot:
- `/playbook` — **all strategies: act/wait + today's action** (the mobile version of the playbook strip)
- `/today` — latest tail-risk scan
- `/why` — gate breakdown (why it did/didn't fire)
- `/skew` — volatility smile + VRP
- `/etf` — ETF momentum ranking, weighted plan + backtest
- `/holdings` — your real ETF positions + live P&L
- `/credit` — put credit spread setup
- `/calendar` — calendar spread
- `/backtest` — tail-risk backtest stats
- `/journal` — options paper journal
- `/id` — your chat id
- *any question* — the Llama explainer (grounded on the data; never gives advice)

---

## 5. Daily routine (2 minutes)

You mostly **don't need to do anything** — the cron runs at 18:30 IST and pushes
an alert. When you want to look:

1. **After ~18:35 IST**, open Telegram → the push alert, or send **`/playbook`** (the one-shot "what do I do today" across all strategies).
2. If any strategy shows **ACT**, open the dashboard → **Today's playbook** + the strategy's card for the full numbers.
3. Before placing *any* option trade, **check the live bid-ask in Groww** (EOD
   data has no spread).
4. If you trade, log it:
   - Options (paper) are auto-logged by the scan.
   - Real ETF buys/sells → `python -m scripts.holdings buy/sell ...` (section 7).

**On a holiday or pre-close**, the option strategies show the last trading day's
data (that's intended — it won't overwrite with blanks). ETF momentum still
updates from live prices.

To force a refresh any time: **▶ Run scan now** on the dashboard (or re-run the
Action). Note it only produces fresh option data **after the NSE close (~18:00
IST)** when the bhavcopy is published.

---

## 6. Monthly routine — ETF rebalance (5 minutes, 1st trading day)

1. Send `/etf` (or open the ETF card).
2. Look at the **top 3** by momentum.
3. For each ETF you currently hold that **dropped out of the top 3** → sell it:
   `python -m scripts.holdings sell <SYMBOL> <units> <price>`
4. Buy the new leaders to keep 3 holdings, equal rupee weight:
   `python -m scripts.holdings buy <SYMBOL> <units> <price>`
5. `git add data/etf_holdings.json && git commit -m "rebalance" && git push`

That's the whole discipline. Do **not** trade it in between — momentum works on a
monthly cadence.

---

## 7. Logging trades

### Real ETF holdings (`scripts/holdings.py`)
```bash
python -m scripts.holdings list                       # show positions + realized P&L
python -m scripts.holdings buy  PSUBNKBEES 2 108.50    # add 2 units @ 108.50
python -m scripts.holdings sell PHARMABEES 7 30.20     # sell 7 @ 30.20 (books realized P&L)
python -m scripts.holdings set  PHARMABEES 7 29.86     # overwrite (fix exact fill prices)
python -m scripts.holdings del  BANKBEES               # remove a position
```
After editing, `git push` so the dashboard/bot update (or wait for the next
cron, which commits it automatically). The bot is read-only, so logging is done
with this CLI, not via chat.

### Options paper journal
Automatic — the tail-risk scan opens/marks/exits paper trades and enforces the
loss budget (`config.yaml` → `paper.loss_budget`). It never uses real money.

---

## 8. Backtest

The backtest replays `data/eod/*.json` (one snapshot per trading day) through the
**same** tail-risk gates and exit rules as the live scan.

- Grow it automatically: every daily scan archives that day.
- Seed it fast from NSE's archive:
  ```bash
  python -m scripts.fetch_history 2026-09-01 2026-11-23
  python -m scripts.run_backtest
  git add data/ docs/data/backtest.json && git commit -m "seed backtest" && git push
  ```
- Read it on the dashboard "Backtest" card or `/backtest`.
- **Judge by expectancy and payoff ratio, not win-rate.** Tail-risk is designed
  to have a low hit rate with rare large wins.

---

## 9. Tuning (`config.yaml`)

Common knobs:
- `market.lot_size` — NIFTY lot (65). Verify on NSE if it changes.
- `entry_gates.*` — tail-risk thresholds (VIX percentile, skew, IV/RV, liquidity, cost cap).
- `exit_rules.*` — take-profit multiple, VIX-spike exit, time stop.
- `credit_spread.*` — delta, width (controls max loss), min VRP to sell.
- `etf_momentum.basket / top_n / budget_inr / trailing_stop_pct`.
- `paper.loss_budget` — the paper loss cap that locks the journal.

Change a value → re-run the scan/backtest → compare. Every number is a hypothesis
to test, not a proven edge.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard shows `n/a` for VIX/RV | Yahoo hiccup; next scan fixes it (values are cached). |
| "Option data 404" in logs | Holiday / weekend / pre-publication. Normal — not a bug. |
| Backtest "need more history" | Seed with `fetch_history.py` or let the cron accumulate days. |
| Bot doesn't reply | Telegram flood-ban on the bot; worst case make a new bot (`/newbot`), update `TELEGRAM_BOT_TOKEN` worker secret, re-hit `/setup`. |
| "Run scan now" says token not set | Set `GH_DISPATCH_TOKEN` (fine-grained PAT, Actions RW) on the Worker. |
| Free-text chat errors "model deprecated" | `wrangler ai models` in `worker/`, pick a current text model, set `LLM_MODEL`, `wrangler deploy`. |
| Scheduled run failed on push | Already handled (rebase + retry); re-run if a one-off. |

---

## 11. The one rule that matters

Every signal here is a *starting hypothesis*. Trade tiny, keep a hard loss
budget, judge strategies by their math over many trades (not one), and never let
a tool talk you out of your own risk limits. The app will happily show you a
setup; whether it's worth your money is always your call.
