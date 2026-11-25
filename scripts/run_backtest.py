"""Run the backtest over the archived EOD dataset and write docs/data/backtest.json.

    python -m scripts.run_backtest
"""

from __future__ import annotations

import json
import os

import yaml

from scanner.backtest import run_backtest

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    result = run_backtest(cfg)
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/backtest.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    if not result["ok"]:
        print(result["reason"])
        return 0
    print(f"days={result['days_tested']} trades={result['total_trades']} "
          f"hit={result['hit_rate_pct']}% net=₹{result['net_pnl']} "
          f"maxDD=₹{result['max_drawdown']}")
    print(result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
