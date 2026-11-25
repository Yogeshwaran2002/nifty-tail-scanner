"""Entrypoint for the daily scan. Run by the GitHub Action (or locally).

    python -m scripts.run_scan            # scan for today
    python -m scripts.run_scan 2026-11-24 # scan for a specific trading date

Reads config.yaml, runs the deterministic scan, writes the dashboard JSON, and
pushes a Telegram alert (or prints it if Telegram isn't configured).
"""

from __future__ import annotations

import sys
from datetime import date

import yaml

from scanner import alert
from scanner.scan import run_scan
from scanner.strategies import run_strategies

# Windows consoles default to cp1252 and choke on the emoji in alert text.
# Telegram/HTTP is always UTF-8, so this only affects local stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    on = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    signal = run_scan(cfg, on)

    # Additional strategy modules (credit spread, calendar, ETF momentum)
    try:
        strat = run_strategies(cfg, on)
        cs = (strat.get("credit_spread") or {}).get("fired")
        mom = (strat.get("etf_momentum") or {}).get("hold")
        print(f"strategies: credit_spread fired={cs}  etf_top={mom}")
    except Exception as e:
        print(f"strategies error: {e}")

    msg = alert.format_signal(signal)
    alert.send(msg)

    print(f"\n=== scan {signal['date']} ===")
    print(f"fired={signal['fired']}  spot={signal.get('spot')}  "
          f"vix={signal.get('vix')}  pctile={signal.get('vix_percentile')}")
    if signal.get("errors"):
        print("errors:", signal["errors"])
    if signal.get("warnings"):
        print("warnings:", signal["warnings"])
    if signal["fired"]:
        print(f"candidates: {len(signal['candidates'])}")
    else:
        for r in signal.get("reasons", []):
            print("  -", r)
    # Non-zero exit only on hard data failure, so the Action surfaces it.
    return 1 if signal.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
