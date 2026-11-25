"""Best-effort seeding of the backtest dataset from NSE archive bhavcopies.

    python -m scripts.fetch_history 2026-09-01 2026-11-20

Writes one data/eod/<date>.json per trading day it can fetch. Skips weekends,
holidays, and any day NSE doesn't serve. NSE throttles/blocks aggressively, so
run this locally (home IP), slowly, and expect gaps. The daily scan fills in
going forward regardless.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import yaml

from scanner import nse_data as nse
from scanner.scan import _archive, _pick_monthly_expiry


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
    session = nse.nse_session()

    # Pull VIX history once (Yahoo) and attach each day's close to its archive,
    # so the backtest's VIX-percentile gate can actually evaluate on seeded days.
    vixmap = {}
    try:
        for d, v in nse.get_vix_history(session, 400):
            vixmap[d] = v
        print(f"VIX history: {len(vixmap)} days loaded from Yahoo.")
    except nse.DataUnavailable as e:
        print(f"VIX history unavailable ({e}) — seeding with vix=None.")

    def vix_for(day):
        # exact date, else the most recent VIX close on/before that day
        if day in vixmap:
            return vixmap[day]
        prior = [d for d in vixmap if d <= day]
        return vixmap[max(prior)] if prior else None

    ok = skipped = 0
    day = start
    while day <= end:
        if day.weekday() >= 5:  # Sat/Sun
            day += timedelta(days=1)
            continue
        try:
            rows = nse.download_fo_bhavcopy(session, day)
            chain = nse.extract_nifty_puts(rows, cfg["market"]["underlying"])
            expiry = _pick_monthly_expiry(chain["puts"], day, cfg)
            if chain["spot"] and expiry:
                exp_puts = [p for p in chain["puts"] if p["expiry"] == expiry]
                _archive(day, rows_count=len(exp_puts), chain=exp_puts,
                         spot=chain["spot"], vix=vix_for(day), expiry=expiry)
                ok += 1
                print(f"  {day}: archived {len(exp_puts)} puts (exp {expiry}, vix {vix_for(day)})")
            else:
                skipped += 1
                print(f"  {day}: no usable chain, skipped")
        except nse.DataUnavailable as e:
            skipped += 1
            print(f"  {day}: {e}")
        time.sleep(1.5)  # be gentle with NSE
        day += timedelta(days=1)

    print(f"\nDone. archived={ok} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
