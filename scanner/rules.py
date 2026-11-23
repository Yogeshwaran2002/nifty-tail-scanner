"""Deterministic entry gates and exit rules.

This is the heart of the "no LLM in the signal path" promise. Every function
here is a pure decision over numbers and config thresholds. Each gate reports
its name, the observed value, the threshold, and pass/fail so the dashboard and
the Telegram alert can show exactly WHY a signal did or didn't fire.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Gate:
    name: str
    passed: bool
    value: Optional[float]
    threshold: float
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_entry_gates(m: dict, cfg: dict) -> list[Gate]:
    """Evaluate all entry gates for one candidate strike.

    `m` is the per-strike metrics dict built in scan.py. A gate whose input is
    unavailable (None) FAILS closed — we never fire on missing data.
    """
    g = cfg["entry_gates"]
    gates: list[Gate] = []

    vix_pct = m.get("vix_percentile")
    gates.append(Gate(
        "vix_percentile", vix_pct is not None and vix_pct <= g["vix_percentile_max"],
        vix_pct, g["vix_percentile_max"],
        "VIX must sit in the calmer part of its 1-year range",
    ))

    skew = m.get("skew")
    gates.append(Gate(
        "skew", skew is not None and skew <= g["skew_max"],
        skew, g["skew_max"],
        "Reject puts whose crash insurance is already richly priced vs ATM",
    ))

    ivrv = m.get("iv_rv_ratio")
    gates.append(Gate(
        "iv_rv_ratio", ivrv is not None and ivrv <= g["iv_rv_ratio_max"],
        ivrv, g["iv_rv_ratio_max"],
        "Reject options rich in implied vs realized volatility",
    ))

    oi = m.get("open_interest")
    gates.append(Gate(
        "open_interest", oi is not None and oi >= g["min_open_interest"],
        oi, g["min_open_interest"],
        "Liquidity floor — avoid untradeable strikes",
    ))

    vol = m.get("volume")
    gates.append(Gate(
        "volume", vol is not None and vol >= g["min_volume"],
        vol, g["min_volume"],
        "Traded-volume floor",
    ))

    cost = m.get("cost_per_lot")
    gates.append(Gate(
        "cost_per_lot", cost is not None and cost <= g["max_cost_per_lot"],
        cost, g["max_cost_per_lot"],
        "Keep each bullet small — ₹ cap per lot",
    ))

    return gates


def passes_all(gates: list[Gate]) -> bool:
    return all(g.passed for g in gates)


def cheapness_score(m: dict) -> float:
    """Lower = a better tail-risk candidate.

    Prefer low IV/RV (not rich), low skew (insurance not over-priced), and low
    absolute cost. Deterministic ranking used only to order already-qualified
    candidates; it never overrides a failed gate.
    """
    ivrv = m.get("iv_rv_ratio") or 99.0
    skew = m.get("skew")
    skew = 99.0 if skew is None else skew
    cost = m.get("cost_per_lot") or 1e9
    return ivrv * 2.0 + (skew / 10.0) + (cost / 10000.0)


# ── Exit rules ──────────────────────────────────────────────────────────────

def evaluate_exit(position: dict, current_premium: float, vix_percentile: Optional[float],
                  dte: int, cfg: dict) -> Optional[dict]:
    """Return an exit instruction dict if any exit rule triggers, else None.

    Rules are checked in priority order; the first to fire wins.
    """
    ex = cfg["exit_rules"]
    entry = position["entry_premium"]

    if dte <= ex["force_close_dte"]:
        return {"action": "FORCE_CLOSE", "reason": "Never hold into expiry (STT on exercise)"}

    if current_premium >= entry * ex["take_profit_mult"]:
        mult = current_premium / entry if entry else 0
        return {"action": "TAKE_PROFIT",
                "reason": f"Premium reached {mult:.1f}x entry (target {ex['take_profit_mult']}x)"}

    if vix_percentile is not None and vix_percentile >= ex["vix_spike_percentile"]:
        return {"action": "VIX_SPIKE_EXIT",
                "reason": f"VIX percentile {vix_percentile:.0f} ≥ {ex['vix_spike_percentile']:.0f} — sell into the panic"}

    if dte <= ex["time_stop_dte"]:
        return {"action": "TIME_STOP",
                "reason": f"{dte} days to expiry ≤ {ex['time_stop_dte']} — theta accelerates"}

    return None
