/**
 * Time-based one-time passwords (RFC 6238), and account recovery codes.
 *
 * WHY THERE IS NO QR CODE
 * -----------------------
 * The obvious way to show a QR is to call an image service with the
 * `otpauth://` URI in the query string. That would hand the shared secret --
 * the entire second factor -- to a third party, and put it in their access
 * logs. Rendering one in-browser needs JavaScript, which this site does not
 * ship (`script-src 'none'`), and writing a Reed-Solomon QR encoder inside a
 * Worker is a lot of subtle code whose failure mode is a silently malformed
 * code.
 *
 * So enrolment shows the secret as text plus the `otpauth://` URI. Every
 * authenticator app accepts manual entry, and the secret never leaves the
 * page it was generated on.
 *
 * WHAT IS STORED
 * --------------
 * The TOTP secret must be stored recoverably -- verification needs it, so it
 * cannot be hashed. Recovery codes are different: they are one-shot
 * credentials, so those ARE hashed, exactly like passwords. A dump of the
 * store would expose TOTP secrets but not recovery codes; that asymmetry is
 * inherent to how TOTP works, not an oversight.
 */

const DIGITS = 6;
const PERIOD = 30;              // seconds per code, per RFC 6238
const DRIFT_WINDOWS = 1;        // accept one step either side of now
const SECRET_BYTES = 20;        // 160 bits, the RFC 4226 recommendation
const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

// --------------------------------------------------------------------------
// Base32 (RFC 4648) -- the encoding every authenticator app expects
// --------------------------------------------------------------------------

export function base32Encode(bytes) {
  let bits = 0, value = 0, out = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32[(value << (5 - bits)) & 31];
  return out;
}

export function base32Decode(input) {
  const clean = String(input).toUpperCase().replace(/[\s=-]/g, "");
  let bits = 0, value = 0;
  const out = [];
  for (const ch of clean) {
    const idx = B32.indexOf(ch);
    if (idx === -1) throw new Error("invalid base32");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 255);
      bits -= 8;
    }
  }
  return new Uint8Array(out);
}

/** A fresh 160-bit secret, base32 encoded. */
export function generateSecret() {
  const raw = new Uint8Array(SECRET_BYTES);
  crypto.getRandomValues(raw);
  return base32Encode(raw);
}

/** Grouped into fours so a human can type it without losing their place. */
export function formatSecret(secret) {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

/** The URI an authenticator app imports. */
export function otpauthUri(secret, email, issuer = "Margin & Co.") {
  const label = encodeURIComponent(`${issuer}:${email}`);
  const params = new URLSearchParams({
    secret,
    issuer,
    algorithm: "SHA1",
    digits: String(DIGITS),
    period: String(PERIOD),
  });
  return `otpauth://totp/${label}?${params.toString()}`;
}

// --------------------------------------------------------------------------
// The code itself
// --------------------------------------------------------------------------

/** HMAC-SHA1 of the counter, dynamically truncated to six digits. */
async function codeForCounter(secret, counter) {
  const key = await crypto.subtle.importKey(
    "raw", base32Decode(secret),
    { name: "HMAC", hash: "SHA-1" }, false, ["sign"]
  );

  // Counter as an 8-byte big-endian integer.
  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setUint32(0, Math.floor(counter / 0x100000000));
  view.setUint32(4, counter >>> 0);

  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, buf));

  // Dynamic truncation: the low nibble of the last byte picks the offset.
  const offset = mac[mac.length - 1] & 0x0f;
  const binary = ((mac[offset] & 0x7f) << 24)
               | ((mac[offset + 1] & 0xff) << 16)
               | ((mac[offset + 2] & 0xff) << 8)
               | (mac[offset + 3] & 0xff);

  return String(binary % 10 ** DIGITS).padStart(DIGITS, "0");
}

export function currentCounter(now = Date.now()) {
  return Math.floor(now / 1000 / PERIOD);
}

/**
 * Verify a submitted code.
 *
 * Returns the counter it matched, or null. The caller must record that
 * counter and refuse to accept it again: without that, a code shouted across
 * a room or captured in a screenshot stays valid for its whole 30-second
 * window and can be replayed.
 *
 * Comparison is constant time. The window is one step either side, which
 * tolerates roughly 30 seconds of clock drift -- wider windows trade real
 * security for a problem better fixed by the user's clock.
 */
export async function verifyCode(secret, submitted, lastUsedCounter = null) {
  const digits = String(submitted || "").replace(/\D/g, "");
  if (digits.length !== DIGITS) return null;

  const now = currentCounter();
  for (let offset = -DRIFT_WINDOWS; offset <= DRIFT_WINDOWS; offset++) {
    const counter = now + offset;
    if (lastUsedCounter !== null && counter <= lastUsedCounter) continue;

    const expected = await codeForCounter(secret, counter);
    if (timingSafeEqual(expected, digits)) return counter;
  }
  return null;
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// --------------------------------------------------------------------------
// Recovery codes
// --------------------------------------------------------------------------

const RECOVERY_COUNT = 8;
const RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"; // no l/1, o/0, i

/**
 * Generate recovery codes. Returns the plaintext (shown once) and hashes
 * (stored). Ambiguous characters are excluded because these get written down.
 */
export async function generateRecoveryCodes() {
  const plain = [];
  for (let i = 0; i < RECOVERY_COUNT; i++) {
    const bytes = new Uint8Array(10);
    crypto.getRandomValues(bytes);
    const chars = [...bytes].map((b) => RECOVERY_ALPHABET[b % RECOVERY_ALPHABET.length]);
    plain.push(`${chars.slice(0, 5).join("")}-${chars.slice(5).join("")}`);
  }
  const hashes = await Promise.all(plain.map(hashRecoveryCode));
  return { plain, hashes };
}

export async function hashRecoveryCode(code) {
  const normalised = String(code).toLowerCase().replace(/[^a-z0-9]/g, "");
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(normalised)
  );
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Consume a recovery code. Returns the remaining hashes, or null if no match.
 * A used code is removed, not marked -- these are strictly single use.
 */
export async function consumeRecoveryCode(code, hashes) {
  const candidate = await hashRecoveryCode(code);
  const remaining = (hashes || []).filter((h) => !timingSafeEqual(h, candidate));
  return remaining.length === (hashes || []).length ? null : remaining;
}

export const TOTP_DIGITS = DIGITS;
export const TOTP_PERIOD = PERIOD;
