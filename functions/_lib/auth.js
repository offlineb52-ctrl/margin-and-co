/**
 * Authentication primitives.
 *
 * WHY THERE ARE NO PASSWORDS HERE
 * -------------------------------
 * This system never accepts, hashes, or stores a password. Sign-in works by
 * emailing a single-use link. That removes, by construction, the three things
 * that cause most account breaches: password reuse across sites, weak
 * passwords, and a stored credential database worth stealing.
 *
 * It is also the only design that works on this site at all. Every hosted
 * auth widget (Clerk, Auth0, Firebase) is JavaScript, and this site sets
 * `script-src 'none'` — which is what makes cross-site scripting structurally
 * impossible here. Handing that up to gain a login box would have been a bad
 * trade, so authentication happens entirely server-side instead: an HTML form
 * posts, a Function responds, a cookie is set. No client JavaScript at all.
 *
 * WHAT IS STORED, AND WHAT IS NOT
 * -------------------------------
 * Tokens and session identifiers are stored **hashed**, never in the clear.
 * Anyone who obtained a dump of the storage could not sign in as a member,
 * because the raw values are only ever held by the member's own browser or
 * inbox. This is the same reason a bank stores a hash of your password rather
 * than the password.
 */

const TOKEN_BYTES = 32;                      // 256 bits of entropy
const LOGIN_TOKEN_TTL_SECONDS = 15 * 60;     // magic links expire fast
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;
const SESSION_COOKIE = "mc_session";

/** Cryptographically secure random identifier, URL-safe. */
export function randomToken(bytes = TOKEN_BYTES) {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return btoa(String.fromCharCode(...raw))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** SHA-256, hex encoded. Used so storage never holds a usable credential. */
export async function hashToken(token) {
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(token)
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Constant-time string comparison.
 *
 * A normal `===` on secrets leaks information through how long it takes to
 * fail. It is a small leak, and exploiting it over a network is hard, but the
 * correct comparison costs one function and removes the question entirely.
 */
export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export const EMAIL_PATTERN = /^[^\s@,;:<>()[\]\\]+@[^\s@.]+(\.[^\s@.]+)+$/;
export const MAX_EMAIL_LENGTH = 254;

export function normaliseEmail(value) {
  const email = String(value || "").trim().toLowerCase();
  if (!email || email.length > MAX_EMAIL_LENGTH || !EMAIL_PATTERN.test(email)) {
    return null;
  }
  return email;
}

// --------------------------------------------------------------------------
// Login tokens
// --------------------------------------------------------------------------

/**
 * Issue a single-use sign-in token. Returns the RAW token, which is emailed
 * and then immediately forgotten by the server — only its hash is kept.
 */
export async function createLoginToken(env, email) {
  const token = randomToken();
  const key = `login:${await hashToken(token)}`;

  await env.AUTH.put(key, JSON.stringify({
    email,
    created_at: new Date().toISOString(),
  }), { expirationTtl: LOGIN_TOKEN_TTL_SECONDS });

  return token;
}

/**
 * Redeem a sign-in token. Single use: the record is deleted before the
 * session is created, so a link that leaks (forwarded email, shared screen,
 * browser history on a shared machine) cannot be replayed.
 */
export async function consumeLoginToken(env, token) {
  if (!token || typeof token !== "string" || token.length > 200) return null;

  const key = `login:${await hashToken(token)}`;
  const record = await env.AUTH.get(key, { type: "json" });
  if (!record) return null;

  await env.AUTH.delete(key);
  return record.email;
}

// --------------------------------------------------------------------------
// Sessions
// --------------------------------------------------------------------------

export async function createSession(env, email) {
  const sessionId = randomToken();
  await env.AUTH.put(`session:${await hashToken(sessionId)}`, JSON.stringify({
    email,
    created_at: new Date().toISOString(),
  }), { expirationTtl: SESSION_TTL_SECONDS });
  return sessionId;
}

export async function readSession(env, request) {
  const sessionId = readCookie(request, SESSION_COOKIE);
  if (!sessionId) return null;
  return env.AUTH.get(`session:${await hashToken(sessionId)}`, { type: "json" });
}

export async function destroySession(env, request) {
  const sessionId = readCookie(request, SESSION_COOKIE);
  if (sessionId) await env.AUTH.delete(`session:${await hashToken(sessionId)}`);
}

export function readCookie(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

/**
 * Session cookie attributes, and why each one is there:
 *   HttpOnly  — unreadable by scripts, so an XSS bug cannot steal the session
 *   Secure    — never sent over plain HTTP
 *   SameSite  — Lax blocks cross-site request forgery on state-changing posts
 *               while still allowing normal inbound links to work
 *   Path=/    — one session for the whole site
 */
export function sessionCookie(sessionId, maxAge = SESSION_TTL_SECONDS) {
  return `${SESSION_COOKIE}=${sessionId}; HttpOnly; Secure; SameSite=Lax; `
       + `Path=/; Max-Age=${maxAge}`;
}

export function clearedCookieHeader() {
  return `${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`;
}

// --------------------------------------------------------------------------
// Members
// --------------------------------------------------------------------------

export async function getMember(env, email) {
  return env.AUTH.get(`member:${email}`, { type: "json" });
}

export async function upsertMember(env, email, patch = {}) {
  const existing = (await getMember(env, email)) || {
    email,
    joined_at: new Date().toISOString(),
    tier: "free",
  };
  const member = { ...existing, ...patch };
  await env.AUTH.put(`member:${email}`, JSON.stringify(member));
  return member;
}

// --------------------------------------------------------------------------
// Rate limiting
// --------------------------------------------------------------------------

/**
 * Crude per-key limiter backed by KV.
 *
 * KV is eventually consistent, so this is not exact and a determined attacker
 * could squeeze extra attempts through. It is not the only defence — tokens
 * are single-use, short-lived and 256 bits — but it stops the obvious abuse:
 * using the sign-in form to spray email at someone else's address.
 */
export async function rateLimit(env, key, limit, windowSeconds) {
  const bucket = `rl:${key}:${Math.floor(Date.now() / (windowSeconds * 1000))}`;
  const count = parseInt((await env.AUTH.get(bucket)) || "0", 10);
  if (count >= limit) return false;
  await env.AUTH.put(bucket, String(count + 1), { expirationTtl: windowSeconds + 60 });
  return true;
}
