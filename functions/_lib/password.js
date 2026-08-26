/**
 * Optional password sign-in.
 *
 * Accounts here are passwordless by default and stay that way. A member who
 * prefers a password can set one; both methods then work. Nobody is made to
 * invent a credential they will reuse from somewhere else.
 *
 * ---------------------------------------------------------------------------
 * Why the iteration count is lower than OWASP recommends, and what makes up
 * for it
 * ---------------------------------------------------------------------------
 * Password hashing has to be slow or an attacker who steals the store can try
 * billions of guesses offline. OWASP asks for 600,000 PBKDF2-SHA256
 * iterations. Cloudflare's free plan allows 10 ms of CPU per request and
 * cannot be raised; 600,000 iterations costs roughly 70 ms, and even 100,000
 * costs about 12 ms. Hashing at full strength would simply fail on this plan.
 *
 * So the work factor is set to what fits, and a PEPPER carries the rest of
 * the weight. The password is HMAC'd with a secret held in Cloudflare's
 * environment -- NOT in KV -- before it is ever hashed. The realistic threat
 * here is a KV dump, and a KV dump alone is not enough to attack these
 * hashes: without the pepper, an attacker cannot even begin guessing, however
 * cheap each guess is. That is a real defence, not a fig leaf, but it is a
 * different defence from a high work factor and it is worth being plain about
 * which one is doing the work.
 *
 * If the project moves to Workers Paid, raise ITERATIONS to 600000 and bump
 * FORMAT_VERSION. Stored hashes record their own iteration count, so old
 * passwords keep verifying and are silently upgraded on next sign-in.
 *
 * Without PASSWORD_PEPPER set, password sign-in is refused outright rather
 * than falling back to unpeppered hashing. A silent downgrade to weaker
 * security is worse than a feature that says it is unavailable.
 */

import { timingSafeEqual } from "./auth.js";

/** Tuned to stay inside the free plan's 10 ms CPU ceiling. See above. */
export const ITERATIONS = 25000;
export const FORMAT_VERSION = 1;

const SALT_BYTES = 16;
const KEY_BITS = 256;

export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 128;

/**
 * Passwords so common that any attacker tries them first. NIST asks for a
 * check like this and explicitly prefers it to composition rules -- forcing a
 * capital and a digit produces Password1!, which is on every list anyway.
 */
const BANNED = new Set([
  "password", "password1", "password123", "passw0rd", "123456", "1234567",
  "12345678", "123456789", "1234567890", "qwerty", "qwertyuiop", "abc123",
  "letmein", "welcome", "monkey", "dragon", "iloveyou", "admin", "login",
  "starwars", "football", "baseball", "trustno1", "changeme", "secret",
  "marginandco", "marginco", "margin&co",
]);

const encoder = new TextEncoder();

function toBase64(bytes) {
  let binary = "";
  for (const b of new Uint8Array(bytes)) binary += String.fromCharCode(b);
  return btoa(binary);
}

function fromBase64(text) {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** True when the environment can do password auth at full configured strength. */
export function passwordsAvailable(env) {
  return Boolean(env && typeof env.PASSWORD_PEPPER === "string"
                 && env.PASSWORD_PEPPER.length >= 16);
}

/**
 * HMAC the password with the server-held pepper.
 *
 * This is what a stolen KV dump does not contain. Done before PBKDF2 so the
 * salted, stretched hash is of a value the attacker cannot produce.
 */
async function pepper(env, plaintext) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(env.PASSWORD_PEPPER),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return crypto.subtle.sign("HMAC", key, encoder.encode(plaintext));
}

async function derive(env, plaintext, salt, iterations) {
  const peppered = await pepper(env, plaintext);
  const key = await crypto.subtle.importKey(
    "raw", peppered, "PBKDF2", false, ["deriveBits"],
  );
  return crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations, hash: "SHA-256" }, key, KEY_BITS,
  );
}

/**
 * Reasons a password is refused, as text a person can act on.
 * Returns null when the password is acceptable.
 */
export function passwordProblem(plaintext) {
  if (typeof plaintext !== "string" || !plaintext) {
    return "Enter a password.";
  }
  if (plaintext.length < MIN_PASSWORD_LENGTH) {
    return `Passwords need at least ${MIN_PASSWORD_LENGTH} characters. `
         + "Length is what makes a password hard to guess, so a short phrase "
         + "of ordinary words beats a short jumble of symbols.";
  }
  if (plaintext.length > MAX_PASSWORD_LENGTH) {
    return `Passwords can be at most ${MAX_PASSWORD_LENGTH} characters.`;
  }
  const flattened = plaintext.toLowerCase().replace(/[\s._-]/g, "");
  if (BANNED.has(flattened)) {
    return "That is one of the most commonly used passwords, so it is one of "
         + "the first an attacker tries. Please choose another.";
  }
  if (/^(.)\1+$/.test(plaintext)) {
    return "That is a single character repeated. Please choose another.";
  }
  return null;
}

/** Hash a password for storage. Returns an opaque, self-describing string. */
export async function hashPassword(env, plaintext) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const bits = await derive(env, plaintext, salt, ITERATIONS);
  return [
    "pbkdf2-sha256", FORMAT_VERSION, ITERATIONS,
    toBase64(salt), toBase64(bits),
  ].join("$");
}

/**
 * Check a password against a stored hash.
 *
 * Returns { valid, needsRehash }. `needsRehash` is true when the stored hash
 * used different parameters from the current ones, so a correct password can
 * be quietly upgraded on sign-in without ever asking the member to change it.
 */
export async function verifyPassword(env, plaintext, stored) {
  if (typeof stored !== "string" || !stored) return { valid: false, needsRehash: false };
  const parts = stored.split("$");
  if (parts.length !== 5 || parts[0] !== "pbkdf2-sha256") {
    return { valid: false, needsRehash: false };
  }
  const iterations = parseInt(parts[2], 10);
  if (!Number.isFinite(iterations) || iterations < 1000 || iterations > 5000000) {
    return { valid: false, needsRehash: false };
  }

  let salt, expected;
  try {
    salt = fromBase64(parts[3]);
    expected = parts[4];
  } catch {
    return { valid: false, needsRehash: false };
  }

  const bits = await derive(env, plaintext, salt, iterations);
  const valid = timingSafeEqual(toBase64(bits), expected);
  return {
    valid,
    needsRehash: valid && (iterations !== ITERATIONS
                           || parseInt(parts[1], 10) !== FORMAT_VERSION),
  };
}

/**
 * Burn the same CPU as a real verification, for an address with no account.
 *
 * Without this, "no such account" returns in a millisecond while a real
 * account takes twenty, and the difference is a reliable way to discover
 * which email addresses are registered. The sign-in flow already returns
 * identical wording for both cases; this makes the timing match too.
 */
export async function dummyVerify(env) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  await derive(env, "timing-equalisation-placeholder", salt, ITERATIONS);
}
