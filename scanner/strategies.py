"""Additional deterministic strategy modules, shown alongside tail-risk.

Each function is pure over already-fetched data and returns a signal dict with
the same shape idea: {fired, reasons, ...fields}. `run_strategies` fetches the
free EOD data once and assembles docs/data/strategies.json for the dashboard.

Strategies:
  - credit_spread : premium SELLING (defined risk) — fires when options are rich
  - calendar      : volatility term-structure (front vs next month ATM IV)
  - etf_momentum  : buy-only sector-ETF momentum (lowest capital)

Everything is educational and loss-budgeted — not advice.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

from . import blackscholes as bs
from . import metrics as mx
from . import nse_data as nse


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)


# ── Put credit spread (premium selling, defined risk) ───────────────────────

def credit_spread(exp_puts, spot, expiry, dte, atm_iv, rv, vix_pct, r, q, cfg):
    c = cfg["credit_spread"]
    T = max(dte, 1) / 365.0
    vrp = mx.vol_risk_premium(atm_iv, rv)
    iv_rv = mx.iv_rv_ratio(atm_iv, rv)

    # Enrich each OTM put with IV + delta
    enriched = []
    for p in exp_puts:
        if p["strike"] >= spot:
            continue
        iv = bs.implied_vol_put(p["close"], spot, p["strike"], T, r, q)
        if iv is None:
            continue
        delta = bs.put_delta(spot, p["strike"], T, r, q, iv)
        if delta is None:
            continue
        enriched.append({**p, "iv": iv, "delta": delta})
    if not enriched:
        return {"fired": False, "reasons": ["No priceable OTM puts for this expiry."]}

    # Short strike = put whose delta is nearest the target (e.g. -0.22)
    target = -abs(c["target_short_delta"])
    short = min(enriched, key=lambda x: abs(x["delta"] - target))
    long_strike_target = short["strike"] - c["width_points"]
    long = min(enriched, key=lambda x: abs(x["strike"] - long_strike_target))
    if long["strike"] >= short["strike"]:
        return {"fired": False, "reasons": ["Could not find a long strike below the short strike."]}

    width = short["strike"] - long["strike"]
    credit = short["close"] - long["close"]
    lot = cfg["market"]["lot_size"]
    max_loss = (width - credit) * lot
    max_profit = credit * lot
    breakeven = short["strike"] - credit
    pop = (1.0 + short["delta"]) * 100.0  # ≈ prob short put expires OTM
    ctw = credit / width if width else 0

    gates = [
        _g("vrp", vrp is not None and vrp >= c["min_vrp"], vrp, c["min_vrp"],
           "Only sell when options are genuinely rich (ATM IV − realized vol)"),
        _g("iv_rv_ratio", iv_rv is not None and iv_rv >= c["min_iv_rv_ratio"], iv_rv, c["min_iv_rv_ratio"],
           "Options must be rich vs realized to collect premium"),
        _g("credit_to_width", ctw >= c["min_credit_to_width"], round(ctw, 3), c["min_credit_to_width"],
           "Reward/risk floor: credit ÷ width"),
        _g("short_liquidity", short["open_interest"] >= cfg["entry_gates"]["min_open_interest"],
           short["open_interest"], cfg["entry_gates"]["min_open_interest"], "Short leg must be liquid"),
        _g("dte", c["dte_min"] <= dte <= c["dte_max"], dte, c["dte_max"], "Days-to-expiry window"),
        _g("positive_credit", credit > 0, round(credit, 2), 0, "Spread must take in net credit"),
    ]
    fired = all(g["passed"] for g in gates)
    return {
        "fired": fired,
        "expiry": expiry, "dte": dte,
        "short_strike": short["strike"], "long_strike": long["strike"],
        "short_premium": round(short["close"], 2), "long_premium": round(long["close"], 2),
        "short_delta": round(short["delta"], 3),
        "width": width, "credit": round(credit, 2),
        "max_profit": round(max_profit, 2), "max_loss": round(max_loss, 2),
        "capital_at_risk": round(max_loss, 2),
        "breakeven": round(breakeven, 2), "prob_profit_pct": round(pop, 1),
        "vrp": vrp, "iv_rv_ratio": round(iv_rv, 3) if iv_rv else None,
        "gates": gates,
        "reasons": [f"{g['name']}: {g['value']} vs {g['threshold']}" for g in gates if not g["passed"]],
    }


# ── Calendar spread (volatility term structure) ─────────────────────────────

def calendar(all_puts, spot, today, r, q, cfg):
    by_exp = {}
    for p in all_puts:
        by_exp.setdefault(p["expiry"], []).append(p)
    # two nearest expiries with enough strikes, front then next
    exps = sorted(e for e in by_exp if (e - today).days >= 3)
    if len(exps) < 2:
        return {"fired": False, "reasons": ["Need two future expiries for a calendar."]}
    front, nxt = exps[0], exps[1]

    def atm_iv(exp):
        puts = by_exp[exp]
        dte = (exp - today).days
        T = max(dte, 1) / 365.0
        atm = min(puts, key=lambda x: abs(x["strike"] - spot))
        return bs.implied_vol_put(atm["close"], spot, atm["strike"], T, r, q), atm, dte

    fiv, fatm, fdte = atm_iv(front)
    niv, natm, ndte = atm_iv(nxt)
    if fiv is None or niv is None:
        return {"fired": False, "reasons": ["Could not compute ATM IV for both expiries."]}

    spread_pts = (fiv - niv) * 100.0
    thr = cfg["calendar"]["front_rich_threshold_pts"]
    fired = spread_pts >= thr
    debit = natm["close"] - fatm["close"]  # buy next, sell front → net debit (if >0)
    return {
        "fired": fired,
        "front_expiry": front.isoformat(), "next_expiry": nxt.isoformat(),
        "front_dte": fdte, "next_dte": ndte,
        "front_atm_iv": round(fiv, 4), "next_atm_iv": round(niv, 4),
        "term_spread_pts": round(spread_pts, 2), "threshold_pts": thr,
        "atm_strike": fatm["strike"],
        "est_debit_per_lot": round(debit * cfg["market"]["lot_size"], 2),
        "reasons": [] if fired else [f"term_spread {round(spread_pts,2)} < {thr} pts (front not rich enough)"],
    }


# ── ETF sector momentum (buy-only) ──────────────────────────────────────────

def etf_momentum(session, cfg):
    e = cfg["etf_momentum"]
    rows = []
    for sym in e["basket"]:
        try:
            hist = nse.yahoo_history(session, sym, "1y")
        except nse.DataUnavailable:
            continue
        closes = [c for _, c in hist]
        if len(closes) < e["lookback_6m_days"] + 1:
            # still usable for 3m if long enough
            pass
        last = closes[-1]
        r3 = _ret(closes, e["lookback_3m_days"])
        r6 = _ret(closes, e["lookback_6m_days"])
        parts = [x for x in (r3, r6) if x is not None]
        blended = sum(parts) / len(parts) if parts else None
        rows.append({"symbol": sym.replace(".NS", ""), "last": round(last, 2),
                     "ret_3m_pct": r3, "ret_6m_pct": r6, "momentum_pct": blended})
    ranked = sorted([r for r in rows if r["momentum_pct"] is not None],
                    key=lambda x: x["momentum_pct"], reverse=True)
    top = ranked[: e["top_n"]]

    # Concrete trade plan: equal-rupee split, whole units, trailing stop.
    budget = e.get("budget_inr", 0)
    ts = e.get("trailing_stop_pct", 12)
    alloc = (budget / len(top)) if top else 0
    plan = []
    for r in top:
        units = int(alloc // r["last"]) if r["last"] > 0 else 0
        plan.append({
            "symbol": r["symbol"], "price": r["last"], "units": units,
            "cost": round(units * r["last"], 2),
            "stop_price": round(r["last"] * (1 - ts / 100.0), 2),
        })
    return {
        "fired": bool(top),
        "top_n": e["top_n"],
        "hold": [r["symbol"] for r in top],
        "ranked": ranked,
        "trade_plan": plan,
        "budget_inr": budget,
        "trailing_stop_pct": ts,
        "holdings": _mark_holdings({r["symbol"]: r["last"] for r in rows}),
        "rules": {
            "buy": "Use a LIMIT order near the shown price (ETFs can have wide spreads — avoid market orders). Buy whole units, equal rupee split.",
            "sizing": "ETFs trade in single units — there is NO F&O lot. Quantity = budget ÷ top_n ÷ price, rounded down.",
            "stop": f"Optional trailing stop: exit a name if it drops {ts}% from your entry (or its recent high). Caps drawdown between rebalances.",
            "sell": "Primary exit is the MONTHLY rebalance: re-check /etf on the 1st trading day; if a holding fell out of the top " + str(e["top_n"]) + ", sell it and rotate into the new leader.",
            "cadence": "Momentum is a monthly strategy — do NOT watch or trade it daily.",
        },
        "note": "Buy-only, equal-weight the top names, rebalance monthly. No leverage.",
        "reasons": [] if top else ["No ETF price data available."],
    }


def _ret(closes, lookback):
    if len(closes) <= lookback:
        return None
    past = closes[-(lookback + 1)]
    if past <= 0:
        return None
    return round(100.0 * (closes[-1] / past - 1.0), 2)


def _mark_holdings(price_map, path="data/etf_holdings.json"):
    """Load real-money ETF holdings and mark them to market with live prices.

    `price_map` is {symbol: last_price} from the momentum fetch. Returns a dict
    with per-holding P&L and a summary, or None if there are no holdings.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return None
    rows, cost_t, value_t = [], 0.0, 0.0
    for h in data.get("holdings", []):
        sym, units, buy = h["symbol"], h["units"], h["buy_price"]
        now = price_map.get(sym)
        cost = units * buy
        value = units * now if now is not None else None
        pnl = (value - cost) if value is not None else None
        rows.append({
            "symbol": sym, "units": units, "buy_price": round(buy, 2),
            "now": round(now, 2) if now is not None else None,
            "cost": round(cost, 2), "value": round(value, 2) if value is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(100.0 * pnl / cost, 2) if (pnl is not None and cost) else None,
            "buy_date": h.get("buy_date"),
        })
        cost_t += cost
        if value is not None:
            value_t += value
    if not rows:
        return None
    pnl_t = value_t - cost_t
    return {
        "rows": rows,
        "total_cost": round(cost_t, 2), "total_value": round(value_t, 2),
        "total_pnl": round(pnl_t, 2),
        "total_pnl_pct": round(100.0 * pnl_t / cost_t, 2) if cost_t else None,
        "note": data.get("note", ""),
    }


def _g(name, passed, value, threshold, detail):
    return {"name": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


# ── Orchestrator ────────────────────────────────────────────────────────────

def run_strategies(cfg, on: date | None = None) -> dict:
    on = on or date.today()
    m = cfg["market"]
    r, q = m["risk_free_rate"], m["dividend_yield"]
    out = {"date": on.isoformat(), "generated_at": datetime.utcnow().isoformat() + "Z",
           "etf_momentum": None, "credit_spread": None, "calendar": None, "notes": []}

    session = nse.nse_session()

    # ETF momentum works regardless of NSE option availability (Yahoo only)
    try:
        out["etf_momentum"] = etf_momentum(session, cfg)
    except Exception as e:  # keep the file writing even if one strategy fails
        out["etf_momentum"] = {"fired": False, "reasons": [f"etf error: {e}"]}

    # Option strategies need the chain
    try:
        rows = nse.download_fo_bhavcopy(session, on)
        chain = nse.extract_nifty_puts(rows, m["underlying"])
        puts, spot = chain["puts"], chain["spot"]
        if spot is None:
            raise nse.DataUnavailable("no spot in chain")

        # vix percentile + realized vol (Yahoo, cached by the main scan too)
        vix_pct = rv = None
        try:
            vh = [v for _, v in nse.get_vix_history(session, cfg["vix"]["lookback_days"])]
            vix_pct = mx.percentile_rank(vh[-1], vh[-cfg["vix"]["lookback_days"]:])
        except nse.DataUnavailable:
            pass
        try:
            nh = [c for _, c in nse.get_nifty_history(session, cfg["realized_vol"]["window_days"] + 10)]
            rv = mx.realized_vol(nh, cfg["realized_vol"]["window_days"], m["trading_days_year"])
        except nse.DataUnavailable:
            pass

        # choose the monthly expiry in the credit-spread DTE band (max OI)
        cband = cfg["credit_spread"]
        by_exp = {}
        for p in puts:
            d = (p["expiry"] - on).days
            if cband["dte_min"] <= d <= cband["dte_max"]:
                by_exp[p["expiry"]] = by_exp.get(p["expiry"], 0.0) + p["open_interest"]
        if by_exp:
            expiry = max(by_exp.items(), key=lambda kv: kv[1])[0]
            dte = (expiry - on).days
            exp_puts = [p for p in puts if p["expiry"] == expiry]
            atm = min(exp_puts, key=lambda x: abs(x["strike"] - spot))
            atm_iv = bs.implied_vol_put(atm["close"], spot, atm["strike"], max(dte, 1) / 365.0, r, q)
            out["credit_spread"] = credit_spread(exp_puts, spot, expiry.isoformat(), dte,
                                                  atm_iv, rv, vix_pct, r, q, cfg)
        else:
            out["credit_spread"] = {"fired": False, "reasons": ["No expiry in the credit-spread DTE band."]}

        out["calendar"] = calendar(puts, spot, on, r, q, cfg)
        out["spot"] = spot
    except nse.DataUnavailable as e:
        out["notes"].append(f"Option strategies unavailable ({e}) — likely a holiday/pre-close. ETF momentum still valid.")
        out["credit_spread"] = {"fired": False, "reasons": [str(e)]}
        out["calendar"] = {"fired": False, "reasons": [str(e)]}

    out["disclaimer"] = cfg.get("disclaimer")
    _write_json(os.path.join(cfg["output"]["dashboard_data_dir"], "strategies.json"), out)
    return out
