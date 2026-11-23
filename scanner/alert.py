"""Telegram push alerts.

Push-only: the daily GitHub Action POSTs to the Bot API's sendMessage — no
server, no polling, fully free. Interactive commands (/today, /why, /journal)
need a running host and come in a later phase.

Credentials come from env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), set as GitHub
Secrets. If unset, messages print to stdout so the scan still works end-to-end.
"""

from __future__ import annotations

import os
import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] not configured — message below:\n" + text)
        return False
    try:
        r = requests.post(
            API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[telegram] send failed HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"[telegram] send error: {e}")
        return False


def _fmt(x, nd=2, suffix=""):
    return "n/a" if x is None else f"{x:.{nd}f}{suffix}"


def format_signal(signal: dict) -> str:
    """Build the rich alert body from a scan signal dict."""
    d = signal["date"]
    vix = signal.get("vix")
    vixp = signal.get("vix_percentile")
    lines = [f"<b>NIFTY Tail-Risk Scan — {d}</b>",
             f"Spot {_fmt(signal.get('spot'), 0)}  |  VIX {_fmt(vix)} "
             f"(pctile {_fmt(vixp, 0)})  |  RV {_fmt((signal.get('realized_vol') or 0)*100,1,'%')}"]

    cands = signal.get("candidates", [])
    if signal.get("fired") and cands:
        lines.append(f"\n🟢 <b>{len(cands)} candidate(s) passed all gates</b>")
        for c in cands[:3]:
            lines.append(
                f"\n• {c['strike']:.0f} PE exp {c['expiry']} ({c['dte']}d)"
                f"\n   premium ₹{_fmt(c['premium'])}  cost/lot ₹{_fmt(c['cost_per_lot'],0)}"
                f"\n   IV {_fmt((c['iv'] or 0)*100,1,'%')}  skew {_fmt(c['skew'],1)}pts  "
                f"IV/RV {_fmt(c['iv_rv_ratio'])}"
                f"\n   breakeven {c['breakeven']:.0f} ({c['breakeven_move_pct']:.1f}% move)"
            )
        lines.append("\n⚠️ EOD data has NO live spread. Check the live bid-ask in Groww "
                     "before you click buy.")
    else:
        lines.append("\n⚪ <b>No trade today.</b> Failed gates:")
        for r in signal.get("reasons", [])[:6]:
            lines.append(f"   ✗ {r}")

    exits = signal.get("exit_signals", [])
    if exits:
        lines.append("\n🔴 <b>Exit signals on open paper trades:</b>")
        for e in exits:
            lines.append(f"   • #{e['id']} {e['strike']:.0f}PE → {e['action']}: {e['reason']}")

    b = signal.get("budget", {})
    if b:
        state = "LOCKED" if b.get("locked") else "open"
        lines.append(f"\nPaper budget: ₹{_fmt(b.get('remaining'),0)} left of "
                     f"₹{_fmt(b.get('budget'),0)} ({state})")

    lines.append("\n<i>Educational only. Not advice. Option buyers lose most (SEBI FY26).</i>")
    return "\n".join(lines)
