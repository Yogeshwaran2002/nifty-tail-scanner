"""Derived metrics: realized volatility, percentile rank, skew, IV/RV richness.

All pure functions over plain lists so they are trivially testable and identical
under backtest and live scan.
"""

from __future__ import annotations

import math
from typing import Sequence


def log_returns(closes: Sequence[float]) -> list[float]:
    out = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def realized_vol(closes: Sequence[float], window: int, trading_days: int = 252) -> float | None:
    """Annualized realized volatility from the trailing `window` sessions.

    Returns a FRACTION (e.g. 0.14 = 14%), matching how implied vol is expressed.
    None if there isn't enough data. (Simple close-to-close — Yang–Zhang below is
    more accurate when OHLC is available.)
    """
    if len(closes) < window + 1:
        return None
    rets = log_returns(closes[-(window + 1):])
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(trading_days)


def yang_zhang_vol(bars: Sequence[dict], window: int, trading_days: int = 252) -> float | None:
    """Annualized Yang–Zhang realized volatility from OHLC bars.

    Yang–Zhang combines overnight (close→open), open→close, and the drift-free
    Rogers–Satchell range estimator. It is the most efficient of the common
    realized-vol estimators and is drift- and gap-robust — materially better than
    close-to-close for option rich/cheap decisions. (Ref: Euan Sinclair,
    *Volatility Trading*; impl pattern per jasonstrimpel/volatility-trading.)

    `bars` is an ordered list of dicts with open/high/low/close. Needs window+1
    bars (the extra one supplies the first overnight gap). Returns a fraction.
    """
    if len(bars) < window + 1:
        return None
    w = bars[-(window + 1):]
    n = window
    o_terms, c_terms, rs_terms = [], [], []
    for i in range(1, len(w)):
        prev_c = w[i - 1]["close"]
        o, h, l, c = w[i]["open"], w[i]["high"], w[i]["low"], w[i]["close"]
        if min(prev_c, o, h, l, c) <= 0:
            return None
        o_terms.append(math.log(o / prev_c))      # overnight
        c_terms.append(math.log(c / o))            # open→close
        rs_terms.append(math.log(h / c) * math.log(h / o) + math.log(l / c) * math.log(l / o))
    if len(o_terms) < 2:
        return None
    om = sum(o_terms) / n
    cm = sum(c_terms) / n
    var_o = sum((x - om) ** 2 for x in o_terms) / (n - 1)
    var_c = sum((x - cm) ** 2 for x in c_terms) / (n - 1)
    var_rs = sum(rs_terms) / n
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var_yz = var_o + k * var_c + (1 - k) * var_rs
    if var_yz <= 0:
        return None
    return math.sqrt(var_yz) * math.sqrt(trading_days)


def percentile_rank(value: float, series: Sequence[float]) -> float | None:
    """Percentile (0–100) of `value` within `series`: % of observations ≤ value."""
    if not series:
        return None
    count = sum(1 for x in series if x <= value)
    return 100.0 * count / len(series)


def nearest(strikes: Sequence[float], target: float) -> float:
    return min(strikes, key=lambda s: abs(s - target))


def put_skew(atm_iv: float | None, otm_iv: float | None) -> float | None:
    """Skew in VOL POINTS: (OTM-put IV − ATM IV) × 100.

    Positive means the OTM put is more expensive (in IV terms) than ATM — the
    normal state for index puts because of standing institutional crash-hedging.
    A large value means crash insurance is already richly priced, which WEAKENS
    the tail-risk edge (idea.md lines 273–278).
    """
    if atm_iv is None or otm_iv is None:
        return None
    return (otm_iv - atm_iv) * 100.0


def iv_rv_ratio(atm_iv: float | None, rv: float | None) -> float | None:
    """ATM implied vol ÷ realized vol. >1 ⇒ options rich vs how the index moves."""
    if atm_iv is None or rv is None or rv <= 0:
        return None
    return atm_iv / rv


def vol_risk_premium(atm_iv: float | None, rv: float | None) -> float | None:
    """Volatility Risk Premium in VOL POINTS: (ATM IV − realized vol) × 100.

    Positive = options price in MORE movement than actually happens (the usual
    state; buyers pay a premium). Low or negative = options are genuinely cheap
    vs realized moves — the real "cheap" condition idea.md wanted, as opposed to
    just "VIX is low".
    """
    if atm_iv is None or rv is None:
        return None
    return (atm_iv - rv) * 100.0


def trade_stats(pnls: Sequence[float]) -> dict:
    """Summary stats over a list of realized P&L numbers. Pure; used by backtest.

    For a tail-risk strategy, judge by `expectancy` and `payoff_ratio`, not
    `hit_rate_pct` — a low hit rate with a high payoff ratio is the expected,
    healthy shape.
    """
    pnls = list(pnls)
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "total": n,
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate_pct": round(100.0 * len(wins) / n, 1) if n else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(sum(pnls) / n, 2) if n else 0.0,
        "payoff_ratio": round(avg_win / abs(avg_loss), 2) if wins and losses else None,
        "best": round(max(pnls), 2) if pnls else 0.0,
        "worst": round(min(pnls), 2) if pnls else 0.0,
    }
