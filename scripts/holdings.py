"""Log real-money ETF buys/sells — edits data/etf_holdings.json.

  python -m scripts.holdings list
  python -m scripts.holdings buy  PSUBNKBEES 2 108.50          # add 2 units @ 108.50 (today)
  python -m scripts.holdings buy  PSUBNKBEES 2 108.50 2026-11-25
  python -m scripts.holdings sell PHARMABEES 7 30.20           # sell 7 units @ 30.20 (books realized P&L)
  python -m scripts.holdings set  PHARMABEES 7 29.86 2026-11-25  # overwrite a position (fix exact fills)
  python -m scripts.holdings del  BANKBEES                      # remove a position entirely

After editing, commit + push so the dashboard/bot pick it up:
  git add data/etf_holdings.json && git commit -m "holdings update" && git push
(The daily scan also commits it automatically on its next run.)

The bot is read-only and can't write holdings — that's why this is a local CLI.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PATH = "data/etf_holdings.json"


def _load() -> dict:
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {"currency": "INR", "note": "REAL money.", "realized_pnl": 0.0, "holdings": []}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def _find(d, sym):
    for h in d["holdings"]:
        if h["symbol"].upper() == sym.upper():
            return h
    return None


def cmd_list(d):
    if not d["holdings"]:
        print("No holdings.")
    for h in d["holdings"]:
        print(f"  {h['symbol']}: {h['units']} units @ {h['buy_price']} (since {h.get('buy_date','?')})"
              f"  cost ₹{round(h['units']*h['buy_price'],2)}")
    print(f"Realized P&L so far: ₹{round(d.get('realized_pnl', 0.0), 2)}")


def cmd_buy(d, sym, units, price, dt):
    units, price = float(units), float(price)
    h = _find(d, sym)
    if h:  # weighted-average the cost basis
        tot = h["units"] + units
        h["buy_price"] = round((h["units"] * h["buy_price"] + units * price) / tot, 4)
        h["units"] = tot
    else:
        d["holdings"].append({"symbol": sym.upper(), "units": units, "buy_price": price,
                              "buy_date": dt or date.today().isoformat()})
    print(f"Bought {units} {sym.upper()} @ {price}.")


def cmd_sell(d, sym, units, price):
    units, price = float(units), float(price)
    h = _find(d, sym)
    if not h:
        print(f"No holding {sym}."); return
    sell_units = min(units, h["units"])
    realized = sell_units * (price - h["buy_price"])
    d["realized_pnl"] = round(d.get("realized_pnl", 0.0) + realized, 2)
    h["units"] = round(h["units"] - sell_units, 6)
    if h["units"] <= 0:
        d["holdings"].remove(h)
    print(f"Sold {sell_units} {sym.upper()} @ {price}. Realized P&L ₹{round(realized,2)} "
          f"(cumulative ₹{d['realized_pnl']}).")


def cmd_set(d, sym, units, price, dt):
    h = _find(d, sym)
    if h:
        d["holdings"].remove(h)
    d["holdings"].append({"symbol": sym.upper(), "units": float(units), "buy_price": float(price),
                          "buy_date": dt or date.today().isoformat()})
    print(f"Set {sym.upper()} = {units} units @ {price}.")


def cmd_del(d, sym):
    h = _find(d, sym)
    if h:
        d["holdings"].remove(h)
        print(f"Removed {sym.upper()}.")
    else:
        print(f"No holding {sym}.")


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__); return 2
    d = _load()
    op = a[0].lower()
    try:
        if op == "list":
            cmd_list(d); return 0
        elif op == "buy":
            cmd_buy(d, a[1], a[2], a[3], a[4] if len(a) > 4 else None)
        elif op == "sell":
            cmd_sell(d, a[1], a[2], a[3])
        elif op == "set":
            cmd_set(d, a[1], a[2], a[3], a[4] if len(a) > 4 else None)
        elif op == "del":
            cmd_del(d, a[1])
        else:
            print(__doc__); return 2
    except IndexError:
        print("Missing arguments.\n" + __doc__); return 2
    _save(d)
    cmd_list(d)
    print("\nSaved. Remember: git add data/etf_holdings.json && git commit -m 'holdings' && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
