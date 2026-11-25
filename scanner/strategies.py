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

import calendar as _calendar
import json
import os
from datetime import date, datetime, timedelta

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
        "reasons": [f"{g['name']}: {round(g['value'], 2) if isinstance(g['value'], (int, float)) else g['value']} vs {g['threshold']}"
                    for g in gates if not g["passed"]],
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
    sma_days = e.get("trend_sma_days", 200)
    use_trend = e.get("trend_filter", True)
    cash_sym = e.get("cash_symbol", "LIQUIDBEES")
    vol_lb = e.get("vol_lookback_days", 60)
    weighting = e.get("weighting", "inverse_vol")
    rows = []
    for sym in e["basket"]:
        try:
            hist = nse.yahoo_history(session, sym, "1y")
        except nse.DataUnavailable:
            continue
        closes = [c for _, c in hist]
        last = closes[-1]
        r3 = _ret(closes, e["lookback_3m_days"])
        r6 = _ret(closes, e["lookback_6m_days"])
        parts = [x for x in (r3, r6) if x is not None]
        blended = sum(parts) / len(parts) if parts else None
        sma = sum(closes[-sma_days:]) / sma_days if len(closes) >= sma_days else None
        trend = None if sma is None else ("up" if last > sma else "down")
        vol = mx.realized_vol(closes, vol_lb, 252)
        rows.append({"symbol": sym.replace(".NS", ""), "last": round(last, 2),
                     "ret_3m_pct": r3, "ret_6m_pct": r6, "momentum_pct": blended,
                     "sma200": round(sma, 2) if sma is not None else None, "trend": trend,
                     "vol_pct": round(vol * 100, 1) if vol is not None else None})
    ranked = sorted([r for r in rows if r["momentum_pct"] is not None],
                    key=lambda x: x["momentum_pct"], reverse=True)
    top = ranked[: e["top_n"]]

    # cash instrument price (for down-trend slices)
    cash_price = None
    try:
        cash_price = nse.yahoo_history(session, cash_sym + ".NS", "3mo")[-1][1]
    except nse.DataUnavailable:
        pass

    budget = e.get("budget_inr", 0)
    ts = e.get("trailing_stop_pct", 12)
    n = len(top)
    base = (budget / n) if n else 0

    # Low-vol tilt: redistribute the up-trend ETF budget inversely to volatility.
    up = [r for r in top if not (use_trend and r["trend"] == "down")]
    inv_w = {}
    if weighting == "inverse_vol" and up and all((r.get("vol_pct") or 0) > 0 for r in up):
        denom = sum(1.0 / r["vol_pct"] for r in up)
        for r in up:
            inv_w[r["symbol"]] = (1.0 / r["vol_pct"]) / denom
    etf_pool = base * len(up)

    plan, hold, in_cash = [], [], 0
    for r in top:
        downtrend = use_trend and r["trend"] == "down"
        if downtrend:
            in_cash += 1
            units = int(base // cash_price) if cash_price else 0
            plan.append({"symbol": cash_sym, "price": round(cash_price, 2) if cash_price else None,
                         "units": units, "cost": round(units * cash_price, 2) if cash_price else None,
                         "weight_pct": round(100.0 / n, 1) if n else None,
                         "stop_price": None, "cash": True, "replaces": r["symbol"]})
            hold.append(f"{cash_sym} (cash — {r['symbol']} below {sma_days}-SMA)")
        else:
            alloc_i = etf_pool * inv_w[r["symbol"]] if r["symbol"] in inv_w else base
            units = int(alloc_i // r["last"]) if r["last"] > 0 else 0
            plan.append({"symbol": r["symbol"], "price": r["last"], "units": units,
                         "cost": round(units * r["last"], 2),
                         "weight_pct": round(100.0 * alloc_i / budget, 1) if budget else None,
                         "vol_pct": r.get("vol_pct"),
                         "stop_price": round(r["last"] * (1 - ts / 100.0), 2), "cash": False})
            hold.append(r["symbol"])

    price_map = {r["symbol"]: r["last"] for r in rows}
    if cash_price:
        price_map[cash_sym] = cash_price
    return {
        "fired": bool(top),
        "top_n": e["top_n"],
        "trend_filter": use_trend,
        "trend_sma_days": sma_days,
        "in_cash_slots": in_cash,
        "hold": hold,
        "ranked": ranked,
        "trade_plan": plan,
        "budget_inr": budget,
        "trailing_stop_pct": ts,
        "holdings": _mark_holdings(price_map),
        "rules": {
            "buy": "Use a LIMIT order near the shown price (ETFs can have wide spreads — avoid market orders). Buy whole units.",
            "sizing": "ETFs trade in single units — there is NO F&O lot. Qty = allocation ÷ price, rounded down.",
            "tilt": ("Low-volatility tilt ON: the budget is split INVERSELY to each ETF's volatility — "
                     "calmer names get more money (see Weight %). Smoother ride, lower drawdown."
                     if weighting == "inverse_vol" else "Equal-weight across holdings."),
            "trend": f"Trend filter ON: a top-ranked ETF is only held while above its {sma_days}-day SMA; otherwise that slice sits in {cash_sym} (cash). This caps drawdowns.",
            "stop": f"Optional trailing stop: exit a name if it drops {ts}% from your entry (or its recent high).",
            "sell": "Primary exit is the MONTHLY rebalance: re-check /etf on the 1st trading day; sell anything that fell out of the top " + str(e["top_n"]) + " or dropped below its " + str(sma_days) + "-SMA.",
            "cadence": "Momentum is a monthly strategy — do NOT watch or trade it daily.",
        },
        "note": "Buy-only, equal-weight the top names (cash when a name is down-trending), rebalance monthly. No leverage.",
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


def _month_window(y, m, last_days, first_days):
    """Calendar (weekday) turn-of-month window for one month: first `first_days`
    weekdays + last `last_days` weekdays. Calendar-based so it doesn't depend on
    how recent the price data is (the true last day of a month is only knowable
    from the calendar, not from 'the latest bar we happen to have')."""
    firsts, d = [], date(y, m, 1)
    while len(firsts) < first_days:
        if d.weekday() < 5:
            firsts.append(d)
        d += timedelta(days=1)
    lasts, d = [], date(y, m, _calendar.monthrange(y, m)[1])
    while len(lasts) < last_days:
        if d.weekday() < 5:
            lasts.append(d)
        d -= timedelta(days=1)
    return sorted(set(firsts) | set(lasts))


def _calendar_tom_status(on, last_days, first_days):
    """(in_window_today, next_window_start_iso) using the calendar window."""
    wins = []
    for k in range(0, 3):
        mm = on.month + k
        yy, mm = on.year + (mm - 1) // 12, (mm - 1) % 12 + 1
        wins += _month_window(yy, mm, last_days, first_days)
    wins = sorted(set(wins))
    future = [d for d in wins if d > on]
    return (on in wins), (future[0].isoformat() if future else None)


def tom_window_dates(dates, last_days, first_days):
    """Given a sorted list of trading dates, return the set that falls in a
    turn-of-the-month window: the last `last_days` of each month plus the first
    `first_days` of each month. Pure + testable."""
    by_month = {}
    for d in sorted(dates):
        by_month.setdefault((d.year, d.month), []).append(d)
    win = set()
    for ds in by_month.values():
        for d in ds[:first_days]:
            win.add(d)
        for d in ds[-last_days:]:
            win.add(d)
    return win


def turn_of_month(session, cfg, on):
    """Turn-of-the-Month timing on a broad index ETF. In the ~4-day window →
    hold the ETF; otherwise cash. Backtested on NIFTY itself (India proof)."""
    t = cfg["turn_of_month"]
    try:
        hist = nse.yahoo_history(session, t["index_symbol"], "2y")
    except nse.DataUnavailable as e:
        return {"fired": False, "reasons": [str(e)]}
    dates = [d for d, _ in hist]
    closes = [c for _, c in hist]
    win = tom_window_dates(dates, t["last_days"], t["first_days"])

    # Live signal from the CALENDAR (robust to data recency); backtest below uses
    # observed trading days (historical months are complete, so that's accurate).
    in_today, next_start = _calendar_tom_status(on, t["last_days"], t["first_days"])

    cash_d = (1 + t.get("cash_annual_yield_pct", 6.0) / 100.0) ** (1 / 252.0) - 1
    eq_tom, eq_bh = 1.0, 1.0
    cur_tom, cur_bh = [1.0], [1.0]
    in_days = 0
    for i in range(len(closes) - 1):
        r = closes[i + 1] / closes[i] - 1
        if dates[i] in win:
            eq_tom *= (1 + r); in_days += 1
        else:
            eq_tom *= (1 + cash_d)
        eq_bh *= (1 + r)
        cur_tom.append(eq_tom); cur_bh.append(eq_bh)
    n = len(closes) - 1

    def stats(curve, equity):
        peak = dd = 0.0
        for v in curve:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        yrs = n / 252.0 if n else 1
        cagr = equity ** (1 / yrs) - 1 if yrs > 0 else 0
        return {"total_return_pct": round((equity - 1) * 100, 1),
                "cagr_pct": round(cagr * 100, 1), "max_drawdown_pct": round(dd * 100, 1)}

    return {
        "fired": in_today,
        "etf": t["etf_symbol"],
        "window": f"last {t['last_days']} + first {t['first_days']} trading days of the month",
        "in_market_pct": round(100.0 * in_days / n, 1) if n else 0,
        "next_window_start": next_start,
        "backtest": {"days": n, "tom": stats(cur_tom, eq_tom), "buyhold": stats(cur_bh, eq_bh),
                     "equity_curve": [{"i": i, "equity": round(v, 4)} for i, v in enumerate(cur_tom) if i % 3 == 0]},
        "note": "Hold the index ETF only in the window, cash otherwise. ToM is a documented "
                "global seasonal effect (Lakonishok & Smidt 1988); backtest here is on NIFTY. "
                "In the market only ~4 days/month → small drawdown. Not advice.",
        "reasons": [] if in_today else [f"outside the window; next starts {next_start}"],
    }


def etf_momentum_backtest(session, cfg):
    """Monthly-rebalance momentum backtest over ~2y of Yahoo ETF history, run
    twice: plain momentum vs momentum + absolute-momentum trend filter (hold a
    top-N name only while above its SMA, else that slice earns cash). Returns
    both so the drawdown reduction from the filter is visible."""
    e = cfg["etf_momentum"]
    l3, l6, topn = e["lookback_3m_days"], e["lookback_6m_days"], e["top_n"]
    sma_days = e.get("trend_sma_days", 200)
    vol_lb = e.get("vol_lookback_days", 60)
    weighting = e.get("weighting", "inverse_vol")
    cash_m = (1 + e.get("cash_annual_yield_pct", 6.0) / 100.0) ** (1 / 12.0) - 1
    series = {}
    for sym in e["basket"]:
        try:
            series[sym.replace(".NS", "")] = {d: c for d, c in nse.yahoo_history(session, sym, "2y")}
        except nse.DataUnavailable:
            continue
    if len(series) < topn:
        return {"ok": False, "reason": "Not enough ETF history for a backtest."}
    common = set.intersection(*[set(s.keys()) for s in series.values()])
    dates = sorted(common)
    start = max(l6, sma_days)
    if len(dates) < start + 25:
        return {"ok": False, "reason": f"Only {len(dates)} aligned days — need ~{start + 25}."}

    def sim(filtered):
        step = 21
        equity = 1.0
        curve = [{"date": dates[start].isoformat(), "equity": 1.0}]
        rets = []
        i = start
        while i + step < len(dates):
            di, dnext = dates[i], dates[i + step]
            ranked = []
            for sym, s in series.items():
                cn, c3, c6 = s[di], s[dates[i - l3]], s[dates[i - l6]]
                mom = ((cn / c3 - 1) + (cn / c6 - 1)) / 2 if c3 > 0 and c6 > 0 else None
                if mom is not None:
                    ranked.append((sym, mom))
            ranked.sort(key=lambda x: x[1], reverse=True)
            held = [sym for sym, _ in ranked[:topn]]
            # inverse-vol weights from trailing vol (fallback equal)
            def vol_of(sym):
                cs = [series[sym][dates[j]] for j in range(i - vol_lb, i + 1)]
                rs = [math.log(cs[t] / cs[t - 1]) for t in range(1, len(cs)) if cs[t - 1] > 0]
                if len(rs) < 2:
                    return None
                mu = sum(rs) / len(rs)
                return (sum((x - mu) ** 2 for x in rs) / (len(rs) - 1)) ** 0.5
            weights = {}
            if weighting == "inverse_vol":
                vs = {s: vol_of(s) for s in held}
                if held and all(vs[s] and vs[s] > 0 for s in held):
                    tot = sum(1.0 / vs[s] for s in held)
                    weights = {s: (1.0 / vs[s]) / tot for s in held}
            if not weights:
                weights = {s: 1.0 / len(held) for s in held} if held else {}
            port = 0.0
            for sym in held:
                s = series[sym]
                if filtered:
                    sma = sum(s[dates[j]] for j in range(i - sma_days + 1, i + 1)) / sma_days
                    if s[di] <= sma:        # down-trend → cash
                        port += weights[sym] * cash_m
                        continue
                port += weights[sym] * (s[dnext] / s[di] - 1)
            equity *= (1 + port)
            rets.append(port)
            curve.append({"date": dnext.isoformat(), "equity": round(equity, 4)})
            i += step
        peak = dd = 0.0
        for pt in curve:
            peak = max(peak, pt["equity"])
            dd = min(dd, pt["equity"] / peak - 1)
        wins = sum(1 for r in rets if r > 0)
        cagr = (equity ** (12.0 / len(rets)) - 1) if rets else 0.0
        return {
            "months": len(rets),
            "total_return_pct": round((equity - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 1),
            "best_month_pct": round(max(rets) * 100, 1) if rets else 0,
            "worst_month_pct": round(min(rets) * 100, 1) if rets else 0,
            "max_drawdown_pct": round(dd * 100, 1),
            "win_rate_pct": round(100.0 * wins / len(rets), 1) if rets else 0,
            "equity_curve": curve,
        }

    plain = sim(filtered=False)
    filtered = sim(filtered=True)
    return {
        "ok": True,
        "plain": plain,
        "filtered": filtered,
        "note": f"Trend filter holds a top-{topn} ETF only while above its {sma_days}-day SMA, "
                "else cash. Compare max drawdown: the filter trades a little return for a much "
                "smoother ride. Past performance is not a promise — judge the method, size small.",
    }


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

    # ETF momentum backtest (buy-only, always produces an equity curve)
    try:
        out["etf_backtest"] = etf_momentum_backtest(session, cfg)
    except Exception as e:
        out["etf_backtest"] = {"ok": False, "reason": f"etf backtest error: {e}"}

    # Turn-of-the-Month (seasonal index timing)
    try:
        out["turn_of_month"] = turn_of_month(session, cfg, on)
    except Exception as e:
        out["turn_of_month"] = {"fired": False, "reasons": [f"tom error: {e}"]}

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
        try:  # prefer Yang–Zhang (OHLC) when available
            bars = [b for b in nse.yahoo_ohlc(session, nse.YF_NIFTY, "6mo") if b["date"] <= on]
            yz = mx.yang_zhang_vol(bars, cfg["realized_vol"]["window_days"], m["trading_days_year"])
            if yz is not None:
                rv = yz
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
        # Option data isn't published (holiday / weekend / before the ~18:00 IST
        # close). Don't clobber the dashboard with a raw 404 — keep the last good
        # option signals if we have them, else show a clean message.
        prev = {}
        try:
            with open(os.path.join(cfg["output"]["dashboard_data_dir"], "strategies.json"), encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        msg = "Options data publishes after the NSE close (~18:00 IST) on trading days — showing the last scan."
        out["notes"].append(f"Option chain unavailable (holiday/pre-close). ETF + ToM still live.")
        out["credit_spread"] = prev.get("credit_spread") or {"fired": False, "reasons": [msg]}
        out["calendar"] = prev.get("calendar") or {"fired": False, "reasons": [msg]}
        if prev.get("spot"):
            out["spot"] = prev["spot"]

    out["disclaimer"] = cfg.get("disclaimer")
    _write_json(os.path.join(cfg["output"]["dashboard_data_dir"], "strategies.json"), out)
    return out
