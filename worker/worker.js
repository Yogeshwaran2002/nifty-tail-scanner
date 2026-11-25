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
      return new Response("nifty-tail-bot up — /setup /check /try /scan (all need ?key=<WEBHOOK_SECRET>)", { status: 200 });
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
    case "/why": return fmtWhy(await getJSON(env, "latest.json"));
    case "/journal": return fmtJournal(await getJSON(env, "latest.json"));
    case "/backtest": return fmtBacktest(await getJSON(env, "backtest.json").catch(() => null));
    case "/skew": return fmtSkew(await getJSON(env, "latest.json"));
    default:
      return askLLM(env, text, await getJSON(env, "latest.json").catch(() => null));
  }
}

function help() {
  return [
    "<b>NIFTY Tail-Risk Scanner bot</b>",
    "",
    "/today — latest scan summary",
    "/why — which entry gates passed/failed",
    "/journal — paper trades + loss budget",
    "/backtest — backtest stats",
    "/skew — volatility smile + risk premium",
    "/id — show your Telegram chat id",
    "",
    "Ask anything else in plain English and I'll explain the numbers.",
    "",
    "<i>Read-only. Educational, not advice. I never tell you to buy or sell.</i>",
  ].join("\n");
}

const n = (x, d = 2) => (x === null || x === undefined) ? "n/a" : Number(x).toFixed(d);
const inr = (x) => (x === null || x === undefined) ? "n/a" : "₹" + Math.round(Number(x)).toLocaleString("en-IN");

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
