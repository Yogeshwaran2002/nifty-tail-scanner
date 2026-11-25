"use strict";

const WORKER_BASE = "https://nifty-tail-bot.yogeshwaran2002.workers.dev";

function wireToolbar() {
  const msg = el("toolmsg");
  const reload = el("reloadbtn");
  const scan = el("scanbtn");
  if (reload) reload.onclick = () => location.reload();
  if (!scan) return;
  scan.onclick = async () => {
    let key = localStorage.getItem("nts_key");
    if (!key) {
      key = prompt("Enter your WEBHOOK_SECRET (set once, stored in this browser):");
      if (!key) return;
      localStorage.setItem("nts_key", key.trim());
    }
    scan.disabled = true;
    msg.textContent = "Triggering scan…";
    try {
      const r = await fetch(`${WORKER_BASE}/scan?key=${encodeURIComponent(key.trim())}`);
      const d = await r.json();
      if (d.ok) {
        msg.textContent = "✓ Scan triggered — GitHub Action is running. Hit ↻ Reload in ~1–2 min.";
      } else if (d.error === "bad key") {
        localStorage.removeItem("nts_key");
        msg.textContent = "✗ Wrong secret — click again to re-enter.";
      } else {
        msg.textContent = "✗ " + (d.error || d.detail || "could not trigger");
      }
    } catch (e) {
      msg.textContent = "✗ Could not reach the Worker (" + e.message + ")";
    } finally {
      scan.disabled = false;
    }
  };
}

const fmt = (x, nd = 2) => (x === null || x === undefined || Number.isNaN(x)) ? "n/a" : Number(x).toFixed(nd);
const inr = (x, nd = 0) => (x === null || x === undefined) ? "n/a" : "₹" + Number(x).toLocaleString("en-IN", { maximumFractionDigits: nd });
const pct = (x, nd = 1) => (x === null || x === undefined) ? "n/a" : fmt(x, nd) + "%";
const el = (id) => document.getElementById(id);

async function getJSON(path) {
  try {
    const r = await fetch(path + "?t=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function tile(big, label) {
  return `<div class="card tile"><div class="big">${big}</div><div class="label">${label}</div></div>`;
}

function renderBanner(s) {
  const b = el("banner");
  if (s.errors && s.errors.length) {
    b.className = "banner error";
    b.innerHTML = `⚠️ Data error — scan could not complete. ${s.errors.map(e => `<span>${e}</span>`).join(" ")}`;
    return;
  }
  if (s.fired) {
    b.className = "banner fired";
    b.innerHTML = `🟢 <span>${s.candidates.length} candidate(s) passed every gate. See below — then verify the live spread in Groww.</span>`;
  } else {
    b.className = "banner notrade";
    b.innerHTML = `⚪ <span>No trade today. The gates below explain why — discipline means waiting.</span>`;
  }
}

function renderTiles(s) {
  const rv = s.realized_vol ? s.realized_vol * 100 : null;
  const atmIv = (s.atm_iv !== null && s.atm_iv !== undefined) ? s.atm_iv * 100 : null;
  el("tiles").innerHTML = [
    tile(fmt(s.spot, 0), "NIFTY spot"),
    tile(fmt(s.vix, 2), "India VIX"),
    tile(s.vix_percentile === null ? "n/a" : fmt(s.vix_percentile, 0), "VIX percentile (1y)"),
    tile(rv === null ? "n/a" : pct(rv), s.rv_method === "yang-zhang" ? "Realized vol (Yang–Zhang)" : "Realized vol (annual)"),
    tile(atmIv === null ? "n/a" : pct(atmIv), "ATM implied vol"),
    tile(s.vrp === null || s.vrp === undefined ? "n/a" : fmt(s.vrp, 1) + " pts", "Vol risk premium (IV−RV)"),
  ].join("");
}

function renderVix(s) {
  const p = s.vix_percentile;
  const warn = (s.warnings || []).length ? `<div class="muted" style="font-size:12px;margin-top:6px">⚠ ${s.warnings.join("; ")}</div>` : "";
  if (p === null || p === undefined) { el("vixnote").innerHTML = "VIX percentile unavailable for this scan." + warn; return; }
  el("vixpin").style.left = Math.max(0, Math.min(100, p)) + "%";
  el("vixnote").innerHTML = `VIX ${fmt(s.vix, 2)} sits at the <b>${fmt(p, 0)}th percentile</b> of the last year. ` +
    (p <= 30 ? "Calm regime — the entry gate for VIX is open." : "Not calm enough — the VIX gate is shut.") + warn;
}

function gateChip(g) {
  const v = g.value === null ? "n/a" : Number(g.value).toFixed(2);
  return `<span class="chip ${g.passed ? "pass" : "fail"}" title="${g.detail} (limit ${g.threshold})">${g.passed ? "✓" : "✗"} ${g.name}: ${v}</span>`;
}

function renderCandidates(s) {
  const body = el("candbody");
  if (s.fired && s.candidates.length) {
    el("spreadnote").style.display = "";
    let rows = s.candidates.map(c => `
      <tr>
        <td>${fmt(c.strike, 0)} PE</td>
        <td>${c.expiry} (${c.dte}d)</td>
        <td>${inr(c.premium, 2)}</td>
        <td>${inr(c.cost_per_lot)}</td>
        <td>${pct((c.iv || 0) * 100)}</td>
        <td>${fmt(c.skew, 1)}</td>
        <td>${fmt(c.iv_rv_ratio, 2)}</td>
        <td>${fmt(c.breakeven, 0)} (${fmt(c.breakeven_move_pct, 1)}%)</td>
      </tr>`).join("");
    body.innerHTML = `<table><thead><tr>
      <th>Strike</th><th>Expiry</th><th>Premium</th><th>Cost/lot</th>
      <th>IV</th><th>Skew pts</th><th>IV/RV</th><th>Breakeven</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } else {
    el("spreadnote").style.display = "none";
    const reasons = (s.reasons || []).map(r => `<li>${r}</li>`).join("");
    body.innerHTML = `<p class="muted">No strike passed all gates${s.expiry ? ` for expiry ${s.expiry}` : ""}.</p>` +
      (reasons ? `<ul class="muted" style="font-size:13px">${reasons}</ul>` : "");
  }
}

function renderSkew(s) {
  const curve = s.skew_curve || [];
  const note = el("skewnote");
  if (curve.length < 2) {
    el("skewchart").innerHTML = `<p class="muted" style="font-size:13px">No skew curve for this scan (needs option IVs).</p>`;
    note.textContent = "";
    return;
  }
  const W = 960, H = 240, padL = 44, padR = 20, padT = 20, padB = 34;
  const xs = curve.map(p => p.strike), ys = curve.map(p => p.iv * 100);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.max(0, Math.min(...ys) - 2), maxY = Math.max(...ys) + 2;
  const sx = v => padL + (W - padL - padR) * ((v - minX) / ((maxX - minX) || 1));
  const sy = v => H - padB - (H - padT - padB) * ((v - minY) / ((maxY - minY) || 1));
  const d = curve.map((p, i) => `${i ? "L" : "M"}${sx(p.strike).toFixed(1)},${sy(p.iv * 100).toFixed(1)}`).join(" ");
  // ATM marker
  const atm = s.atm_strike;
  const atmX = atm ? sx(atm) : null;
  const yticks = [minY, (minY + maxY) / 2, maxY].map(v =>
    `<text x="6" y="${(sy(v) + 4).toFixed(1)}" fill="var(--ink-muted)" font-size="10">${v.toFixed(0)}%</text>
     <line class="axis" x1="${padL}" y1="${sy(v).toFixed(1)}" x2="${W - padR}" y2="${sy(v).toFixed(1)}" stroke-dasharray="3 5" opacity="0.4"/>`).join("");
  const xticks = [minX, (minX + maxX) / 2, maxX].map(v =>
    `<text x="${sx(v).toFixed(1)}" y="${H - 12}" fill="var(--ink-muted)" font-size="10" text-anchor="middle">${v.toFixed(0)}</text>`).join("");
  const dots = curve.map(p =>
    `<circle class="dot" cx="${sx(p.strike).toFixed(1)}" cy="${sy(p.iv * 100).toFixed(1)}" r="2.5"><title>${p.strike} PE — IV ${(p.iv*100).toFixed(1)}% (moneyness ${(p.moneyness*100).toFixed(0)}%)</title></circle>`).join("");
  el("skewchart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Implied volatility by strike">
    ${yticks}${xticks}
    ${atmX !== null ? `<line x1="${atmX.toFixed(1)}" y1="${padT}" x2="${atmX.toFixed(1)}" y2="${H - padB}" stroke="var(--ink-muted)" stroke-dasharray="4 4"/>
      <text x="${atmX.toFixed(1)}" y="${padT - 6}" fill="var(--ink-muted)" font-size="10" text-anchor="middle">ATM ${atm}</text>` : ""}
    <path class="line" d="${d}"/>${dots}
    <text x="${padL}" y="14" fill="var(--ink-muted)" font-size="11">Implied vol (%) vs strike</text>
  </svg>`;
  if (s.vrp !== null && s.vrp !== undefined) {
    note.innerHTML = `Volatility risk premium (ATM IV − realized vol) = <b>${fmt(s.vrp, 1)} vol points</b>. ` +
      (s.vrp <= 0 ? "Options look genuinely cheap vs realized moves — a real tail-buying signal."
                  : "Options price in more movement than has occurred — the usual premium buyers pay.");
  } else { note.textContent = ""; }
}

function badge(fired) {
  return fired
    ? `<span class="chip pass">● SETUP</span>`
    : `<span class="chip" style="background:var(--surface-3);color:var(--ink-2);border-color:var(--border)">○ wait</span>`;
}

function renderStrategies(st) {
  const credit = el("strat-credit"), cal = el("strat-calendar"), etf = el("strat-etf");
  if (!st) {
    credit.innerHTML = `<p class="muted">Strategy data not available yet.</p>`;
    cal.innerHTML = etf.innerHTML = "";
    return;
  }
  // Credit spread
  const c = st.credit_spread || {};
  if (c.short_strike) {
    credit.innerHTML = `
      <h2>Put credit spread ${badge(c.fired)} <span class="muted" style="font-weight:400">— sell premium, defined risk</span></h2>
      <p style="margin:0 0 10px">Sell <b>${fmt(c.short_strike,0)} PE</b> / buy <b>${fmt(c.long_strike,0)} PE</b> · expiry ${c.expiry} (${c.dte}d)</p>
      <table><tbody>
        <tr><td>Net credit (you receive)</td><td>${inr(c.max_profit)}</td></tr>
        <tr><td><b>Max loss (capital at risk)</b></td><td><b>${inr(c.max_loss)}</b></td></tr>
        <tr><td>Prob. of profit (≈)</td><td>${pct(c.prob_profit_pct)}</td></tr>
        <tr><td>Breakeven (NIFTY above)</td><td>${fmt(c.breakeven,0)}</td></tr>
        <tr><td>VRP / IV÷RV</td><td>${fmt(c.vrp,1)} pts / ${fmt(c.iv_rv_ratio,2)}</td></tr>
      </tbody></table>
      ${c.fired
        ? `<div class="warn-note" style="color:var(--good);background:var(--good-bg);border-color:var(--good)">✓ Options are rich enough to sell. Max loss is capped at ${inr(c.max_loss)} — check live prices in Groww first.</div>`
        : `<p class="muted" style="font-size:13px;margin-top:10px">Not firing — ${(c.reasons||[]).join("; ")}. (Premium isn't rich now; selling has no edge.)</p>`}`;
  } else {
    credit.innerHTML = `<h2>Put credit spread ${badge(false)}</h2><p class="muted">${(c.reasons||["unavailable"]).join("; ")}</p>`;
  }
  // Calendar
  const k = st.calendar || {};
  if (k.front_atm_iv !== undefined && k.front_atm_iv !== null) {
    cal.innerHTML = `
      <h2>Calendar spread ${badge(k.fired)} <span class="muted" style="font-weight:400">— volatility term structure</span></h2>
      <p style="margin:0">Front ${k.front_expiry} IV <b>${pct(k.front_atm_iv*100)}</b> vs next ${k.next_expiry} IV <b>${pct(k.next_atm_iv*100)}</b> → term spread <b>${fmt(k.term_spread_pts,2)} pts</b> (needs ≥ ${k.threshold_pts}).</p>
      ${k.fired
        ? `<div class="warn-note" style="color:var(--good);background:var(--good-bg);border-color:var(--good)">✓ Front month is rich — sell the ${fmt(k.atm_strike,0)} front PE, buy the next-month ${fmt(k.atm_strike,0)} PE (net debit ≈ ${inr(k.est_debit_per_lot)}).</div>`
        : `<p class="muted" style="font-size:13px;margin-top:8px">Not firing — front month isn't rich enough vs next.</p>`}`;
  } else {
    cal.innerHTML = `<h2>Calendar spread ${badge(false)}</h2><p class="muted">${(k.reasons||["unavailable"]).join("; ")}</p>`;
  }
  // ETF momentum
  const e = st.etf_momentum || {};
  if (e.ranked && e.ranked.length) {
    const rows = e.ranked.map((r,i) => {
      const tr = r.trend === "up" ? `<span style="color:var(--good)">▲ up</span>`
               : r.trend === "down" ? `<span style="color:var(--critical)">▼ down</span>` : "n/a";
      return `<tr${i < e.top_n ? ' style="font-weight:600"' : ''}>
      <td>${i < e.top_n ? "★ " : ""}${r.symbol}</td><td>${inr(r.last)}</td>
      <td>${r.ret_3m_pct===null?"n/a":fmt(r.ret_3m_pct,1)+"%"}</td>
      <td>${r.ret_6m_pct===null?"n/a":fmt(r.ret_6m_pct,1)+"%"}</td>
      <td>${fmt(r.momentum_pct,1)}%</td><td>${tr}</td></tr>`;
    }).join("");
    const plan = (e.trade_plan || []).map(p =>
      `<tr><td>${p.symbol}${p.cash ? " <span style='color:var(--ink-muted)'>(cash)</span>" : ""}</td><td>${p.price===null?"—":inr(p.price)}</td><td><b>${p.units}</b> units</td><td>${p.cost===null?"—":inr(p.cost)}</td><td>${p.stop_price===null?"—":inr(p.stop_price)}</td></tr>`).join("");
    const rules = e.rules || {};
    const h = e.holdings;
    const bt = st.etf_backtest;
    let btHtml = "";
    if (bt && bt.ok && bt.plain && bt.filtered) {
      const P = bt.plain, F = bt.filtered;
      btHtml = `<div style="margin-top:14px">
        <div class="muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">2-year backtest — plain vs trend-filtered</div>
        <table><thead><tr><th>Metric</th><th>Plain momentum</th><th>+ Trend filter ★</th></tr></thead><tbody>
          <tr><td>Total return</td><td>${fmt(P.total_return_pct,1)}%</td><td>${fmt(F.total_return_pct,1)}%</td></tr>
          <tr><td>CAGR</td><td>${fmt(P.cagr_pct,1)}%</td><td>${fmt(F.cagr_pct,1)}%</td></tr>
          <tr><td>Win rate (months)</td><td>${fmt(P.win_rate_pct,0)}%</td><td>${fmt(F.win_rate_pct,0)}%</td></tr>
          <tr><td><b>Max drawdown</b></td><td style="color:var(--critical)">${fmt(P.max_drawdown_pct,1)}%</td><td style="color:var(--good)"><b>${fmt(F.max_drawdown_pct,1)}%</b></td></tr>
          <tr><td>Worst month</td><td>${fmt(P.worst_month_pct,1)}%</td><td>${fmt(F.worst_month_pct,1)}%</td></tr>
        </tbody></table>
        <div class="muted" style="font-size:12px;margin-top:8px">Trend-filtered equity (recommended):</div>
        ${etfEquitySVG(F.equity_curve)}
        <p class="muted" style="font-size:12px;margin-top:6px">${
          Math.abs(F.max_drawdown_pct - P.max_drawdown_pct) < 1
            ? "The trend filter was <b>dormant</b> this window (markets trended up), so returns are identical — it cost nothing. Its job is to move to cash and cut losses in the next sustained downturn (free downside insurance for a loss-averse plan)."
            : `The trend filter cut max drawdown from ${fmt(P.max_drawdown_pct,1)}% to ${fmt(F.max_drawdown_pct,1)}% — a smoother ride for a small return trade-off.`
        }</p>
        <p class="muted" style="font-size:12px;margin-top:4px">${bt.note}</p>
      </div>`;
    }
    let holdingsHtml = "";
    if (h && h.rows && h.rows.length) {
      const hr = h.rows.map(x => {
        const pnlColor = x.pnl === null ? "" : x.pnl >= 0 ? "color:var(--good)" : "color:var(--critical)";
        return `<tr><td>${x.symbol}</td><td>${x.units}</td><td>${inr(x.buy_price)}</td><td>${x.now===null?"n/a":inr(x.now)}</td><td>${x.value===null?"n/a":inr(x.value)}</td><td style="${pnlColor}">${x.pnl===null?"n/a":inr(x.pnl)+" ("+fmt(x.pnl_pct,1)+"%)"}</td></tr>`;
      }).join("");
      const tColor = h.total_pnl >= 0 ? "color:var(--good)" : "color:var(--critical)";
      holdingsHtml = `<div style="margin:4px 0 14px;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2)">
        <div style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-muted);margin-bottom:6px">Your holdings (real money)</div>
        <table><thead><tr><th>ETF</th><th>Qty</th><th>Buy</th><th>Now</th><th>Value</th><th>P&L</th></tr></thead><tbody>${hr}</tbody></table>
        <div style="margin-top:8px;font-size:14px">Invested <b>${inr(h.total_cost)}</b> · now <b>${inr(h.total_value)}</b> · <b style="${tColor}">${inr(h.total_pnl)} (${fmt(h.total_pnl_pct,1)}%)</b></div>
      </div>`;
    }
    etf.innerHTML = `
      <h2>ETF sector momentum ${badge(e.fired)} <span class="muted" style="font-weight:400">— buy-only, lowest capital</span></h2>
      ${holdingsHtml}
      <p style="margin:0 0 10px">Hold (equal weight, rebalance monthly): <b>${(e.hold||[]).join(", ")}</b></p>
      <table><thead><tr><th>ETF</th><th>Price</th><th>3m</th><th>6m</th><th>Momentum</th><th>Trend</th></tr></thead><tbody>${rows}</tbody></table>
      ${plan ? `<div style="margin-top:14px"><div class="muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Trade plan — ₹${e.budget_inr} budget, equal split</div>
        <table><thead><tr><th>Buy</th><th>Limit ≈</th><th>Qty</th><th>Cost</th><th>Stop (−${e.trailing_stop_pct}%)</th></tr></thead><tbody>${plan}</tbody></table></div>` : ""}
      <ul class="muted" style="font-size:13px;margin:12px 0 0;padding-left:18px">
        <li><b>Buy:</b> ${rules.buy || ""}</li>
        <li><b>Quantity:</b> ${rules.sizing || ""}</li>
        <li><b>Trend filter:</b> ${rules.trend || ""}</li>
        <li><b>Stop-loss:</b> ${rules.stop || ""}</li>
        <li><b>Sell:</b> ${rules.sell || ""}</li>
        <li><b>Cadence:</b> ${rules.cadence || ""}</li>
      </ul>
      ${btHtml}`;
  } else {
    etf.innerHTML = `<h2>ETF sector momentum ${badge(false)}</h2><p class="muted">${(e.reasons||["unavailable"]).join("; ")}</p>`;
  }
}

function renderBudget(s) {
  const b = s.budget;
  if (!b) { el("budget").innerHTML = ""; return; }
  const usedPct = b.budget ? Math.min(100, 100 * b.loss_used / b.budget) : 0;
  const cls = b.locked ? "critical" : usedPct > 60 ? "warning" : "good";
  el("budget").innerHTML = `
    <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px">
      <span>Loss budget ${b.locked ? "<b style='color:var(--critical)'>· LOCKED</b>" : ""}</span>
      <span class="mono">${inr(b.loss_used)} used · ${inr(b.remaining)} left of ${inr(b.budget)}</span>
    </div>
    <div class="bar ${cls}"><span style="width:${usedPct}%"></span></div>
    <div class="muted" style="font-size:12px;margin-top:6px">Realized paper P&L: ${inr(b.realized_pnl)}</div>`;
}

function renderJournal(s) {
  const open = s.open_trades || [];
  if (!open.length) { el("journalbody").innerHTML = `<p class="muted" style="font-size:13px">No open paper trades.</p>`; return; }
  const rows = open.map(t => `<tr>
    <td>#${t.id}</td><td>${fmt(t.strike, 0)} PE</td><td>${t.expiry}</td>
    <td>${inr(t.entry_premium, 2)}</td><td>${t.lots}×${t.lot_size}</td><td>${inr(t.cost)}</td>
    <td>${t.open_date}</td></tr>`).join("");
  el("journalbody").innerHTML = `<table><thead><tr>
    <th>ID</th><th>Strike</th><th>Expiry</th><th>Entry</th><th>Qty</th><th>Cost</th><th>Opened</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderBacktest(bt) {
  const body = el("backtestbody");
  if (!bt || !bt.ok) {
    body.innerHTML = `<p class="muted">${bt ? bt.reason : "No backtest yet — it grows as the daily scan archives more days."}</p>`;
    return;
  }
  body.innerHTML = `
    <div class="grid" style="margin-bottom:14px">
      ${tile(bt.days_tested, "Days tested")}
      ${tile(bt.total_trades, "Trades")}
      ${tile(pct(bt.hit_rate_pct), "Hit rate")}
      ${tile(inr(bt.net_pnl), "Net P&L")}
      ${tile(inr(bt.expectancy), "Expectancy / trade")}
      ${tile(bt.payoff_ratio === null || bt.payoff_ratio === undefined ? "n/a" : fmt(bt.payoff_ratio, 2) + "×", "Payoff ratio")}
      ${tile(inr(bt.best_trade), "Best trade")}
      ${tile(inr(bt.max_drawdown), "Max drawdown")}
    </div>
    <p class="muted" style="font-size:13px">${bt.note}</p>
    ${equitySVG(bt.equity_curve)}`;
}

function etfEquitySVG(curve) {
  if (!curve || curve.length < 2) return "";
  const W = 960, H = 160, pad = 30;
  const ys = curve.map(p => p.equity);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = i => pad + (W - 2 * pad) * (i / (curve.length - 1));
  const sy = v => H - pad - (H - 2 * pad) * ((v - minY) / ((maxY - minY) || 1));
  const d = curve.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.equity).toFixed(1)}`).join(" ");
  const base = sy(1.0);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="ETF momentum equity curve">
    <line class="axis" x1="${pad}" y1="${base}" x2="${W - pad}" y2="${base}" stroke-dasharray="4 4"/>
    <path class="line" d="${d}"/>
    <text x="${pad}" y="14" fill="var(--ink-muted)" font-size="11">Growth of ₹1 (dashed = starting value)</text>
  </svg>`;
}

function equitySVG(curve) {
  if (!curve || curve.length < 2) return "";
  const W = 960, H = 180, pad = 28;
  const xs = curve.map((_, i) => i), ys = curve.map(p => p.realized_pnl);
  const minY = Math.min(0, ...ys), maxY = Math.max(0, ...ys);
  const sx = i => pad + (W - 2 * pad) * (i / (curve.length - 1));
  const sy = v => H - pad - (H - 2 * pad) * ((v - minY) / ((maxY - minY) || 1));
  const d = curve.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.realized_pnl).toFixed(1)}`).join(" ");
  const zero = sy(0);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="Equity curve of realized paper P&L">
    <line class="axis" x1="${pad}" y1="${zero}" x2="${W - pad}" y2="${zero}" stroke-dasharray="4 4"/>
    <path class="line" d="${d}"/>
    <text x="${pad}" y="16" fill="var(--ink-muted)" font-size="11">Realized P&L (₹) over archived days</text>
  </svg>`;
}

(async function main() {
  wireToolbar();
  const s = await getJSON("data/latest.json");
  const bt = await getJSON("data/backtest.json");
  const strat = await getJSON("data/strategies.json");
  if (!s) {
    el("banner").className = "banner notrade";
    el("banner").textContent = "No scan data yet. The first daily run will populate this dashboard.";
    el("asof").textContent = "no data";
    return;
  }
  el("asof").textContent = "as of " + s.date;
  renderBanner(s);
  renderTiles(s);
  renderVix(s);
  renderSkew(s);
  renderCandidates(s);
  renderBudget(s);
  renderJournal(s);
  renderStrategies(strat);
  renderBacktest(bt);
  el("disclaimer").textContent = s.disclaimer ||
    "Educational only. Not investment advice. SEBI FY26: ~87.7% of F&O traders lost money; option buyers lost most. Paper-trade until results earn trust.";
})();
