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


def format_playbook(sig: dict, st: dict | None) -> str:
    """Full daily playbook. Leads with an ACTION banner when any strategy is live,
    and spells out buy/sell, capital, and risk (max loss) on each ACT line."""
    st = st or {}
    cs, ic, cal = st.get("credit_spread") or {}, st.get("iron_condor") or {}, st.get("calendar") or {}
    tom = st.get("turn_of_month") or {}
    bn = st.get("banknifty") or {}
    bcs, bic = bn.get("credit_spread") or {}, bn.get("iron_condor") or {}
    acts = sum(1 for f in [sig.get("fired"), cs.get("fired"), ic.get("fired"), cal.get("fired"),
                           tom.get("fired"), bcs.get("fired"), bic.get("fired")] if f)

    L = []
    if acts:
        L.append(f"🔔 <b>ACTION — {acts} setup(s) live</b> · {sig.get('date')}")
    else:
        L.append(f"<b>Playbook — all waiting</b> · {sig.get('date')}")
    L.append(f"Spot {_fmt(sig.get('spot'), 0)} | VIX {_fmt(sig.get('vix'))} "
             f"(pctile {_fmt(sig.get('vix_percentile'), 0)}) | RV {_fmt((sig.get('realized_vol') or 0) * 100, 1)}%")
    L.append("")

    # Tail-risk (buy)
    if sig.get("fired") and sig.get("candidates"):
        c = sig["candidates"][0]
        L.append(f"🟢 <b>Tail-risk — BUY</b> {c['strike']:.0f} PE {c['expiry']}\n"
                 f"   ≈ ₹{_fmt(c['premium'])}/unit · capital ₹{_fmt(c.get('cost_per_lot'), 0)}/lot · "
                 f"risk = premium paid (max loss ₹{_fmt(c.get('cost_per_lot'), 0)})\n"
                 f"   exit: 3× / VIX spike / ~2wk before expiry")
    else:
        L.append("⚪ Tail-risk: wait")

    def sell_line(name, b):
        if not b:
            return f"⚪ {name}: n/a"
        if not b.get("fired"):
            return f"⚪ {name}: wait"
        if b.get("short_put") and b.get("short_call"):   # iron condor
            legs = f"shorts {b['short_put']:.0f}P / {b['short_call']:.0f}C"
        else:                                             # credit spread
            legs = f"SELL {b['short_strike']:.0f}/buy {b['long_strike']:.0f} PE"
        return (f"🟢 <b>{name} — SELL</b> {legs}\n"
                f"   credit ₹{_fmt(b.get('max_profit'), 0)} · <b>risk (max loss) ₹{_fmt(b.get('max_loss'), 0)}</b> · "
                f"PoP {_fmt(b.get('prob_profit_pct'), 0)}%\n"
                f"   exit: buy back ~50% of credit; never into expiry")

    L.append(sell_line("Credit spread", cs))
    L.append(sell_line("Iron condor", ic))
    L.append(f"⚪ Calendar: {'wait' if not cal.get('fired') else 'see dashboard'}")

    # ETF momentum (always a standing hold)
    e = st.get("etf_momentum") or {}
    if e.get("trade_plan"):
        holds = ", ".join(f"{p['symbol']} {_fmt(p.get('weight_pct'), 0)}%" for p in e["trade_plan"])
        L.append(f"🟢 <b>ETF momentum — HOLD</b> {holds}\n"
                 f"   risk control: −{e.get('trailing_stop_pct', 12)}% trailing stop · rebalance monthly")

    # Turn-of-the-Month
    if tom.get("backtest"):
        if tom.get("fired"):
            L.append(f"🟢 <b>Turn-of-Month — BUY</b> {tom.get('units')} {tom.get('etf')} ≈ ₹{_fmt(tom.get('cost'), 0)}\n"
                     f"   capital ₹{_fmt(tom.get('budget_inr'), 0)} · sell after first ~3 trading days of next month")
        else:
            L.append(f"⚪ Turn-of-Month: cash (next window {tom.get('next_window_start')})")

    # BankNIFTY
    if bic or bcs:
        if bic.get("fired"):
            L.append(f"🟢 <b>BankNIFTY condor — SELL</b> shorts {bic['short_put']:.0f}P/{bic['short_call']:.0f}C\n"
                     f"   credit ₹{_fmt(bic.get('max_profit'), 0)} · <b>risk ₹{_fmt(bic.get('max_loss'), 0)}</b> · PoP {_fmt(bic.get('prob_profit_pct'), 0)}%")
        elif bcs.get("fired"):
            L.append(f"🟢 <b>BankNIFTY credit — SELL</b> {bcs['short_strike']:.0f}/buy {bcs['long_strike']:.0f} PE\n"
                     f"   credit ₹{_fmt(bcs.get('max_profit'), 0)} · <b>risk ₹{_fmt(bcs.get('max_loss'), 0)}</b>")
        else:
            L.append("⚪ BankNIFTY: wait")

    h = e.get("holdings") or {}
    if h.get("rows"):
        L.append(f"\n<b>Your ETF holdings</b>: invested ₹{_fmt(h.get('total_cost'), 0)} → "
                 f"₹{_fmt(h.get('total_value'), 0)} ({_fmt(h.get('total_pnl'), 0)} / {_fmt(h.get('total_pnl_pct'), 1)}%)")

    L.append("\n⚠️ EOD data, no live spread — check Groww before any order.")
    L.append("<i>Educational only. Not advice. Risk = defined max loss shown.</i>")
    return "\n".join(L)


def strategy_signature(sig: dict, st: dict | None) -> str:
    """A compact signature of which strategies are ACT today — the push fires
    when this changes (new setup goes live), plus once per new date."""
    st = st or {}
    fired = []
    if sig.get("fired"):
        fired.append("tail")
    for k in ("credit_spread", "iron_condor", "calendar", "turn_of_month"):
        if (st.get(k) or {}).get("fired"):
            fired.append(k)
    bn = st.get("banknifty") or {}
    if (bn.get("credit_spread") or {}).get("fired"):
        fired.append("bn_credit")
    if (bn.get("iron_condor") or {}).get("fired"):
        fired.append("bn_condor")
    return f"{sig.get('date')}|{','.join(sorted(fired))}"


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
