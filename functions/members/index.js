/**
 * /members/ — the member dashboard.
 *
 * ON WHAT THIS PAGE DOES AND DOES NOT PUBLISH
 * -------------------------------------------
 * Everything here is *systematic research output*: what the published rules
 * flagged, what the live paper portfolio holds, and what it cost. None of it
 * is a recommendation to buy or sell, and none of it should become one.
 *
 * That is a legal boundary, not a stylistic preference. In the UK, advising on
 * investments by way of business without FCA authorisation is an offence under
 * s.19 of the Financial Services and Markets Act 2000, and charging for the
 * service is what makes "by way of business" easy to establish. Reporting what
 * a published, mechanical rule did is research. Telling a paying member which
 * stock to buy is advice. The wording on this page stays on the correct side
 * of that line, and any future contributor should keep it there.
 */

import { page, esc } from "../_lib/page.js";

function fmtPct(value, places = 1) {
  if (value === null || value === undefined) return "&mdash;";
  const sign = value > 0 ? "num--pos" : value < 0 ? "num--neg" : "num--nil";
  return `<span class="num ${sign}">${(value * 100).toFixed(places)}%</span>`;
}

function fmtNum(value, places = 2) {
  if (value === null || value === undefined) return "&mdash;";
  return Number(value).toLocaleString("en-GB", {
    minimumFractionDigits: places, maximumFractionDigits: places,
  });
}

async function loadJson(origin, path) {
  try {
    const response = await fetch(`${origin}${path}`, { cf: { cacheTtl: 60 } });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export async function onRequestGet({ request, data }) {
  const origin = new URL(request.url).origin;
  const email = data?.member?.email || "";

  const live = await loadJson(origin, "/live/live.json");

  let portfolio = `<div class="note"><p>The live portfolio data isn't
      available at the moment. It refreshes after each trading session.</p></div>`;

  if (live) {
    const p = live.performance || {};
    const positions = live.positions || [];
    const pending = live.pending_orders || [];

    const rows = positions.length
      ? positions.map((h) => `
          <tr>
            <td>${esc(h.ticker)}</td>
            <td class="num">${fmtNum(h.shares, 0)}</td>
            <td class="num">${fmtNum(h.cost_basis)}</td>
            <td class="num">${esc(h.opened_on)}</td>
          </tr>`).join("")
      : `<tr><td colspan="4">Entirely in cash — no name is currently
           oversold on the rule.</td></tr>`;

    const orders = pending.length
      ? pending.map((o) => `
          <tr>
            <td>${esc(o.ticker)}</td>
            <td>${esc(o.side)}</td>
            <td class="num">${fmtNum(o.shares, 0)}</td>
            <td class="reason">${esc(o.reason || "")}</td>
          </tr>`).join("")
      : `<tr><td colspan="4">No orders standing for the next open.</td></tr>`;

    portfolio = `
      <div class="stats">
        <div class="stat">
          <div class="stat__label">Portfolio value</div>
          <div class="stat__value">${fmtNum(p.nav, 0)}</div>
          <div class="stat__note">${esc(live.book?.currency || "USD")}, from 100,000</div>
        </div>
        <div class="stat">
          <div class="stat__label">Total return</div>
          <div class="stat__value">${fmtPct(p.total_return)}</div>
          <div class="stat__note">${live.book?.sessions || 0} sessions</div>
        </div>
        <div class="stat">
          <div class="stat__label">Buy &amp; hold</div>
          <div class="stat__value">${fmtPct(p.benchmark_return)}</div>
          <div class="stat__note">same names, same period</div>
        </div>
        <div class="stat">
          <div class="stat__label">Exposure</div>
          <div class="stat__value">${fmtPct(p.current_invested, 0)}</div>
          <div class="stat__note">rest in cash</div>
        </div>
      </div>

      <h2>Open positions</h2>
      <div class="table-scroll"><table>
        <thead><tr><th>Ticker</th><th>Shares</th><th>Avg entry</th><th>Opened</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>

      <h2>Committed for the next open</h2>
      <p>Published before they execute. Whatever is listed here either fills at
         the next session's open or is withdrawn on the record.</p>
      <div class="table-scroll"><table>
        <thead><tr><th>Ticker</th><th>Side</th><th>Shares</th><th>Why</th></tr></thead>
        <tbody>${orders}</tbody>
      </table></div>`;
  }

  const body = `
    <div class="note note--flag">
      <p><strong>What this is, and what it is not.</strong> Everything below is
         the output of a mechanical rule that is published in full, applied to
         public data. It is research, not advice — there are no
         recommendations, price targets or forecasts here, and there will not
         be. Nothing on this site is a suggestion to buy or sell anything.</p>
    </div>

    ${portfolio}

    <h2>Where the research stands</h2>
    <p>Across 763 S&amp;P 500 and FTSE 350 companies, three of the four
       indicators tested produced a negative Sharpe ratio out-of-sample after
       costs. RSI was the only survivor at 0.48, and it still lost to buy &amp;
       hold at 1.50. The <a href="/reports/week-01/">full report</a> shows the
       working.</p>

    <h2>Your membership</h2>
    <p>Signed in as <strong>${esc(email)}</strong>. Membership is currently
       <strong>free</strong> — daily research updates are being built out, and
       nothing will be charged without asking you first.</p>
    <p>To leave, email <a href="mailto:hello@marginco.co.uk">hello@marginco.co.uk</a>
       and the account and its record are deleted, not deactivated.</p>`;

  return page({
    title: "Members",
    eyebrow: "Members",
    heading: "Daily research",
    body,
    signedIn: true,
    actions: `
      <form method="post" action="/api/auth/logout" class="btn-row">
        <a class="btn" href="/members/security/">Security &amp; 2FA</a>
        <a class="btn" href="/live/">Public live page</a>
        <button class="btn" type="submit">Sign out</button>
      </form>`,
  });
}
