/**
 * NIFTY Tail-Risk Scanner — Phase 2 interactive Telegram bot.
 *
 * Runs free on Cloudflare Workers (webhook, no polling, no server). Reads the
 * scanner's published JSON from GitHub Pages — it holds no state and never
 * computes a signal, matching idea.md's "conversational layer is read-only"
 * rule. Free-text questions are answered by a free OPEN-SOURCE model via
 * Cloudflare Workers AI (Llama) — no external API key, no Anthropic.
 *
 * Commands: /today /why /journal /backtest /skew /help
 * Anything else → the LLM explainer (if the AI binding is present).
 *
 * Secrets (wrangler secret put): TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, ALLOWED_CHAT_ID
 * Vars (wrangler.toml): DATA_BASE_URL, LLM_MODEL
 */

const DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // GET /setup?key=<WEBHOOK_SECRET> — register the Telegram webhook FROM
    // Cloudflare's network (bypasses any office-IP throttle on api.telegram.org).
    // GET /delete?key=... removes it. Any other GET is a health check.
    if (request.method === "GET") {
      if (url.pathname === "/setup" || url.pathname === "/delete") {
        if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
          return new Response("forbidden (append ?key=<your WEBHOOK_SECRET>)", { status: 403 });
        }
        const base = `${url.protocol}//${url.host}`;
        const method = url.pathname === "/setup" ? "setWebhook" : "deleteWebhook";
        const body = url.pathname === "/setup"
          ? { url: base, secret_token: env.WEBHOOK_SECRET, drop_pending_updates: true }
          : { drop_pending_updates: true };
        const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
          method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
        });
        return new Response(await r.text(), { status: 200, headers: { "content-type": "application/json" } });
      }
      // GET /check?key=... — diagnostic: who is the token? (getMe from Cloudflare)
      // ok:true + username = fresh/valid token. 401 = revoked. 429 = token is
      // globally rate-limited (still the abused/old token).
      if (url.pathname === "/check") {
        if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
          return new Response("forbidden (append ?key=<your WEBHOOK_SECRET>)", { status: 403 });
        }
        const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getMe`);
        return new Response(await r.text(), { status: 200, headers: { "content-type": "application/json" } });
      }
      // GET /try?key=...&cmd=/today — run a bot command in the browser (no
      // Telegram needed). Handy for testing while a bot is unavailable.
      if (url.pathname === "/try") {
        if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
          return new Response("forbidden (append ?key=<your WEBHOOK_SECRET>)", { status: 403 });
        }
        const cmd = url.searchParams.get("cmd") || "/today";
        let out;
        try { out = await handle(env, cmd, "web"); }
        catch (e) { out = "error: " + (e && e.message ? e.message : e); }
        const html = `<!doctype html><meta charset="utf-8"><title>bot /try</title>` +
          `<body style="background:#0f1419;color:#e6edf3;font:14px/1.6 ui-monospace,Consolas,monospace;padding:24px">` +
          `<div style="color:#6e7d8a">cmd: ${cmd} — try ?cmd=/today /why /skew /journal /backtest or any question</div>` +
          `<pre style="white-space:pre-wrap;margin-top:12px">${out}</pre></body>`;
        return new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
      }
      // GET /scan?key=... — trigger the GitHub Actions daily-scan via
      // workflow_dispatch (powers the "Run scan now" button on the dashboard).
      // Needs a GH_DISPATCH_TOKEN secret (fine-grained PAT, Actions: read+write).
      if (url.pathname === "/scan") {
        const cors = { "access-control-allow-origin": "*", "content-type": "application/json" };
        if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
          return new Response(JSON.stringify({ ok: false, error: "bad key" }), { status: 403, headers: cors });
        }
        if (!env.GH_DISPATCH_TOKEN) {
          return new Response(JSON.stringify({ ok: false, error: "GH_DISPATCH_TOKEN not set on the Worker" }), { status: 200, headers: cors });
        }
        const repo = env.GH_REPO || "Yogeshwaran2002/nifty-tail-scanner";
        const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/daily-scan.yml/dispatches`, {
          method: "POST",
          headers: {
            "authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "user-agent": "nifty-tail-bot",
            "content-type": "application/json",
          },
          body: JSON.stringify({ ref: "main" }),
        });
        const ok = r.status === 204;
        return new Response(JSON.stringify({ ok, status: r.status, detail: ok ? "scan triggered" : await r.text() }),
          { status: 200, headers: cors });
      }
      // GET /log?key=...&action=buy|sell&symbol=..&units=..&price=.. — log a
      // real ETF trade by dispatching the log-trade GitHub Action (reuses the
      // dispatch token; no file-write permission needed). Powers the dashboard form.
      if (url.pathname === "/log") {
        const cors = { "access-control-allow-origin": "*", "content-type": "application/json" };
        if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
          return new Response(JSON.stringify({ ok: false, error: "bad key" }), { status: 403, headers: cors });
        }
        if (!env.GH_DISPATCH_TOKEN) {
          return new Response(JSON.stringify({ ok: false, error: "GH_DISPATCH_TOKEN not set" }), { status: 200, headers: cors });
        }
        const inputs = {
          action: (url.searchParams.get("action") || "buy").toLowerCase(),
          symbol: (url.searchParams.get("symbol") || "").toUpperCase().trim(),
          units: url.searchParams.get("units") || "",
          price: url.searchParams.get("price") || "",
        };
        if (!["buy", "sell"].includes(inputs.action) || !inputs.symbol || !inputs.units || !inputs.price) {
          return new Response(JSON.stringify({ ok: false, error: "need action(buy/sell), symbol, units, price" }), { status: 200, headers: cors });
        }
        const repo = env.GH_REPO || "Yogeshwaran2002/nifty-tail-scanner";
        const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/log-trade.yml/dispatches`, {
          method: "POST",
          headers: { "authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`, "accept": "application/vnd.github+json",
                     "x-github-api-version": "2022-11-28", "user-agent": "nifty-tail-bot", "content-type": "application/json" },
          body: JSON.stringify({ ref: "main", inputs }),
        });
        const ok = r.status === 204;
        return new Response(JSON.stringify({ ok, status: r.status, detail: ok ? "trade logged — dashboard updates in ~1 min" : await r.text() }),
          { status: 200, headers: cors });
      }
      return new Response("nifty-tail-bot up — /setup /check /try /scan /log (all need ?key=<WEBHOOK_SECRET>)", { status: 200 });
    }

    // CORS preflight for the dashboard button
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "content-type",
      } });
    }

    if (request.method !== "POST") return new Response("ok", { status: 200 });

    // Telegram sends this header when the webhook was registered with a secret.
    if (env.WEBHOOK_SECRET &&
        request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }

    const msg = update.message || update.edited_message;
    if (!msg || !msg.text) return new Response("ok");

    const chatId = String(msg.chat.id);
    // Private bot: only answer the owner, so nobody else can burn your AI quota.
    if (env.ALLOWED_CHAT_ID && chatId !== String(env.ALLOWED_CHAT_ID)) {
      await sendTelegram(env, chatId, "This is a private scanner bot.");
      return new Response("ok");
    }

    let reply;
    try {
      reply = await handle(env, msg.text.trim(), chatId);
    } catch (e) {
      reply = "Something went wrong reading the scan data: " + (e && e.message ? e.message : e);
    }
    await sendTelegram(env, chatId, reply);
    return new Response("ok");
  },
};

const dataBase = (env) =>
  env.DATA_BASE_URL || "https://yogeshwaran2002.github.io/nifty-tail-scanner/data";

async function getJSON(env, name) {
  const r = await fetch(`${dataBase(env)}/${name}?t=${Date.now()}`);
  if (!r.ok) throw new Error(`could not fetch ${name} (HTTP ${r.status})`);
  return r.json();
}

async function handle(env, text, chatId) {
  const cmd = text.split(/\s+/)[0].toLowerCase().replace(/@.*$/, "");
  switch (cmd) {
    case "/start":
    case "/help":
      return help();
    case "/id":
      return `Your chat id is <code>${chatId}</code>\n\nSet it as the bot's ALLOWED_CHAT_ID secret to keep the bot private:\n<code>wrangler secret put ALLOWED_CHAT_ID</code>`;
    case "/today": return fmtToday(await getJSON(env, "latest.json"));
    case "/playbook": return fmtPlaybook(await getJSON(env, "latest.json"), await getJSON(env, "strategies.json").catch(() => null));
    case "/why": return fmtWhy(await getJSON(env, "latest.json"));
    case "/journal": return fmtJournal(await getJSON(env, "latest.json"));
    case "/backtest": return fmtBacktest(await getJSON(env, "backtest.json").catch(() => null));
    case "/skew": return fmtSkew(await getJSON(env, "latest.json"));
    case "/etf": return fmtEtf(await getJSON(env, "strategies.json").catch(() => null));
    case "/holdings": return fmtHoldings(await getJSON(env, "strategies.json").catch(() => null));
    case "/credit": return fmtCredit(await getJSON(env, "strategies.json").catch(() => null));
    case "/condor": return fmtCondor((await getJSON(env, "strategies.json").catch(() => null) || {}).iron_condor, "Iron condor (NIFTY)");
    case "/banknifty": return fmtBankNifty(await getJSON(env, "strategies.json").catch(() => null));
    case "/calendar": return fmtCalendar(await getJSON(env, "strategies.json").catch(() => null));
    case "/tom": return fmtTom(await getJSON(env, "strategies.json").catch(() => null));
    default:
      return askLLM(env, text, await getJSON(env, "latest.json").catch(() => null));
  }
}

function help() {
  return [
    "<b>NIFTY Tail-Risk Scanner bot</b>",
    "",
    "/today — latest scan summary",
    "/playbook — all strategies: act/wait + today's action",
    "/why — which entry gates passed/failed",
    "/journal — paper trades + loss budget",
    "/backtest — backtest stats",
    "/skew — volatility smile + risk premium",
    "/etf — ETF sector momentum (buy-only, low capital)",
    "/holdings — your real-money ETF holdings + P&L",
    "/credit — put credit spread (sell premium, defined risk)",
    "/condor — iron condor (sell both sides, defined risk)",
    "/banknifty — BankNIFTY credit spread + condor",
    "/calendar — volatility term-structure spread",
    "/tom — turn-of-the-month seasonal (NIFTY-proven)",
    "/id — show your Telegram chat id",
    "",
    "Ask anything else in plain English and I'll explain the numbers.",
    "",
    "<i>Read-only. Educational, not advice. I never tell you to buy or sell.</i>",
  ].join("\n");
}

const n = (x, d = 2) => (x === null || x === undefined) ? "n/a" : Number(x).toFixed(d);
const inr = (x) => (x === null || x === undefined) ? "n/a" : "₹" + Math.round(Number(x)).toLocaleString("en-IN");

function fmtPlaybook(s, st) {
  const L = ["<b>Today's playbook</b> — " + (s ? s.date : "")];
  // Tail-risk
  if (s && s.fired && (s.candidates || []).length) {
    const c = s.candidates[0];
    L.push(`\n🟢 <b>Tail-risk</b>: BUY ${n(c.strike,0)} PE ${c.expiry} ≈₹${n(c.premium)} — exit 3× / VIX spike / time stop`);
  } else {
    L.push(`\n⚪ <b>Tail-risk</b>: wait (${(s && s.reasons || ["no setup"])[0]})`);
  }
  const cs = st && st.credit_spread;
  if (cs && cs.fired) L.push(`🟢 <b>Credit spread</b>: SELL ${n(cs.short_strike,0)}/buy ${n(cs.long_strike,0)} PE — credit ${inr(cs.max_profit)}, max loss ${inr(cs.max_loss)}`);
  else L.push(`⚪ <b>Credit spread</b>: wait (${(cs && cs.reasons || ["not rich"])[0]})`);
  const ic = st && st.iron_condor;
  if (ic && ic.fired) L.push(`🟢 <b>Iron condor</b>: shorts ${n(ic.short_put,0)}P/${n(ic.short_call,0)}C — credit ${inr(ic.max_profit)}, max loss ${inr(ic.max_loss)}`);
  else if (ic) L.push(`⚪ <b>Iron condor</b>: wait`);
  const cal = st && st.calendar;
  L.push(cal && cal.fired ? `🟢 <b>Calendar</b>: sell front ${n(cal.atm_strike,0)} PE, buy next month`
                          : `⚪ <b>Calendar</b>: wait (front not rich)`);
  const e = st && st.etf_momentum;
  if (e && (e.trade_plan || []).length) {
    const act = e.trade_plan.map(p => `${p.symbol}${p.weight_pct != null ? " " + n(p.weight_pct,0) + "%" : ""}`).join(" · ");
    L.push(`🟢 <b>ETF momentum</b>: HOLD ${act} — rebalance monthly, <200-SMA→cash, −${e.trailing_stop_pct}% trail`);
  }
  const t = st && st.turn_of_month;
  if (t && t.backtest) {
    L.push(t.fired ? `🟢 <b>Turn-of-Month</b>: HOLD ${t.etf} (in window) — exit after first days of next month`
                   : `⚪ <b>Turn-of-Month</b>: cash — next window ${t.next_window_start || "?"}`);
  }
  const bn = st && st.banknifty;
  if (bn) {
    const bic = bn.iron_condor || {}, bcs = bn.credit_spread || {};
    if (bic.fired) L.push(`🟢 <b>BankNIFTY condor</b>: shorts ${n(bic.short_put,0)}P/${n(bic.short_call,0)}C — max loss ${inr(bic.max_loss)}`);
    else if (bcs.fired) L.push(`🟢 <b>BankNIFTY credit</b>: SELL ${n(bcs.short_strike,0)}/buy ${n(bcs.long_strike,0)} PE — max loss ${inr(bcs.max_loss)}`);
    else L.push(`⚪ <b>BankNIFTY</b>: wait`);
  }
  L.push("\n<i>Not advice. Check live prices in Groww before any order.</i>");
  return L.join("\n");
}

function fmtToday(s) {
  const lines = [
    `<b>Scan ${s.date}</b>`,
    `Spot ${n(s.spot, 0)} | VIX ${n(s.vix)} (pctile ${n(s.vix_percentile, 0)}) | RV ${n((s.realized_vol || 0) * 100, 1)}%`,
    `ATM IV ${n((s.atm_iv || 0) * 100, 1)}% | VRP ${n(s.vrp, 1)} pts`,
  ];
  if (s.fired && s.candidates && s.candidates.length) {
    lines.push(`\n🟢 ${s.candidates.length} candidate(s) passed every gate:`);
    for (const c of s.candidates.slice(0, 3)) {
      lines.push(`• ${n(c.strike, 0)} PE ${c.expiry} (${c.dte}d) — ₹${n(c.premium)} premium, cost/lot ${inr(c.cost_per_lot)}, breakeven ${n(c.breakeven, 0)}`);
    }
    lines.push(`\n⚠️ Check the LIVE bid-ask in Groww before any order — EOD data has no spread.`);
  } else {
    lines.push(`\n⚪ No trade today. Try /why for the gate breakdown.`);
  }
  if (s.exit_signals && s.exit_signals.length) {
    lines.push(`\n🔴 Exit signals:`);
    for (const e of s.exit_signals) lines.push(`• #${e.id} ${n(e.strike, 0)}PE → ${e.action}: ${e.reason}`);
  }
  return lines.join("\n");
}

function fmtWhy(s) {
  const best = (s.candidates && s.candidates[0]) ||
    (s.all_evaluated && s.all_evaluated.slice().sort((a, b) => (a.cost_per_lot || 1e9) - (b.cost_per_lot || 1e9))[0]);
  if (!best || !best.gates) return "No evaluated strikes in this scan. " + (s.reasons || []).join("; ");
  const lines = [`<b>Gate breakdown — ${n(best.strike, 0)} PE, expiry ${best.expiry}</b>`];
  for (const g of best.gates) {
    const v = g.value === null || g.value === undefined ? "n/a" : Number(g.value).toFixed(2);
    lines.push(`${g.passed ? "✓" : "✗"} ${g.name}: ${v} (limit ${g.threshold}) — ${g.detail}`);
  }
  lines.push(`\n${s.fired ? "🟢 All gates passed." : "⚪ At least one gate shut → no trade."}`);
  return lines.join("\n");
}

function fmtJournal(s) {
  const b = s.budget || {};
  const lines = [
    `<b>Paper journal</b>`,
    `Budget: ${inr(b.loss_used)} used, ${inr(b.remaining)} left of ${inr(b.budget)}${b.locked ? " (LOCKED)" : ""}`,
    `Realized P&L: ${inr(b.realized_pnl)}`,
  ];
  const open = s.open_trades || [];
  if (!open.length) lines.push(`No open paper trades.`);
  else { lines.push(`\nOpen:`); for (const t of open) lines.push(`• #${t.id} ${n(t.strike, 0)}PE ${t.expiry} — entry ₹${n(t.entry_premium)}, ${t.lots}×${t.lot_size}`); }
  return lines.join("\n");
}

function fmtBacktest(bt) {
  if (!bt) return "No backtest data yet.";
  if (!bt.ok) return "Backtest: " + (bt.reason || "not enough history yet.");
  return [
    `<b>Backtest — ${bt.days_tested} days</b>`,
    `Trades ${bt.total_trades} | hit rate ${n(bt.hit_rate_pct, 1)}%`,
    `Net P&L ${inr(bt.net_pnl)} | expectancy ${inr(bt.expectancy)}/trade`,
    `Payoff ratio ${bt.payoff_ratio === null || bt.payoff_ratio === undefined ? "n/a" : n(bt.payoff_ratio) + "×"} | best ${inr(bt.best_trade)} | maxDD ${inr(bt.max_drawdown)}`,
    `\n<i>Tail-risk expects a LOW hit rate with rare big wins — judge by expectancy & payoff ratio.</i>`,
  ].join("\n");
}

function fmtSkew(s) {
  const curve = s.skew_curve || [];
  const lines = [
    `<b>Volatility smile — expiry ${s.expiry}</b>`,
    `ATM IV ${n((s.atm_iv || 0) * 100, 1)}% | realized vol ${n((s.realized_vol || 0) * 100, 1)}%`,
    `Vol risk premium (IV−RV): ${n(s.vrp, 1)} pts`,
  ];
  if (curve.length) {
    const lo = curve[0], hi = curve[curve.length - 1];
    lines.push(`Far-OTM ${n(lo.strike, 0)} IV ${n(lo.iv * 100, 1)}% vs near-money ${n(hi.strike, 0)} IV ${n(hi.iv * 100, 1)}%`);
  }
  lines.push(s.vrp !== null && s.vrp !== undefined && s.vrp <= 0
    ? `\nOptions look genuinely cheap vs realized moves.`
    : `\nOptions price in more movement than has occurred (the usual premium).`);
  return lines.join("\n");
}

function fmtEtf(st) {
  const e = st && st.etf_momentum;
  if (!e) return "ETF data not available.";
  if (!e.ranked || !e.ranked.length) return "ETF momentum: " + (e.reasons || []).join("; ");
  const lines = [`<b>ETF sector momentum</b> ${e.fired ? "● setup" : ""}`,
    `Hold (equal weight, rebalance monthly): <b>${(e.hold || []).join(", ")}</b>`, ""];
  for (const r of e.ranked.slice(0, 8)) {
    const star = e.hold && e.hold.includes(r.symbol) ? "★ " : "  ";
    lines.push(`${star}${r.symbol}: ${n(r.momentum_pct, 1)}%  (3m ${n(r.ret_3m_pct, 1)} / 6m ${n(r.ret_6m_pct, 1)})`);
  }
  if (e.trade_plan && e.trade_plan.length) {
    lines.push(`\n<b>Plan</b> (₹${e.budget_inr} budget, equal split):`);
    for (const p of e.trade_plan) {
      const tag = p.cash ? " (cash)" : "";
      lines.push(`• ${p.symbol}${tag}: buy <b>${p.units}</b> units @ ≈${inr(p.price)} (${inr(p.cost)}${p.weight_pct ? ", " + n(p.weight_pct, 0) + "%" : ""})${p.stop_price ? ", stop " + inr(p.stop_price) : ""}`);
    }
    const r = e.rules || {};
    lines.push(`\nTrend: ${r.trend || ""}`);
    lines.push(`Sell: ${r.sell || ""}`);
    lines.push(`Cadence: ${r.cadence || ""}`);
  }
  const bt = st.etf_backtest;
  if (bt && bt.ok && bt.plain && bt.filtered) {
    lines.push(`\n<b>2y backtest</b> (plain → trend-filtered):`);
    lines.push(`• Return ${n(bt.plain.total_return_pct, 0)}% → ${n(bt.filtered.total_return_pct, 0)}%`);
    lines.push(`• Max drawdown ${n(bt.plain.max_drawdown_pct, 1)}% → <b>${n(bt.filtered.max_drawdown_pct, 1)}%</b> (smoother)`);
  }
  lines.push("\n<i>Buy-only, no leverage — lowest capital. Not advice.</i>");
  return lines.join("\n");
}

function fmtHoldings(st) {
  const h = st && st.etf_momentum && st.etf_momentum.holdings;
  if (!h || !h.rows || !h.rows.length) return "No ETF holdings logged yet.";
  const lines = ["<b>Your ETF holdings (real money)</b>"];
  for (const x of h.rows) {
    const pnl = x.pnl === null ? "n/a" : `${inr(x.pnl)} (${n(x.pnl_pct, 1)}%)`;
    lines.push(`• ${x.symbol}: ${x.units}u @ ${inr(x.buy_price)} → now ${x.now === null ? "n/a" : inr(x.now)} | ${pnl}`);
  }
  lines.push(`\nInvested <b>${inr(h.total_cost)}</b> · now <b>${inr(h.total_value)}</b> · P&L <b>${inr(h.total_pnl)} (${n(h.total_pnl_pct, 1)}%)</b>`);
  return lines.join("\n");
}

function fmtCondor(ic, title) {
  if (!ic || !ic.short_put) return `${title}: ` + ((ic && ic.reasons) || ["unavailable"]).join("; ");
  return [
    `<b>${title}</b> ${ic.fired ? "● SETUP" : "○ wait"}`,
    `Sell ${n(ic.short_put,0)} PE + ${n(ic.short_call,0)} CE; buy ${n(ic.long_put,0)}P/${n(ic.long_call,0)}C wings · ${ic.expiry}`,
    `Credit ${inr(ic.max_profit)} | <b>max loss ${inr(ic.max_loss)}</b> | PoP ${n(ic.prob_profit_pct,1)}%`,
    `Profit range ${n(ic.lower_breakeven,0)}–${n(ic.upper_breakeven,0)} | VRP ${n(ic.vrp,1)}`,
    ic.fired ? "\n✓ Rich enough — profit if index stays in range, loss capped. Check Groww."
             : "\n○ Not firing — " + (ic.reasons || []).join("; "),
  ].join("\n");
}

function fmtBankNifty(st) {
  const bn = st && st.banknifty;
  if (!bn) return "BankNIFTY data not available.";
  const cs = bn.credit_spread || {};
  const csLine = cs.short_strike
    ? `<b>BankNIFTY credit spread</b> ${cs.fired ? "● SETUP" : "○ wait"}\nSell ${n(cs.short_strike,0)}/buy ${n(cs.long_strike,0)} PE — credit ${inr(cs.max_profit)}, max loss ${inr(cs.max_loss)}${cs.fired ? "" : " (wait)"}`
    : `<b>BankNIFTY credit spread</b> ○ ${(cs.reasons||["n/a"])[0]}`;
  return csLine + "\n\n" + fmtCondor(bn.iron_condor, "BankNIFTY iron condor") +
    `\n\n<i>Spot ${bn.spot ? n(bn.spot,0) : "n/a"}, lot ${bn.lot}. Higher IV, more ₹ per spread.</i>`;
}

function fmtCredit(st) {
  const c = st && st.credit_spread;
  if (!c) return "Credit-spread data not available.";
  if (!c.short_strike) return "Credit spread: " + (c.reasons || []).join("; ");
  return [
    `<b>Put credit spread</b> ${c.fired ? "● SETUP" : "○ wait"}`,
    `Sell ${n(c.short_strike, 0)} PE / buy ${n(c.long_strike, 0)} PE · ${c.expiry} (${c.dte}d)`,
    `Credit ${inr(c.max_profit)} | <b>max loss ${inr(c.max_loss)}</b> | PoP ${n(c.prob_profit_pct, 1)}%`,
    `Breakeven ${n(c.breakeven, 0)} | VRP ${n(c.vrp, 1)} | IV/RV ${n(c.iv_rv_ratio, 2)}`,
    c.fired
      ? "\n✓ Options rich enough to sell. Max loss is capped — check live prices in Groww."
      : "\n○ Not firing — " + (c.reasons || []).join("; ") + " (premium not rich; no edge in selling).",
  ].join("\n");
}

function fmtTom(st) {
  const t = st && st.turn_of_month;
  if (!t || !t.backtest) return "Turn-of-month: " + ((t && t.reasons) || ["unavailable"]).join("; ");
  const b = t.backtest;
  return [
    `<b>Turn-of-the-Month</b> ${t.fired ? "● IN WINDOW" : "○ cash"}`,
    t.fired ? `Hold <b>${t.etf}</b> now — sell after the first few days of next month.`
            : `In cash — next window starts <b>${t.next_window_start || "?"}</b>.`,
    `In market only ${n(t.in_market_pct, 0)}% of days.`,
    `\n<b>${b.days}d NIFTY backtest</b>: ToM ${n(b.tom.total_return_pct,0)}% ret / ${n(b.tom.max_drawdown_pct,1)}% DD  vs  buy&amp;hold ${n(b.buyhold.total_return_pct,0)}% / ${n(b.buyhold.max_drawdown_pct,1)}% DD`,
    "\n<i>Documented seasonal effect; backtest on NIFTY. Not advice.</i>",
  ].join("\n");
}

function fmtCalendar(st) {
  const k = st && st.calendar;
  if (!k) return "Calendar data not available.";
  if (k.front_atm_iv === undefined || k.front_atm_iv === null) return "Calendar: " + (k.reasons || []).join("; ");
  return [
    `<b>Calendar spread</b> ${k.fired ? "● SETUP" : "○ wait"}`,
    `Front ${k.front_expiry} IV ${n(k.front_atm_iv * 100, 1)}% vs next ${k.next_expiry} IV ${n(k.next_atm_iv * 100, 1)}%`,
    `Term spread ${n(k.term_spread_pts, 2)} pts (needs ≥ ${k.threshold_pts})`,
    k.fired
      ? `\n✓ Front rich — sell front ${n(k.atm_strike, 0)} PE, buy next-month ${n(k.atm_strike, 0)} PE (debit ≈ ${inr(k.est_debit_per_lot)}).`
      : "\n○ Not firing — front month isn't rich enough vs next.",
  ].join("\n");
}

function trimSignal(s) {
  if (!s) return null;
  return {
    date: s.date, spot: s.spot, vix: s.vix, vix_percentile: s.vix_percentile,
    realized_vol: s.realized_vol, atm_iv: s.atm_iv, vrp: s.vrp, expiry: s.expiry,
    fired: s.fired, reasons: s.reasons,
    candidates: (s.candidates || []).slice(0, 3).map(c => ({
      strike: c.strike, dte: c.dte, premium: c.premium, iv: c.iv, skew: c.skew,
      iv_rv_ratio: c.iv_rv_ratio, cost_per_lot: c.cost_per_lot, breakeven: c.breakeven,
    })),
    budget: s.budget,
  };
}

async function askLLM(env, question, signal) {
  if (!env.AI) {
    return "Free-form chat isn't enabled (no AI binding). Use /today, /why, /journal, /backtest, /skew.";
  }
  const model = env.LLM_MODEL || DEFAULT_MODEL;
  const system =
    "You are a READ-ONLY explainer for a NIFTY tail-risk options scanner. " +
    "Explain ONLY using the JSON data provided. You must NEVER give buy/sell/hold advice, " +
    "price targets, or predictions, and never tell the user to place a trade. If asked for advice " +
    "or a forecast, refuse and point them to the data and the education. Keep answers under 120 words. " +
    "This is educational, not investment advice; option buyers lose most (SEBI FY26).";
  try {
    const resp = await env.AI.run(model, {
      max_tokens: 400,
      messages: [
        { role: "system", content: system },
        { role: "user", content: `Scanner data (JSON):\n${JSON.stringify(trimSignal(signal))}\n\nQuestion: ${question}` },
      ],
    });
    const out = (resp && (resp.response || resp.result || resp.text)) || "";
    return out.trim() || "I couldn't form an answer — try /today or /why.";
  } catch (e) {
    return "Chat model error: " + (e && e.message ? e.message : e) + ". Try /today, /why, /journal.";
  }
}

async function sendTelegram(env, chatId, text) {
  if (!env.TELEGRAM_BOT_TOKEN) return;
  const body = { chat_id: chatId, text: text.slice(0, 4000), parse_mode: "HTML", disable_web_page_preview: true };
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
}
