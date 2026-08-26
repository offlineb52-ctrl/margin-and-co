/**
 * Checks on the Pro CSV export. Run with:
 *
 *     node tests/js/test_csv_export.mjs
 *
 * The Functions are JavaScript, so their tests are too -- the Python suite
 * cannot import them. Same plain-assert style as tests/test_indicators.py.
 *
 * The test that matters most here is that a negative number survives export
 * as a number. A spreadsheet-injection guard that is even slightly too eager
 * sees the leading minus of -0.34, quotes it as text, and quietly makes every
 * drawdown in the file impossible to do arithmetic on -- which is the one
 * thing this export exists for.
 */

import { csvCell, buildCsv } from "../../functions/tool/export.js";
let pass = 0, fail = 0;
const check = (n, c, d = "") => { c ? pass++ : fail++; console.log(`  [${c ? "PASS" : "FAIL"}] ${n}${d ? "  -- " + d : ""}`); };

// A real CSV parser, so the test cannot mistake a quoted comma for a delimiter.
function parseRow(line) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (q) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') q = false;
      else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ",") { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur); return out;
}

console.log("csvCell — numbers must stay numbers:");
check("negative float unmangled", csvCell(-0.34) === "-0.34", csvCell(-0.34));
check("negative string unmangled", csvCell("-0.34") === "-0.34", csvCell("-0.34"));
check("positive float unmangled", csvCell(3.5) === "3.5");
check("exponent form unmangled", csvCell("1.2e-5") === "1.2e-5", csvCell("1.2e-5"));
console.log("csvCell — injection still blocked:");
check("=1+1 neutralised", csvCell("=1+1").startsWith("'"), csvCell("=1+1"));
check("@SUM neutralised", csvCell("@SUM(A1)").startsWith("'"));
check("-cmd neutralised (not a number)", csvCell("-cmd|calc").startsWith("'"), csvCell("-cmd|calc"));
check("+text neutralised", csvCell("+bad").startsWith("'"));
console.log("csvCell — quoting:");
check("comma quoted", csvCell("a,b") === '"a,b"');
check("quote doubled", csvCell('say "hi"') === '"say ""hi"""');

const data = {
  ticker: "AAPL", week: 2,
  indicators: { EMA: { score: 3.5, verdict: "Did not survive", performance: 2.8,
    consistency: 5.4, drawdown: 3.2, raw_score: 3.5, capped_by: null,
    out_sample_sharpe_net: 0.06, out_sample_sharpe_gross: 0.08,
    in_sample_sharpe_gross: 0.42, max_drawdown_net: -0.34,
    pct_windows_positive: 0.54, n_windows: 13, num_trades: 22,
    time_in_market: 1.0, cost_paid: 0.036 } },
  history: { EMA: [{ week: 1, published: "2026-08-24", score: 3.5, verdict: "Did not survive", out_sample_sharpe_net: 0.06 }] },
};
const csv = buildCsv(data);
const lines = csv.split("\r\n");
const header = parseRow(lines.find((l) => l.startsWith("Indicator,")));
const row = parseRow(lines.find((l) => l.startsWith("EMA,")));
console.log("\nbuildCsv:");
check("header and data have equal columns", header.length === row.length, `${header.length} vs ${row.length}`);
check("drawdown exports as a usable number", row[header.indexOf("Max drawdown, after costs")] === "-0.34", row[header.indexOf("Max drawdown, after costs")]);
check("null cap becomes empty, not 'null'", row[header.indexOf("Capped by")] === "", JSON.stringify(row[header.indexOf("Capped by")]));
check("gross Sharpe present (Pro-only)", header.includes("Out-of-sample Sharpe, before costs"));
check("history section present", csv.includes("Score history"));
check("not-advice line present", csv.toLowerCase().includes("not investment advice"));
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
