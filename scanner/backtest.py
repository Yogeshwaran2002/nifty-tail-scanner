"""Backtest engine — replays the archived EOD dataset.

Free multi-year option history is hard to source, so this engine runs on
whatever `data/eod/*.json` snapshots exist. The daily scan archives one snapshot
per trading day, so the dataset (and the backtest's usefulness) grows over time.
Seed it faster with `scripts/fetch_history.py` if NSE serves archive bhavcopies.

It reconstructs VIX-percentile and realized-vol from the snapshot SEQUENCE
itself, then applies the exact same deterministic gates and exit rules as the
live scan — so a backtest result and a live signal are produced by identical
logic.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import date

from . import blackscholes as bs
from . import metrics as mx
from . import rules


def _load_archive(eod_dir: str = "data/eod") -> list[dict]:
    days = []
    for path in sorted(glob.glob(os.path.join(eod_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            try:
                days.append(json.load(f))
            except json.JSONDecodeError:
                continue
    return days


def _d(s: str) -> date:
    return date.fromisoformat(s)


def run_backtest(cfg: dict, eod_dir: str = "data/eod") -> dict:
    days = _load_archive(eod_dir)
    if len(days) < 5:
        return {"ok": False, "reason": f"Only {len(days)} archived day(s). "
                "Need more history — the daily scan grows it, or seed with fetch_history.py.",
                "days_available": len(days)}

    m = cfg["market"]
    r, q = m["risk_free_rate"], m["dividend_yield"]
    vix_series = [d["vix"] for d in days if d.get("vix") is not None]
    spot_series = [d["spot"] for d in days if d.get("spot") is not None]

    # index snapshots by date for forward mark-to-market
    by_date = {d["date"]: d for d in days}
    dates = [d["date"] for d in days]

    trades = []
    open_positions: list[dict] = []
    realized = 0.0
    equity_curve = []

    for i, snap in enumerate(days):
        today = _d(snap["date"])
        spot = snap.get("spot")
        if spot is None:
            continue
        vix_hist = [d["vix"] for d in days[:i + 1] if d.get("vix") is not None]
        spot_hist = [d["spot"] for d in days[:i + 1] if d.get("spot") is not None]
        vix_pct = mx.percentile_rank(vix_hist[-1], vix_hist[-cfg["vix"]["lookback_days"]:]) if vix_hist else None
        rv = mx.realized_vol(spot_hist, cfg["realized_vol"]["window_days"], m["trading_days_year"])

        # ── exits first on existing positions ──
        still_open = []
        for pos in open_positions:
            cur = _premium_for(by_date.get(snap["date"]), pos["strike"], pos["expiry"])
            dte = (_d(pos["expiry"]) - today).days
            if cur is None:
                still_open.append(pos)
                continue
            decision = rules.evaluate_exit(pos, cur, vix_pct, dte, cfg)
            if decision:
                pnl = _net_pnl(pos, cur, cfg)
                realized += pnl
                trades.append({**pos, "exit_date": snap["date"], "exit_premium": cur,
                               "exit_reason": decision["action"], "pnl": round(pnl, 2)})
            else:
                still_open.append(pos)
        open_positions = still_open

        # ── entry ──
        expiry = snap.get("expiry")
        puts = snap.get("puts", [])
        if expiry and puts and not _locked(realized, cfg):
            dte = (_d(expiry) - today).days
            if cfg["universe"]["dte_min"] <= dte <= cfg["universe"]["dte_max"]:
                cand = _best_candidate(puts, spot, dte, r, q, rv, vix_pct, cfg)
                if cand and not any(p["strike"] == cand["strike"] and p["expiry"] == expiry
                                    for p in open_positions):
                    open_positions.append({
                        "open_date": snap["date"], "strike": cand["strike"], "expiry": expiry,
                        "entry_premium": cand["premium"], "lots": cfg["paper"]["default_lots_per_trade"],
                        "lot_size": m["lot_size"]})

        equity_curve.append({"date": snap["date"], "realized_pnl": round(realized, 2),
                             "open_positions": len(open_positions)})

    return _summarize(trades, equity_curve, realized, len(days), cfg)


def _premium_for(snap, strike, expiry):
    if not snap:
        return None
    for p in snap.get("puts", []):
        if p["strike"] == strike and p["expiry"] == expiry:
            return p["close"]
    return None


def _best_candidate(puts, spot, dte, r, q, rv, vix_pct, cfg):
    T = max(dte, 1) / 365.0
    strikes = [p["strike"] for p in puts]
    atm = mx.nearest(strikes, spot)
    atm_put = next(p for p in puts if p["strike"] == atm)
    atm_iv = bs.implied_vol_put(atm_put["close"], spot, atm, T, r, q)
    u = cfg["universe"]
    lo_k, hi_k = spot * (1 - u["otm_max_pct"] / 100.0), spot * (1 - u["otm_min_pct"] / 100.0)
    best = None
    for p in puts:
        if not (lo_k <= p["strike"] <= hi_k):
            continue
        iv = bs.implied_vol_put(p["close"], spot, p["strike"], T, r, q)
        metrics = {
            "strike": p["strike"], "premium": p["close"], "iv": iv,
            "skew": mx.put_skew(atm_iv, iv), "iv_rv_ratio": mx.iv_rv_ratio(atm_iv, rv),
            "open_interest": p["open_interest"], "volume": p["volume"],
            "cost_per_lot": p["close"] * cfg["market"]["lot_size"],
            "vix_percentile": vix_pct,
        }
        if rules.passes_all(rules.evaluate_entry_gates(metrics, cfg)):
            if best is None or rules.cheapness_score(metrics) < rules.cheapness_score(best):
                best = metrics
    return best


def _net_pnl(pos, exit_premium, cfg):
    gross = (exit_premium - pos["entry_premium"]) * pos["lots"] * pos["lot_size"]
    c = cfg["costs"]
    notional = exit_premium * pos["lots"] * pos["lot_size"]
    stt = notional * c["stt_sell_pct"] / 100.0
    slippage = notional * c["slippage_pct"] / 100.0
    return gross - stt - slippage - 2 * c["brokerage_per_order"]


def _locked(realized, cfg):
    return realized <= -abs(cfg["paper"]["loss_budget"])


def _summarize(trades, equity_curve, realized, n_days, cfg):
    stats = mx.trade_stats([t["pnl"] for t in trades])
    peak = 0.0
    max_dd = 0.0
    for pt in equity_curve:
        run = pt["realized_pnl"]
        peak = max(peak, run)
        max_dd = min(max_dd, run - peak)
    return {
        "ok": True,
        "days_tested": n_days,
        "total_trades": stats["total"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "hit_rate_pct": stats["hit_rate_pct"],
        "net_pnl": round(realized, 2),
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "expectancy": stats["expectancy"],
        "payoff_ratio": stats["payoff_ratio"],
        "best_trade": stats["best"],
        "worst_trade": stats["worst"],
        "max_drawdown": round(max_dd, 2),
        "trades": trades,
        "equity_curve": equity_curve,
        "note": "Tail-risk expects a LOW hit rate with rare large wins. Judge by "
                "expectancy and payoff ratio, not win %.",
    }
