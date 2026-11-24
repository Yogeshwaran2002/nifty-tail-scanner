"use strict";

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
    tile(rv === null ? "n/a" : pct(rv), "Realized vol (annual)"),
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
  const s = await getJSON("data/latest.json");
  const bt = await getJSON("data/backtest.json");
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
  renderBacktest(bt);
  el("disclaimer").textContent = s.disclaimer ||
    "Educational only. Not investment advice. SEBI FY26: ~87.7% of F&O traders lost money; option buyers lost most. Paper-trade until results earn trust.";
})();
