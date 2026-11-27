"""Black-Scholes pricing and implied-volatility inversion.

Pure standard-library (math only) so the GitHub Action needs no numpy/scipy.
Implied vol is recovered by bisection, which is slower than Newton but cannot
diverge — robust is worth more than fast for a once-a-day scan.
"""

from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float):
    if sigma <= 0 or T <= 0:
        raise ValueError("sigma and T must be positive")
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def bs_put_price(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """European put price (with continuous dividend yield q)."""
    if T <= 0:
        return max(K - S, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)


def bs_call_price(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0:
        return max(S - K, 0.0)
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)


def put_delta(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float | None:
    """Delta of a European put (negative, in [-1, 0]). Used to pick strikes by
    probability — a ~-0.20 delta put is roughly 80% likely to expire worthless."""
    if sigma is None or sigma <= 0 or T <= 0:
        return None
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    return -math.exp(-q * T) * norm_cdf(-d1)


def call_delta(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float | None:
    """Delta of a European call (positive, in [0, 1])."""
    if sigma is None or sigma <= 0 or T <= 0:
        return None
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    return math.exp(-q * T) * norm_cdf(d1)


def implied_vol_call(market_price: float, S: float, K: float, T: float, r: float, q: float,
                     lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-5, max_iter: int = 100) -> float | None:
    """Recover implied vol of a call via bisection. None on arbitrage-bound /
    unbracketable (illiquid) prints."""
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    if market_price < intrinsic - tol or market_price > S * math.exp(-q * T) + tol:
        return None
    p_lo = bs_call_price(S, K, T, r, q, lo) - market_price
    p_hi = bs_call_price(S, K, T, r, q, hi) - market_price
    if p_lo * p_hi > 0:
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = bs_call_price(S, K, T, r, q, mid) - market_price
        if abs(p_mid) < tol:
            return mid
        if p_lo * p_mid < 0:
            hi = mid
        else:
            lo, p_lo = mid, p_mid
    return 0.5 * (lo + hi)


def implied_vol_put(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-5,
    max_iter: int = 100,
) -> float | None:
    """Recover implied vol of a put from its market price via bisection.

    Returns None when the price is below intrinsic / arbitrage bounds or the
    root cannot be bracketed (e.g. illiquid prints) — callers must handle None
    rather than trust a bogus number.
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    intrinsic = max(K * math.exp(-r * T) - S * math.exp(-q * T), 0.0)
    upper_bound = K * math.exp(-r * T)
    if market_price < intrinsic - tol or market_price > upper_bound + tol:
        return None

    p_lo = bs_put_price(S, K, T, r, q, lo) - market_price
    p_hi = bs_put_price(S, K, T, r, q, hi) - market_price
    if p_lo * p_hi > 0:
        return None  # not bracketed within [lo, hi]

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = bs_put_price(S, K, T, r, q, mid) - market_price
        if abs(p_mid) < tol:
            return mid
        if p_lo * p_mid < 0:
            hi = mid
        else:
            lo, p_lo = mid, p_mid
    return 0.5 * (lo + hi)
