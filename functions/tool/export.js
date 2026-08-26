/**
 * /tool/export?ticker=AAPL — the full working for one ticker, as CSV.
 *
 * Pro only, and the check is server-side on every request: the member record
 * is read from storage, never trusted from the cookie, which carries nothing
 * but a session id.
 *
 * The point of this endpoint is that a reader can check the arithmetic. The
 * site's whole claim is that a result nobody can verify is just an opinion,
 * which is hard to square with publishing a score and withholding the numbers
 * underneath it. So this exports the full working, not a prettified summary:
 * every component, both gross and net Sharpe, and the score history.
 */

import { readSession, getMember } from "../_lib/auth.js";
import { page } from "../_lib/page.js";

const TICKER_PATTERN = /^[A-Z0-9][A-Z0-9.-]{0,11}$/;

function hasProAccess(member) {
  if (!member) return false;
  if (member.tier !== "pro") return false;
  if (member.subscription_status && member.subscription_status !== "active") {
    return false;
  }
  if (member.expires_at && new Date(member.expires_at) < new Date()) return false;
  return true;
}

/**
 * Quote a value for CSV.
 *
 * A verdict like "Did not survive" has no comma today, but a future one might,
 * and a stray comma silently shifts every column after it. Quoting everything
 * that could need it costs nothing and cannot go wrong later.
 *
 * The leading apostrophe guard is for spreadsheet formula injection: a field
 * beginning = + - or @ is executed as a formula by Excel and Sheets when the
 * file is opened. None of this data should ever start that way, which is
 * exactly why it is worth handling -- if it ever does, something is wrong
 * upstream and it must not become code on someone's machine.
 */
export function csvCell(value) {
  if (value === null || value === undefined) return "";
  const text = String(value);

  // A number is never a formula, and must never be quoted or prefixed --
  // doing so makes the spreadsheet read it as text, and the whole reason for
  // this export is that someone can do arithmetic on it. Negative numbers are
  // why this check exists: a naive injection guard sees the leading minus of
  // -0.34 and turns every drawdown in the file into a string.
  const isNumber = typeof value === "number"
    || /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(text);
  if (isNumber) return text;

  // Anything else beginning = + - @ or a control character is executed as a
  // formula by Excel and Sheets on open, so it is neutralised with a leading
  // apostrophe.
  let out = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  if (/[",\n\r]/.test(out)) out = `"${out.replace(/"/g, '""')}"`;
  return out;
}

function csvRow(cells) {
  return cells.map(csvCell).join(",");
}

const SCORE_COLUMNS = [
  ["indicator", "Indicator"],
  ["score", "Survival Score"],
  ["verdict", "Verdict"],
  ["performance", "Performance component (60%)"],
  ["consistency", "Consistency component (25%)"],
  ["drawdown", "Drawdown component (15%)"],
  ["raw_score", "Raw score before caps"],
  ["capped_by", "Capped by"],
  ["out_sample_sharpe_net", "Out-of-sample Sharpe, after costs"],
  ["out_sample_sharpe_gross", "Out-of-sample Sharpe, before costs"],
  ["in_sample_sharpe_gross", "In-sample Sharpe, before costs"],
  ["max_drawdown_net", "Max drawdown, after costs"],
  ["pct_windows_positive", "Walk-forward windows positive"],
  ["n_windows", "Walk-forward windows"],
  ["num_trades", "Trades"],
  ["time_in_market", "Time in market"],
  ["cost_paid", "Paid in costs"],
];

export function buildCsv(data) {
  const lines = [];
  const order = ["EMA", "VWAP", "MACD", "RSI"];
  const names = Object.keys(data.indicators)
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));

  lines.push(`# Margin & Co. — Survival Score working for ${data.ticker}`);
  lines.push(`# Week ${data.week}. Generated ${new Date().toISOString()}.`);
  lines.push("# Research, not investment advice. Not a recommendation.");
  lines.push("");

  lines.push("## Current week");
  lines.push(csvRow(SCORE_COLUMNS.map(([, label]) => label)));
  for (const name of names) {
    const d = data.indicators[name];
    lines.push(csvRow(SCORE_COLUMNS.map(([key]) => (
      key === "indicator" ? name : d[key]
    ))));
  }

  if (data.history && Object.keys(data.history).length) {
    lines.push("");
    lines.push("## Score history");
    lines.push(csvRow(["Indicator", "Week", "Published", "Survival Score",
                       "Verdict", "Out-of-sample Sharpe, after costs"]));
    for (const name of names) {
      for (const row of data.history[name] || []) {
        lines.push(csvRow([name, row.week, row.published, row.score,
                           row.verdict, row.out_sample_sharpe_net]));
      }
    }
  }

  return lines.join("\r\n") + "\r\n";
}

/** Read a build-time asset, bypassing the /members/ gate. See tool/index.js. */
async function readAsset(env, url, path) {
  if (!env.ASSETS) return null;
  let response;
  try {
    response = await env.ASSETS.fetch(new Request(new URL(path, url.origin)));
  } catch {
    return null;
  }
  if (!response.ok) return null;
  if (!(response.headers.get("content-type") || "").includes("json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const ticker = String(url.searchParams.get("ticker") || "")
    .trim().toUpperCase().replace(/^\$/, "").replace(/\s+/g, "");

  const session = env.AUTH ? await readSession(env, request) : null;
  const member = session && env.AUTH ? await getMember(env, session.email) : null;

  if (!hasProAccess(member)) {
    return page({
      title: "Pro only",
      eyebrow: "Tool",
      heading: "The raw data is a Pro export.",
      signedIn: Boolean(session),
      status: session ? 402 : 401,
      body: `<p>The score and its components are free to read on
                <a href="/tool/">the lookup page</a>. The full working as a
                CSV — both gross and net Sharpe, the caps, and the score
                history — is part of Pro.</p>`,
      actions: `<p class="btn-row">
                  <a class="btn btn--solid" href="${session ? "/members/" : "/login/"}"
                    >${session ? "See Pro" : "Sign in"}</a>
                  <a class="btn" href="/tool/">Back to the tool</a>
                </p>`,
    });
  }

  if (!TICKER_PATTERN.test(ticker)) {
    return new Response("Give a ticker, e.g. /tool/export?ticker=AAPL\n", {
      status: 400,
      headers: { "content-type": "text/plain; charset=utf-8",
                 "cache-control": "no-store" },
    });
  }

  const data = await readAsset(env, url, `/members/data/scores/${ticker}.json`);
  if (!data) {
    return new Response(`No working on record for ${ticker}.\n`, {
      status: 404,
      headers: { "content-type": "text/plain; charset=utf-8",
                 "cache-control": "no-store" },
    });
  }

  return new Response(buildCsv(data), {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition":
        `attachment; filename="margin-and-co-${ticker}-week${data.week}.csv"`,
      // A member's export must not be held by a shared cache.
      "cache-control": "no-store, private",
      "x-content-type-options": "nosniff",
    },
  });
}
