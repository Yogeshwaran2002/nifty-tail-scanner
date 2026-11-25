"""Daily scan orchestrator — the deterministic brain.

Pipeline: fetch EOD data → compute metrics → run entry gates → rank candidates
→ mark open paper trades to market and run exit rules → (optionally) auto-open
the top candidate as a paper trade → emit a signal JSON for the dashboard and
Telegram.

Degrades gracefully: if VIX or index history can't be fetched, the dependent
gate FAILS CLOSED (no signal) rather than firing on missing data.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

from . import blackscholes as bs
from . import journal as jnl
from . import metrics as mx
from . import nse_data as nse
from . import rules


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)


def _merge_series_cache(path: str, fetched: list[tuple]) -> list[tuple]:
    """Merge a freshly fetched [(date, value), ...] series into an on-disk cache
    (keyed by ISO date) and return the merged, date-sorted series. Lets the
    percentile window grow over time and bridges days when a fetch returns a
    short window. A failed fetch (empty list) just returns the cached series."""
    cache: dict[str, float] = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except json.JSONDecodeError:
            cache = {}
    for d, v in fetched:
        cache[d.isoformat() if hasattr(d, "isoformat") else str(d)] = v
    _write_json(path, cache)
    merged = sorted(((date.fromisoformat(k), v) for k, v in cache.items()), key=lambda t: t[0])
    return merged


def _pick_monthly_expiry(puts: list[dict], today: date, cfg: dict):
    """Among expiries whose DTE is in the configured band, pick the one with the
    most open interest — the monthly series dwarfs weeklies in OI."""
    band = cfg["universe"]
    by_exp: dict[date, float] = {}
    for p in puts:
        dte = (p["expiry"] - today).days
        if band["dte_min"] <= dte <= band["dte_max"]:
            by_exp[p["expiry"]] = by_exp.get(p["expiry"], 0.0) + p["open_interest"]
    if not by_exp:
        return None
    return max(by_exp.items(), key=lambda kv: kv[1])[0]


def run_scan(cfg: dict, on: date | None = None, *, archive: bool = True) -> dict:
    on = on or date.today()
    m = cfg["market"]
    r, q = m["risk_free_rate"], m["dividend_yield"]

    signal: dict = {
        "date": on.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "fired": False,
        "spot": None, "vix": None, "vix_percentile": None, "realized_vol": None,
        "expiry": None, "candidates": [], "reasons": [], "exit_signals": [],
        "errors": [], "warnings": [],
    }

    session = nse.nse_session()

    # 1) Option chain
    try:
        rows = nse.download_fo_bhavcopy(session, on)
        chain = nse.extract_nifty_puts(rows, m["underlying"])
    except nse.DataUnavailable as e:
        signal["errors"].append(f"Option data: {e}")
        return _finalize(signal, cfg)

    puts = chain["puts"]
    spot = chain["spot"]

    # 2) VIX history → current + percentile (cached). Fails closed if we can't
    #    get a VIX value dated on/near the scan date.
    try:
        vixh = nse.get_vix_history(session, cfg["vix"]["lookback_days"])
    except nse.DataUnavailable as e:
        signal["warnings"].append(f"VIX: {e}")
        vixh = []
    vmerged = _merge_series_cache(os.path.join("data", "vix_history.json"), vixh)
    vix_window = [(d, v) for d, v in vmerged if d <= on][-cfg["vix"]["lookback_days"]:]
    if vix_window and (on - vix_window[-1][0]).days <= 5:
        signal["vix"] = vix_window[-1][1]
        signal["vix_percentile"] = mx.percentile_rank(vix_window[-1][1], [v for _, v in vix_window])
    elif not any("VIX:" in w for w in signal["warnings"]):
        signal["warnings"].append("VIX: no value dated near the scan date — gate fails closed.")

    # 3) NIFTY history → realized vol + spot fallback (cached)
    try:
        nh = nse.get_nifty_history(session, cfg["realized_vol"]["window_days"] + 10)
    except nse.DataUnavailable as e:
        signal["warnings"].append(f"NIFTY index: {e}")
        nh = []
    nmerged = _merge_series_cache(os.path.join("data", "nifty_history.json"), nh)
    nwindow = [(d, c) for d, c in nmerged if d <= on]
    closes = [c for _, c in nwindow]
    if closes:
        signal["realized_vol"] = mx.realized_vol(closes, cfg["realized_vol"]["window_days"],
                                                 m["trading_days_year"])
        if spot is None:
            spot = closes[-1]

    signal["spot"] = spot
    if spot is None:
        signal["reasons"].append("No spot price available — cannot compute moneyness.")
        return _finalize(signal, cfg)

    # 4) Target expiry
    expiry = _pick_monthly_expiry(puts, on, cfg)
    if expiry is None:
        signal["reasons"].append(
            f"No expiry with {cfg['universe']['dte_min']}–{cfg['universe']['dte_max']} DTE "
            f"in today's chain.")
        return _finalize(signal, cfg)
    signal["expiry"] = expiry.isoformat()
    dte = (expiry - on).days
    T = max(dte, 1) / 365.0

    exp_puts = [p for p in puts if p["expiry"] == expiry]
    strikes = [p["strike"] for p in exp_puts]
    if not strikes:
        signal["reasons"].append("Chosen expiry had no put strikes.")
        return _finalize(signal, cfg)

    # 5) ATM IV
    atm_strike = mx.nearest(strikes, spot)
    atm_put = next(p for p in exp_puts if p["strike"] == atm_strike)
    atm_iv = bs.implied_vol_put(atm_put["close"], spot, atm_strike, T, r, q)
    rv = signal["realized_vol"]

    # 6) Evaluate OTM-band candidates
    u = cfg["universe"]
    lo_k, hi_k = spot * (1 - u["otm_max_pct"] / 100.0), spot * (1 - u["otm_min_pct"] / 100.0)
    candidates = []
    for p in exp_puts:
        if not (lo_k <= p["strike"] <= hi_k):
            continue
        iv = bs.implied_vol_put(p["close"], spot, p["strike"], T, r, q)
        cost_per_lot = p["close"] * m["lot_size"]
        breakeven = p["strike"] - p["close"]
        metrics = {
            "strike": p["strike"], "expiry": expiry.isoformat(), "dte": dte,
            "premium": p["close"], "iv": iv, "atm_iv": atm_iv,
            "skew": mx.put_skew(atm_iv, iv),
            "iv_rv_ratio": mx.iv_rv_ratio(atm_iv, rv),
            "open_interest": p["open_interest"], "volume": p["volume"],
            "cost_per_lot": cost_per_lot,
            "vix_percentile": signal["vix_percentile"],
            "breakeven": breakeven,
            "breakeven_move_pct": 100.0 * (spot - breakeven) / spot,
        }
        gates = rules.evaluate_entry_gates(metrics, cfg)
        metrics["gates"] = [g.to_dict() for g in gates]
        metrics["passed"] = rules.passes_all(gates)
        candidates.append(metrics)

    passing = [c for c in candidates if c["passed"]]
    passing.sort(key=rules.cheapness_score)
    signal["candidates"] = passing
    signal["all_evaluated"] = candidates
    signal["fired"] = bool(passing)

    if not passing:
        signal["reasons"] = _failure_reasons(candidates)

    # Volatility smile / skew curve + volatility risk premium (for the dashboard
    # and the "genuinely cheap?" judgement — idea.md's central correction).
    signal["atm_iv"] = atm_iv
    signal["atm_strike"] = atm_strike
    signal["vrp"] = mx.vol_risk_premium(atm_iv, rv)
    curve = []
    for p in sorted(exp_puts, key=lambda x: x["strike"]):
        if not (spot * 0.80 <= p["strike"] <= spot * 1.05):
            continue
        iv = bs.implied_vol_put(p["close"], spot, p["strike"], T, r, q)
        if iv is not None:
            curve.append({"strike": p["strike"], "moneyness": round(p["strike"] / spot, 4),
                          "iv": round(iv, 4)})
    signal["skew_curve"] = curve

    # 7) Journal: mark open trades, run exits, auto-open top candidate
    _update_journal(signal, cfg, exp_puts_by_strike={p["strike"]: p for p in exp_puts},
                    all_puts=puts, on=on, dte_lookup=lambda e: (e - on).days)

    # 8) Archive raw chain for the backtest dataset
    if archive:
        _archive(on, rows_count=len(puts), chain=exp_puts, spot=spot,
                 vix=signal["vix"], expiry=expiry)

    return _finalize(signal, cfg)


def _failure_reasons(candidates: list[dict]) -> list[str]:
    """Human-readable summary of which gates blocked the closest candidate."""
    if not candidates:
        return ["No put strikes fell in the OTM band."]
    # Report the gate failures of the strike nearest the middle of the band.
    best = min(candidates, key=rules.cheapness_score)
    out = []
    for g in best["gates"]:
        if not g["passed"]:
            val = "n/a" if g["value"] is None else f"{g['value']:.2f}"
            out.append(f"{g['name']}: {val} vs limit {g['threshold']} ({g['detail']})")
    return out or ["All gates passed on evaluation but no candidate qualified."]


def _update_journal(signal, cfg, *, exp_puts_by_strike, all_puts, on, dte_lookup):
    j = jnl.load()
    # mark-to-market + exits for open trades
    all_by_key = {(p["strike"], p["expiry"].isoformat()): p for p in all_puts}
    for t in jnl.open_trades(j):
        key = (t["strike"], t["expiry"])
        cur = all_by_key.get(key)
        if cur is None:
            continue
        dte = dte_lookup(cur["expiry"])
        decision = rules.evaluate_exit(t, cur["close"], signal["vix_percentile"], dte, cfg)
        if decision:
            signal["exit_signals"].append({"id": t["id"], "strike": t["strike"],
                                           "action": decision["action"],
                                           "reason": decision["reason"],
                                           "current_premium": cur["close"]})
            jnl.close_trade(j, t["id"], exit_premium=cur["close"],
                            reason=decision["action"], cfg=cfg, on=on)

    # auto-open the top candidate (paper), if enabled and not already held
    if cfg["paper"]["enabled"] and signal["fired"] and not j.get("locked"):
        top = signal["candidates"][0]
        held = any(t["status"] == "OPEN" and t["strike"] == top["strike"]
                   and t["expiry"] == top["expiry"] for t in j["trades"])
        if not held:
            res = jnl.open_trade(j, strike=top["strike"], expiry=top["expiry"],
                                 entry_premium=top["premium"],
                                 lots=cfg["paper"]["default_lots_per_trade"],
                                 lot_size=cfg["market"]["lot_size"], cfg=cfg, on=on)
            if res["ok"]:
                signal["paper_opened"] = res["trade"]

    signal["budget"] = jnl.budget_status(j, cfg)
    signal["open_trades"] = jnl.open_trades(j)
    jnl.save(j)


def _archive(on, *, rows_count, chain, spot, vix, expiry):
    path = os.path.join("data", "eod", f"{on.isoformat()}.json")
    _write_json(path, {
        "date": on.isoformat(), "spot": spot, "vix": vix,
        "expiry": expiry.isoformat(), "put_count": rows_count,
        "puts": [{"strike": p["strike"], "close": p["close"],
                  "open_interest": p["open_interest"], "volume": p["volume"],
                  "expiry": p["expiry"].isoformat()} for p in chain],
    })


def _finalize(signal, cfg) -> dict:
    """Write signal to signals_dir, dashboard latest.json, and history index.

    On a data-unavailable day (market holiday, weekend, pre-publication, or a
    blocked fetch) the scan has no spot — in that case DON'T touch the dashboard,
    so it keeps showing the last good scan instead of flashing n/a.
    """
    signal.setdefault("disclaimer", cfg.get("disclaimer"))
    if signal.get("spot") is None and signal.get("errors"):
        return signal
    out = cfg["output"]
    _write_json(os.path.join(out["signals_dir"], f"{signal['date']}.json"), signal)
    _write_json(os.path.join(out["dashboard_data_dir"], "latest.json"), signal)
    _append_history(signal, out)
    return signal


def _append_history(signal, out):
    idx_path = os.path.join(out["dashboard_data_dir"], "history.json")
    history = []
    if os.path.exists(idx_path):
        try:
            with open(idx_path, encoding="utf-8") as f:
                history = json.load(f)
        except json.JSONDecodeError:
            history = []
    history = [h for h in history if h.get("date") != signal["date"]]
    history.append({
        "date": signal["date"], "fired": signal["fired"],
        "vix": signal.get("vix"), "vix_percentile": signal.get("vix_percentile"),
        "spot": signal.get("spot"), "candidate_count": len(signal.get("candidates", [])),
    })
    history.sort(key=lambda h: h["date"])
    history = history[-out["history_keep_days"]:]
    _write_json(idx_path, history)
