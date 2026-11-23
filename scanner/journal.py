"""Paper-trade journal with a hard loss-budget lock.

Discipline is the point (idea.md lines 288–302): once realized losses hit the
budget, the journal LOCKS and refuses new opens until you consciously reset it.
Every trade carries its exit rule from the moment it opens.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

_DEFAULT_PATH = "data/journal.json"


def _blank() -> dict:
    return {"trades": [], "locked": False, "realized_pnl": 0.0, "updated": None}


def load(path: str = _DEFAULT_PATH) -> dict:
    if not os.path.exists(path):
        return _blank()
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return _blank()


def save(journal: dict, path: str = _DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    journal["updated"] = datetime.utcnow().isoformat() + "Z"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(journal, f, indent=2, default=str)


def _next_id(journal: dict) -> int:
    return 1 + max((t["id"] for t in journal["trades"]), default=0)


def open_trade(journal: dict, *, strike: float, expiry: str, entry_premium: float,
               lots: int, lot_size: int, cfg: dict, on: date) -> dict:
    """Open a paper position. Refuses if the journal is locked."""
    if journal.get("locked"):
        return {"ok": False, "reason": "Journal is LOCKED — loss budget spent. Reset to continue."}
    trade = {
        "id": _next_id(journal),
        "status": "OPEN",
        "open_date": on.isoformat(),
        "strike": strike,
        "expiry": expiry,
        "entry_premium": entry_premium,
        "lots": lots,
        "lot_size": lot_size,
        "cost": round(entry_premium * lots * lot_size, 2),
        "exit_date": None,
        "exit_premium": None,
        "pnl": None,
        "exit_reason": None,
        "exit_plan": {
            "take_profit_mult": cfg["exit_rules"]["take_profit_mult"],
            "vix_spike_percentile": cfg["exit_rules"]["vix_spike_percentile"],
            "time_stop_dte": cfg["exit_rules"]["time_stop_dte"],
        },
    }
    journal["trades"].append(trade)
    return {"ok": True, "trade": trade}


def close_trade(journal: dict, trade_id: int, *, exit_premium: float, reason: str,
                cfg: dict, on: date) -> dict:
    for t in journal["trades"]:
        if t["id"] == trade_id and t["status"] == "OPEN":
            gross = (exit_premium - t["entry_premium"]) * t["lots"] * t["lot_size"]
            c = cfg["costs"]
            stt = exit_premium * t["lots"] * t["lot_size"] * c["stt_sell_pct"] / 100.0
            brokerage = 2 * c["brokerage_per_order"]
            slippage = exit_premium * t["lots"] * t["lot_size"] * c["slippage_pct"] / 100.0
            net = gross - stt - brokerage - slippage
            t.update(status="CLOSED", exit_date=on.isoformat(),
                     exit_premium=exit_premium, exit_reason=reason, pnl=round(net, 2))
            journal["realized_pnl"] = round(journal.get("realized_pnl", 0.0) + net, 2)
            if journal["realized_pnl"] <= -abs(cfg["paper"]["loss_budget"]):
                journal["locked"] = True
            return {"ok": True, "trade": t, "pnl": round(net, 2)}
    return {"ok": False, "reason": f"No OPEN trade with id {trade_id}"}


def open_trades(journal: dict) -> list[dict]:
    return [t for t in journal["trades"] if t["status"] == "OPEN"]


def budget_status(journal: dict, cfg: dict) -> dict:
    budget = abs(cfg["paper"]["loss_budget"])
    realized = journal.get("realized_pnl", 0.0)
    used = max(0.0, -realized)
    return {"budget": budget, "realized_pnl": realized, "loss_used": round(used, 2),
            "remaining": round(budget - used, 2), "locked": journal.get("locked", False)}
