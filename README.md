# NIFTY Tail-Risk Scanner

A free, deterministic scanner that looks for **tail-risk option-buying setups on
the NIFTY 50** — the Cornwall Capital / *Big Short* idea ("buy cheap insurance
against events the bell curve calls impossible") adapted to Indian retail with
**manual execution**. It runs once a day on NSE end-of-day data, publishes a
dashboard, and pushes a Telegram alert when (and only when) a setup passes every
gate.

> ### Honest framing — read this first
> This is an **educational / personal-research experiment, not investment advice.**
> SEBI's FY26 study: **~87.7% of individual F&O traders lost money**, and **option
> buyers lost the most** (options were 92% of aggregate retail losses). This
> strategy *is* option buying. The "buy puts when VIX is low" idea is a
> **hypothesis to be tested**, not a proven edge — index puts carry volatility
> **skew** (institutions constantly buy crash insurance), so far-OTM puts are
> rarely "dirt cheap". Trade **paper only** until the backtest and paper results
> earn trust, and never risk more than your loss budget. EOD data has **no live
> bid-ask spread** — always check the live spread in your broker before clicking.

## How it works

```
NSE EOD data ──► metrics ──► entry gates ──► rank ──► signal ──► dashboard + Telegram
(bhavcopy,      (IV per     (deterministic,  (cheapness          (JSON)      (push)
 VIX, index)     strike,     config-driven)   score)
                 skew,
                 realized vol)
```

**Deterministic core, no LLM in the signal path.** Same data in → same signal out,
so it is unit-tested and backtestable. A conversational layer (read-only, explains
the numbers) is a later phase — see Roadmap.

### The daily scan
1. Download the **F&O bhavcopy** (UDiFF) and extract NIFTY index puts.
2. Pick the **monthly expiry** with 30–60 days left (configurable).
3. Invert **Black-Scholes** for implied vol per strike; compute **skew**
   (OTM-put IV − ATM IV), **VIX percentile** vs its 1-year range, and **IV ÷
   realized vol** richness.
4. Run **entry gates** (all must pass): VIX calm, skew not extreme, IV not rich
   vs realized, enough OI/volume, cost per lot within cap.
5. Rank survivors by a cheapness score; emit the signal.
6. Mark open **paper trades** to market and apply **exit rules** (take-profit
   multiple, VIX-spike exit, time stop, never hold into expiry).

Gates **fail closed**: if VIX or realized vol can't be fetched, no signal fires.

## Project layout

| Path | What |
|------|------|
| `config.yaml` | Every threshold. Tune here; nothing hardcoded. |
| `scanner/blackscholes.py` | BS pricing + implied-vol inversion (pure stdlib) |
| `scanner/metrics.py` | realized vol, percentile, skew, IV/RV |
| `scanner/rules.py` | entry gates + exit rules (the deterministic heart) |
| `scanner/nse_data.py` | defensive NSE EOD downloaders |
| `scanner/scan.py` | the daily orchestrator |
| `scanner/journal.py` | paper trades + loss-budget lock |
| `scanner/backtest.py` | replay over the archived dataset |
| `scanner/alert.py` | Telegram push + message formatting |
| `scripts/run_scan.py` | daily entrypoint (used by the Action) |
| `scripts/run_backtest.py` | backtest entrypoint |
| `scripts/fetch_history.py` | best-effort archive seeding |
| `docs/` | static dashboard (GitHub Pages) |
| `data/eod/` | archived daily snapshots — grows the backtest dataset |
| `.github/workflows/daily-scan.yml` | the free daily cron |

## Run it locally

```bash
pip install -r requirements.txt
python -m scripts.run_scan            # scan for today → writes docs/data/latest.json
python -m scripts.run_backtest        # replay archived days → docs/data/backtest.json
python -m pytest tests/ -q            # verify the deterministic core

# preview the dashboard
cd docs && python -m http.server 8899   # open http://localhost:8899
```

NSE's historical JSON APIs (VIX, index) can return **503 from datacenter/corporate
IPs** (the archive host is more reliable). If the cron keeps failing on those,
run `scripts/run_scan.py` from your home machine and commit — the result is
identical. VIX/index history is cached under `data/` so the percentile window
grows and survives transient failures.

## Deploy (free)

1. **Create a public repo** on github.com and push this folder (public = free
   Actions + free GitHub Pages).
2. **Enable Pages**: Settings → Pages → Source = *Deploy from a branch* →
   `main` / `/docs`. Your dashboard: `https://<username>.github.io/nifty-tail-scanner/`.
3. **Telegram alerts** (optional but recommended):
   - Message **@BotFather** → `/newbot` → copy the **bot token**.
   - Message your new bot once, then open
     `https://api.telegram.org/bot<token>/getUpdates` to find your numeric
     **chat id**.
   - Repo → Settings → Secrets and variables → Actions → add
     `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. The workflow runs **13:00 UTC (18:30 IST), Mon–Fri**, commits the day's
   signal + archive, and pushes an alert. Trigger it once manually via
   **Actions → daily-scan → Run workflow** to seed the dashboard.

> A public repo's scheduled workflow is **auto-disabled after 60 days of no repo
> activity** — the daily commit keeps it alive, but if you pause it for weeks,
> re-enable it in the Actions tab.

## Tuning

Everything lives in [`config.yaml`](config.yaml): lot size (65), DTE band, OTM
band, every gate threshold, exit rules, costs (STT 0.15% on sell, brokerage,
slippage), and the paper loss budget. Change a value, re-run the backtest, compare.

## Roadmap

- **Phase 1 (this repo):** daily scanner, dashboard, Telegram **push** alerts,
  paper journal, backtest harness.
- **Phase 2:** interactive Telegram commands (`/today`, `/why`, `/journal`) — needs
  a small always-on host; the read-only conversational agent lives here.
- **Later:** live bid-ask spread check, richer skew surface, alert when a setup is
  cheap in implied-vs-realized terms (not just when VIX is low).

## Verify before you trust a number
- Confirm the **NIFTY lot size** and **expiry calendar** on NSE (they change).
- The backtest needs history; seed with `fetch_history.py` or let the daily scan
  accumulate `data/eod/`. Tail-risk expects a **low hit rate with rare large
  wins** — judge by net P&L and payoff ratio, not win %.
