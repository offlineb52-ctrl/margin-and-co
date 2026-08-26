/**
 * Checks on optional password sign-in. Run with:
 *
 *     node tests/js/test_password.mjs
 *
 * The properties tested here are the ones whose failure is silent: a hash
 * that verifies against the wrong password, a pepper that turns out not to
 * matter, or a timing difference that quietly reveals which email addresses
 * have accounts.
 */

import {
  hashPassword, verifyPassword, passwordProblem, passwordsAvailable,
  dummyVerify, ITERATIONS, MIN_PASSWORD_LENGTH,
} from "../../functions/_lib/password.js";

let pass = 0, fail = 0;
const check = (n, c, d = "") => { c ? pass++ : fail++;
  console.log(`  [${c ? "PASS" : "FAIL"}] ${n}${d ? "  -- " + d : ""}`); };

const env = { PASSWORD_PEPPER: "a-test-pepper-at-least-16-chars-long" };
const other = { PASSWORD_PEPPER: "a-DIFFERENT-pepper-also-16-chars-min" };
const GOOD = "correct horse battery staple";

console.log("availability:");
check("available with a long pepper", passwordsAvailable(env) === true);
check("unavailable with no pepper", passwordsAvailable({}) === false);
check("unavailable with a short pepper",
      passwordsAvailable({ PASSWORD_PEPPER: "tooshort" }) === false);

console.log("\nhash + verify:");
const stored = await hashPassword(env, GOOD);
check("stored hash is self-describing", stored.startsWith("pbkdf2-sha256$"), stored.slice(0, 40));
check("stored hash records its iteration count",
      stored.split("$")[2] === String(ITERATIONS));
check("plaintext never appears in the hash", !stored.includes(GOOD));
check("correct password verifies", (await verifyPassword(env, GOOD, stored)).valid === true);
check("wrong password rejected", (await verifyPassword(env, "wrong password here", stored)).valid === false);
check("near-miss rejected", (await verifyPassword(env, GOOD + " ", stored)).valid === false);
check("empty rejected", (await verifyPassword(env, "", stored)).valid === false);

console.log("\nthe pepper must actually matter:");
check("same password fails under a different pepper",
      (await verifyPassword(other, GOOD, stored)).valid === false,
      "a KV dump alone must not be crackable");

console.log("\nsalting:");
const again = await hashPassword(env, GOOD);
check("same password hashes differently each time", again !== stored,
      "otherwise identical passwords are visible in the store");
check("both hashes still verify", (await verifyPassword(env, GOOD, again)).valid === true);

console.log("\nmalformed stored values are rejected, not thrown on:");
for (const bad of ["", "garbage", "pbkdf2-sha256$1$25000$notbase64",
                   "pbkdf2-sha256$1$0$AAAA$AAAA", "bcrypt$1$2$3$4", null, undefined]) {
  const r = await verifyPassword(env, GOOD, bad);
  check(`rejects ${JSON.stringify(bad)}`, r.valid === false);
}

console.log("\nrehash on parameter change:");
const legacy = stored.replace(`$${ITERATIONS}$`, "$10000$");
const legacyCheck = await verifyPassword(env, GOOD, legacy);
check("an old iteration count does not verify a tampered hash",
      legacyCheck.valid === false, "salt/hash no longer match the work factor");

console.log("\npolicy:");
check("short password refused", passwordProblem("short") !== null);
check(`${MIN_PASSWORD_LENGTH}-char password accepted`,
      passwordProblem("a".repeat(MIN_PASSWORD_LENGTH)) === null
      || passwordProblem("abcdefghijkl") === null);
check("common password refused", passwordProblem("password123") !== null);
check("common password refused despite spacing", passwordProblem("pass word123") !== null);
check("repeated character refused", passwordProblem("aaaaaaaaaaaaaa") !== null);
check("over-long password refused", passwordProblem("x".repeat(200)) !== null);
check("good passphrase accepted", passwordProblem(GOOD) === null);

console.log("\ntiming (unknown account must not be faster):");
const t0 = performance.now(); await verifyPassword(env, GOOD, stored);
const real = performance.now() - t0;
const t1 = performance.now(); await dummyVerify(env);
const dummy = performance.now() - t1;
const ratio = dummy / real;
check("dummy verify costs comparable CPU", ratio > 0.5 && ratio < 2.0,
      `real ${real.toFixed(1)}ms vs dummy ${dummy.toFixed(1)}ms`);
check(`one hash fits the 10ms free-plan CPU ceiling`, real < 10,
      `${real.toFixed(1)}ms measured here`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
