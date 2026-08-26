/**
 * /tool/ — the self-serve Survival Score lookup.
 *
 * Type a ticker, get that company's score for all four indicators, with the
 * working shown. This is the product the weekly report is an advertisement
 * for: the report tells you what happened to one company, this answers the
 * question a reader actually has, which is "what about mine?".
 *
 * No JavaScript. The form is a plain GET, the results are rendered on the
 * server, and the whole page still runs under `script-src 'none'`. That is
 * the same constraint the sign-in flow was built to, and it is why a lookup
 * works in any browser, with any blocker, on any connection.
 *
 * Tiers, per the product:
 *   anonymous  a few lookups, soft-limited by cookie (see checkQuota)
 *   free       a monthly allowance, counted server-side against the account
 *   pro        unlimited, plus score history and a CSV of the working
 */

import { page, esc } from "../_lib/page.js";
import { readSession, getMember, hashToken } from "../_lib/auth.js";

const ANON_MONTHLY_LOOKUPS = 3;
const FREE_MONTHLY_LOOKUPS = 10;
const QUOTA_COOKIE = "mc_lookups";

/** Ticker symbols as this project writes them: AAPL, BRK-B, AAF.L */
const TICKER_PATTERN = /^[A-Z0-9][A-Z0-9.-]{0,11}$/;

/** "2026-08-24" -> "24 August 2026", matching how the built site writes dates. */
function longDate(iso) {
  if (!iso) return "";
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}

/** The calendar month a quota belongs to, e.g. "2026-08". */
function currentPeriod() {
  return new Date().toISOString().slice(0, 7);
}

/**
 * Clean up whatever was typed into the box.
 *
 * People paste "$aapl", "aapl ", and "AAPL.L" interchangeably. Rejecting
 * those as invalid would be technically correct and useless.
 */
function normaliseTicker(raw) {
  if (!raw) return "";
  return String(raw).trim().toUpperCase()
    .replace(/^\$/, "")
    .replace(/\s+/g, "");
}

function hasProAccess(member) {
  if (!member) return false;
  if (member.tier !== "pro") return false;
  if (member.subscription_status && member.subscription_status !== "active") {
    return false;
  }
  if (member.expires_at && new Date(member.expires_at) < new Date()) return false;
  return true;
}

function readCookieValue(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return null;
}

/**
 * Decide whether this lookup is allowed, and how many are left.
 *
 * Signed-in allowances are counted in KV against the account, so they are
 * real. The anonymous allowance is held in a cookie, which the visitor
 * controls -- clearing it resets the count. That is a deliberate trade, not
 * an oversight: counting anonymous visitors properly means storing IP
 * addresses, and the privacy policy promises that reading this site is not
 * logged against you. A soft limit that respects that promise is worth more
 * than a hard one that breaks it, and the real gate on heavy use is that an
 * account is free.
 */
async function checkQuota(env, request, member) {
  if (hasProAccess(member)) {
    return { allowed: true, tier: "pro", limit: null, used: null, remaining: null };
  }

  const period = currentPeriod();

  if (member && env.AUTH) {
    const key = `lookups:${await hashToken(member.email)}:${period}`;
    const used = parseInt((await env.AUTH.get(key)) || "0", 10);
    if (used >= FREE_MONTHLY_LOOKUPS) {
      return { allowed: false, tier: "free", limit: FREE_MONTHLY_LOOKUPS,
               used, remaining: 0, key, period };
    }
    return { allowed: true, tier: "free", limit: FREE_MONTHLY_LOOKUPS,
             used, remaining: FREE_MONTHLY_LOOKUPS - used, key, period };
  }

  const raw = readCookieValue(request, QUOTA_COOKIE) || "";
  const [cookiePeriod, rawCount] = raw.split(":");
  const used = cookiePeriod === period ? parseInt(rawCount, 10) || 0 : 0;
  if (used >= ANON_MONTHLY_LOOKUPS) {
    return { allowed: false, tier: "anon", limit: ANON_MONTHLY_LOOKUPS,
             used, remaining: 0, period };
  }
  return { allowed: true, tier: "anon", limit: ANON_MONTHLY_LOOKUPS,
           used, remaining: ANON_MONTHLY_LOOKUPS - used, period };
}

/** Record that a lookup happened. Returns headers to set, if any. */
async function consumeQuota(env, quota) {
  if (quota.tier === "pro") return {};
  if (quota.tier === "free" && quota.key && env.AUTH) {
    // Expires a little over two months out, so an old month's counter cannot
    // accumulate in storage forever.
    await env.AUTH.put(quota.key, String(quota.used + 1),
                       { expirationTtl: 70 * 24 * 60 * 60 });
    return {};
  }
  const value = `${quota.period}:${quota.used + 1}`;
  return {
    "set-cookie": `${QUOTA_COOKIE}=${value}; Path=/; Max-Age=5529600; `
                  + "HttpOnly; Secure; SameSite=Lax",
  };
}

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------

function fmt(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function searchForm(value = "", autofocus = true) {
  return `
    <form class="lookup" method="get" action="/tool/" role="search">
      <label for="ticker">Ticker symbol</label>
      <input id="ticker" name="ticker" type="text" inputmode="latin"
             autocapitalize="characters" autocomplete="off" spellcheck="false"
             maxlength="12" required ${autofocus ? "autofocus" : ""}
             placeholder="AAPL" value="${esc(value)}">
      <button class="btn btn--solid" type="submit">Get the score</button>
    </form>
    <p class="meta">US and UK listings. London tickers end in
       <code>.L</code> — for example <code>SHEL.L</code>.</p>`;
}

function quotaLine(quota) {
  if (quota.tier === "pro") {
    return `<p class="meta">Pro — unlimited lookups.</p>`;
  }
  const left = quota.remaining;
  const noun = left === 1 ? "lookup" : "lookups";
  if (quota.tier === "free") {
    return `<p class="meta">${left} ${noun} left this month.
            <a href="/members/">Pro</a> removes the limit.</p>`;
  }
  return `<p class="meta">${left} free ${noun} left this month.
          <a href="/join/">Create a free account</a> for ${FREE_MONTHLY_LOOKUPS}
          a month.</p>`;
}

/** The score breakdown table for one ticker. */
function resultTable(data) {
  const order = ["EMA", "VWAP", "MACD", "RSI"];
  const names = Object.keys(data.indicators)
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));

  const rows = names.map((name) => {
    const d = data.indicators[name];
    return `
      <tr>
        <th scope="row">${esc(name)}</th>
        <td class="num"><strong>${fmt(d.score, 1)}</strong></td>
        <td>${esc(d.verdict ?? "—")}</td>
        <td class="num">${fmt(d.out_sample_sharpe_net)}</td>
        <td class="num">${pct(d.max_drawdown_net)}</td>
        <td class="num">${pct(d.pct_windows_positive)}</td>
        <td class="num">${d.num_trades ?? "—"}</td>
        <td class="num">${pct(d.cost_paid)}</td>
      </tr>`;
  }).join("");

  return `
    <div class="table-scroll">
      <table>
        <caption class="visually-hidden">Survival Score for ${esc(data.ticker)}, week ${esc(data.week)},
                 published ${esc(longDate(data.published))}.</caption>
        <thead>
          <tr>
            <th scope="col">Indicator</th>
            <th scope="col">Score</th>
            <th scope="col">Verdict</th>
            <th scope="col">OOS Sharpe<br><span class="meta">after costs</span></th>
            <th scope="col">Max drawdown</th>
            <th scope="col">Windows<br><span class="meta">positive</span></th>
            <th scope="col">Trades</th>
            <th scope="col">Paid in costs</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/** How the score was built, so the number is never a black box. */
function componentTable(data) {
  const order = ["EMA", "VWAP", "MACD", "RSI"];
  const names = Object.keys(data.indicators)
    .sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const rows = names.map((name) => {
    const d = data.indicators[name];
    return `<tr>
        <th scope="row">${esc(name)}</th>
        <td class="num">${fmt(d.performance, 2)}</td>
        <td class="num">${fmt(d.consistency, 2)}</td>
        <td class="num">${fmt(d.drawdown, 2)}</td>
        <td class="num"><strong>${fmt(d.score, 1)}</strong></td>
      </tr>`;
  }).join("");

  return `
    <h2>How each score was built</h2>
    <p>The score is 60% out-of-sample Sharpe after costs, 25% consistency
       across walk-forward windows, and 15% drawdown, each scored out of 10
       and then weighted. The thresholds were fixed in code before the numbers
       were seen. <a href="/methodology/">The full method is here.</a></p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Indicator</th>
            <th scope="col">Performance<br><span class="meta">60%</span></th>
            <th scope="col">Consistency<br><span class="meta">25%</span></th>
            <th scope="col">Drawdown<br><span class="meta">15%</span></th>
            <th scope="col">Score</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

const DISCLAIMER = `
  <div class="note note--flag">
    <p><strong>This is not a recommendation.</strong> A Survival Score says
       whether a published rule held up on past data once costs were removed.
       It is not a view on the company, a forecast, or advice to buy or sell
       anything. Most scores are low, and that is the finding.</p>
  </div>`;

// --------------------------------------------------------------------------
// Request handling
// --------------------------------------------------------------------------

/**
 * Read a build-time asset, bypassing the /internal/ middleware.
 *
 * Returns null for anything that is not a JSON file this build produced.
 * A missing asset does NOT arrive as a 404: Pages answers it with the site's
 * own not-found page, as HTML, with a 200 status. Checking `response.ok`
 * alone therefore passes, and `.json()` then throws on the leading `<`.
 * Every unknown ticker would 500. So the content type is checked too, and
 * parsing is guarded.
 */
async function readInternal(env, url, path) {
  if (!env.ASSETS) return null;
  let response;
  try {
    response = await env.ASSETS.fetch(new Request(new URL(path, url.origin)));
  } catch {
    return null;
  }
  if (!response.ok) return null;
  const type = response.headers.get("content-type") || "";
  if (!type.includes("json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function onRequestGet(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const raw = url.searchParams.get("ticker");
  const ticker = normaliseTicker(raw);

  const session = env.AUTH ? await readSession(env, request) : null;
  const member = session && env.AUTH ? await getMember(env, session.email) : null;
  const signedIn = Boolean(session);

  const index = await readInternal(env, url, "/internal/scores/_index.json");
  const universe = index ? index.count : 0;
  const asOf = index ? index.published : null;

  // No ticker asked for: the landing state.
  if (!raw) {
    return page({
      title: "Survival Score lookup",
      eyebrow: "Tool",
      heading: "Does your stock's indicator actually survive?",
      robots: "index, follow",
      description: "Look up the Survival Score for any S&P 500 or FTSE 350 "
                   + "company across EMA, VWAP, MACD and RSI, after realistic "
                   + "trading costs.",
      signedIn,
      body: `
        <p>Enter a ticker and see how EMA, VWAP, MACD and RSI performed on it
           out of sample, after spread, commission and slippage are taken
           out.${universe ? ` ${universe} companies tested` : ""}${asOf
             ? `, as of ${esc(longDate(asOf))}` : ""}.</p>
        ${searchForm()}
        ${DISCLAIMER}`,
    });
  }

  if (!TICKER_PATTERN.test(ticker)) {
    return page({
      title: "Survival Score lookup",
      eyebrow: "Tool",
      heading: "That does not look like a ticker.",
      robots: "noindex, follow",
      signedIn,
      status: 400,
      body: `<p>Ticker symbols are short — letters and digits, sometimes with a
                dot or a hyphen, like <code>AAPL</code>, <code>BRK-B</code> or
                <code>SHEL.L</code>. Try again:</p>
             ${searchForm(ticker)}`,
    });
  }

  const quota = await checkQuota(env, request, member);
  if (!quota.allowed) {
    const upgrade = quota.tier === "anon"
      ? `<p class="btn-row">
           <a class="btn btn--solid" href="/join/">Create a free account</a>
         </p>
         <p>A free account raises the limit to ${FREE_MONTHLY_LOOKUPS} lookups
            a month. It takes an email address and no password.</p>`
      : `<p>You have used all ${FREE_MONTHLY_LOOKUPS} of this month's lookups.
            Pro removes the limit and adds score history and the raw data as a
            CSV.</p>
         <p class="btn-row"><a class="btn btn--solid" href="/members/">See Pro</a></p>`;
    return page({
      title: "Lookup limit reached",
      eyebrow: "Tool",
      heading: "That is this month's lookups used up.",
      robots: "noindex, follow",
      signedIn,
      status: 429,
      body: `${upgrade}
             <p class="meta">The count resets on the 1st. The weekly report
                stays free either way — <a href="/reports/">read the
                archive</a>.</p>`,
    });
  }

  const data = await readInternal(env, url, `/internal/scores/${ticker}.json`);

  if (!data) {
    const known = index && Array.isArray(index.tickers)
      && index.tickers.includes(ticker);
    return page({
      title: `${ticker} — not tested`,
      eyebrow: "Tool",
      heading: `${ticker} has not been tested.`,
      robots: "noindex, follow",
      signedIn,
      status: 404,
      body: `
        <p>${known
              ? "That company is in the universe but its file is missing from "
                + "this build, which is a bug worth reporting."
              : "The universe is the S&P 500 and the FTSE 350, and only "
                + "companies with at least five years of history are tested. "
                + "Anything newly listed, delisted, or outside those indices "
                + "will not be here."}</p>
        <p>A lookup that finds nothing does not count against your allowance.</p>
        ${searchForm(ticker)}`,
    });
  }

  // Only a lookup that actually returned a result is charged for.
  const extraHeaders = await consumeQuota(env, quota);
  const charged = { ...quota, used: quota.used + 1,
                    remaining: quota.remaining === null ? null : quota.remaining - 1 };

  const best = data.best_indicator ? data.indicators[data.best_indicator] : null;
  const anySurvived = Object.values(data.indicators)
    .some((d) => (d.verdict || "").toLowerCase().startsWith("survived"));

  const verdictLine = anySurvived
    ? `<p><strong>At least one rule survived on ${esc(data.ticker)}.</strong>
          That is unusual, and it is not a reason to trade it — one week of
          survival is what you would expect from chance alone across a
          universe this size.</p>`
    : `<p><strong>None of the four survived on ${esc(data.ticker)}.</strong>
          ${best ? `The least bad was ${esc(data.best_indicator)} at
          ${fmt(best.score, 1)} out of 10.` : ""} That is the usual answer,
          and it is the point of publishing it.</p>`;

  const proBlock = quota.tier === "pro"
    ? `<h2>Pro</h2>
       <p class="btn-row">
         <a class="btn" href="/members/pro/">Full ranked table</a>
         <a class="btn" href="/members/data/scores/${esc(ticker)}.json">Raw data</a>
       </p>`
    : `<div class="note">
         <p><strong>Pro adds the history.</strong> Whether ${esc(data.ticker)}'s
            score is drifting week to week matters more than where it stands
            today, and one week cannot show it.
            <a href="/members/">What Pro includes</a>.</p>
       </div>`;

  return page({
    title: `${ticker} Survival Score`,
    eyebrow: "Tool",
    heading: `${ticker}`,
    robots: "index, follow",
    description: `Survival Score for ${ticker} across EMA, VWAP, MACD and RSI, `
                 + `after realistic trading costs.`,
    signedIn,
    headers: extraHeaders,
    body: `
      ${verdictLine}
      ${resultTable(data)}
      ${componentTable(data)}
      ${DISCLAIMER}
      ${proBlock}
      <h2>Look up another</h2>
      ${searchForm("", false)}
      ${quotaLine(charged)}`,
  });
}
