/**
 * /members/pro/ — the Pro report.
 *
 * Two gates, in order:
 *   1. `functions/members/_middleware.js` has already required a valid session.
 *   2. This page requires that session's member to have an active subscription.
 *
 * The subscription check reads the member record from storage on every
 * request. It does not trust anything in the cookie beyond the session id,
 * because a cookie is data the client holds and could tamper with; the tier
 * is looked up server-side each time.
 */

import { page, esc } from "../../_lib/page.js";
import { getMember } from "../../_lib/auth.js";

/** True only for a member whose subscription is explicitly active. */
function hasProAccess(member) {
  if (!member) return false;
  if (member.tier !== "pro") return false;
  if (member.subscription_status && member.subscription_status !== "active") {
    return false;
  }
  // An expiry in the past revokes access even if the tier was never changed.
  if (member.expires_at && new Date(member.expires_at) < new Date()) {
    return false;
  }
  return true;
}

function upgradeWall(email) {
  return page({
    title: "Margin & Co. Pro",
    eyebrow: "Pro",
    heading: "This report is for Pro members.",
    signedIn: true,
    status: 402,
    body: `
      <p>You are signed in as <strong>${esc(email)}</strong> on the free plan.</p>

      <h2>What Pro adds</h2>
      <ul class="limits">
        <li><strong>The full ranked Survival Score table</strong> — every
            indicator against every stock tested that week, not just the
            flagship finding.</li>
        <li><strong>The raw data</strong> as a CSV, so you can check the
            arithmetic yourself rather than taking the table on trust.</li>
        <li><strong>Full methodology notes</strong> for each week, including
            every assumption that could change the result.</li>
        <li><strong>The complete archive</strong> and score history, so you can
            see whether a rule is drifting rather than only where it stands
            today.</li>
      </ul>

      <div class="note note--flag">
        <p><strong>What Pro does not add: recommendations.</strong> There are no
           stock tips at any price here. Pro buys the working, the breadth and
           the history — never a suggestion about what to buy. Nothing on this
           site is investment advice.</p>
      </div>

      <div class="note">
        <p><strong>Pro is not on sale yet.</strong> Subscriptions are not open,
           and no payment is being taken anywhere on this site. When they do
           open the price is intended to be £5 a month. Until then, the free
           weekly report carries the actual conclusion — which is the part that
           should be public anyway.</p>
        <p>Email <a href="mailto:hello@marginco.co.uk">hello@marginco.co.uk</a>
           to be told when it opens.</p>
      </div>`,
    actions: `<p class="btn-row">
                <a class="btn btn--solid" href="/members/">Back to members</a>
                <a class="btn" href="/">This week's free report</a>
              </p>`,
  });
}

export async function onRequestGet({ request, env, data }) {
  const email = data?.member?.email || "";
  const member = env.AUTH ? await getMember(env, email) : null;

  if (!hasProAccess(member)) return upgradeWall(email);

  const origin = new URL(request.url).origin;

  // Pro report data lives under a members-only path, so it is never fetchable
  // by an anonymous visitor even if they guess the filename.
  let report = null;
  try {
    const response = await fetch(`${origin}/members/data/latest_pro.json`);
    if (response.ok) report = await response.json();
  } catch { /* fall through to the empty state */ }

  if (!report) {
    return page({
      title: "Pro", eyebrow: "Pro", heading: "No Pro report published yet.",
      signedIn: true,
      body: "<p>The next one appears here as soon as it is generated.</p>",
    });
  }

  const rows = (report.sections?.table || []).map((r) => `
    <tr>
      <td>${esc(r.indicator)}</td>
      <td>${esc(r.ticker)}</td>
      <td class="num"><strong>${Number(r.score).toFixed(1)}</strong></td>
      <td>${esc(r.verdict)}</td>
      <td class="num">${fmt(r.out_sample_sharpe_net)}</td>
      <td class="num">${pct(r.pct_windows_positive)}</td>
      <td class="num">${pct(r.max_drawdown_net)}</td>
      <td class="num">${r.num_trades ?? "—"}</td>
    </tr>`).join("");

  const notes = (report.sections?.methodology || [])
    .map((n) => `<li>${esc(n)}</li>`).join("");

  return page({
    title: `Week ${report.week} — Pro`,
    eyebrow: `Pro · Week ${report.week}`,
    heading: report.headline || "This week's results",
    signedIn: true,
    body: `
      <p>${esc(report.summary || "")}</p>

      <h2>Full Survival Score table</h2>
      <div class="table-scroll"><table>
        <thead><tr>
          <th>Indicator</th><th>Ticker</th><th>Score</th><th>Verdict</th>
          <th>OOS Sharpe (net)</th><th>Windows +ve</th>
          <th>Max drawdown</th><th>Trades</th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="8">No rows.</td></tr>'}</tbody>
      </table></div>

      <p class="btn-row">
        <a class="btn" href="/members/data/week${String(report.week).padStart(2, "0")}_pro_scores.csv">
          Download the raw data (CSV)
        </a>
      </p>

      <h2>Methodology</h2>
      <ul class="limits">${notes}</ul>`,
    actions: '<p class="btn-row"><a class="btn" href="/members/">Back to members</a></p>',
  });
}

function fmt(value) {
  return value === null || value === undefined ? "—" : Number(value).toFixed(2);
}

function pct(value) {
  return value === null || value === undefined
    ? "—" : `${(Number(value) * 100).toFixed(0)}%`;
}
