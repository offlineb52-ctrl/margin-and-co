/**
 * POST /api/auth/password/signin — sign in with email and password.
 *
 * The alternative to the emailed link, for members who have set a password.
 * Accounts without one are unaffected and keep working exactly as before.
 *
 * Three things this is careful about:
 *
 * 1. It never reveals whether an address has an account. Same wording, same
 *    status, and -- via dummyVerify -- roughly the same CPU cost whether the
 *    account exists, has no password set, or has one that did not match. The
 *    emailed-link form already behaves this way; a password form that did not
 *    would undo it, since an attacker could just use this one instead.
 *
 * 2. A password is not a second factor. If the account has TOTP enabled, a
 *    correct password issues a PENDING session and sends the member to the
 *    same challenge the email link goes through.
 *
 * 3. Attempts are rate limited per address and per IP. Unlike a sign-in link,
 *    a password can be guessed, so this is the only flow here where an
 *    attacker gets to try repeatedly.
 */

import { page, redirect, esc } from "../../../_lib/page.js";
import {
  createSession, sessionCookie, getMember, upsertMember, normaliseEmail,
  rateLimit,
} from "../../../_lib/auth.js";
import {
  verifyPassword, hashPassword, dummyVerify, passwordsAvailable,
} from "../../../_lib/password.js";

const MAX_ATTEMPTS_PER_EMAIL = 8;
const MAX_ATTEMPTS_PER_IP = 20;
const WINDOW_SECONDS = 15 * 60;

/**
 * One message for every failure mode.
 *
 * Wrong password, no password set, no account at all: the visitor is told the
 * same thing. Anything more specific is a membership oracle.
 */
function refused({ status = 401, heading = "That did not match." } = {}) {
  return page({
    title: "Sign in",
    eyebrow: "Sign in",
    heading,
    status,
    body: `
      <p>The email address and password did not match an account. If you are
         not sure whether you set a password, use the emailed link instead —
         it always works, and it does not need one.</p>`,
    actions: `<p class="btn-row">
                <a class="btn btn--solid" href="/login/">Email me a link</a>
                <a class="btn" href="/login/#password">Try again</a>
              </p>`,
  });
}

function unavailable() {
  return page({
    title: "Sign in",
    eyebrow: "Sign in",
    heading: "Password sign-in is not switched on.",
    status: 503,
    body: `<p>This site is not currently configured for password sign-in.
              The emailed link works as normal.</p>`,
    actions: '<p class="btn-row"><a class="btn btn--solid" href="/login/">Email me a link</a></p>',
  });
}

export async function onRequestPost({ request, env }) {
  if (!env.AUTH) return unavailable();
  if (!passwordsAvailable(env)) return unavailable();

  const form = await request.formData();

  // Bots fill hidden fields. A real browser leaves this empty.
  if (form.get("website")) return refused();

  const email = normaliseEmail(form.get("email"));
  const password = String(form.get("password") || "");

  if (!email || !password) return refused({ status: 400 });

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const withinIpLimit = await rateLimit(env, `pw-ip:${ip}`,
                                        MAX_ATTEMPTS_PER_IP, WINDOW_SECONDS);
  const withinEmailLimit = await rateLimit(env, `pw:${email}`,
                                           MAX_ATTEMPTS_PER_EMAIL, WINDOW_SECONDS);
  if (!withinIpLimit || !withinEmailLimit) {
    return page({
      title: "Too many attempts",
      eyebrow: "Sign in",
      heading: "Too many sign-in attempts.",
      status: 429,
      body: `<p>Password attempts are limited to slow down guessing. Wait a
                few minutes, or sign in with an emailed link instead — that
                route is not affected.</p>`,
      actions: '<p class="btn-row"><a class="btn btn--solid" href="/login/">Email me a link</a></p>',
    });
  }

  const member = await getMember(env, email);

  // No account, or an account with no password: spend the same CPU a real
  // check would, then give the same answer.
  if (!member || !member.password_hash) {
    await dummyVerify(env);
    return refused();
  }

  const { valid, needsRehash } = await verifyPassword(
    env, password, member.password_hash);
  if (!valid) return refused();

  const patch = { last_login_at: new Date().toISOString() };
  // A correct password stored under old parameters is upgraded here, so the
  // member never has to be asked to change anything.
  if (needsRehash) {
    patch.password_hash = await hashPassword(env, password);
    patch.password_set_at = new Date().toISOString();
  }
  await upsertMember(env, email, patch);

  const needsSecondFactor = Boolean(member.totp_secret);
  const sessionId = await createSession(env, email, {
    pendingTwoFactor: needsSecondFactor,
  });

  return redirect(
    needsSecondFactor ? "/api/auth/2fa/challenge" : "/members/",
    { "set-cookie": sessionCookie(sessionId) },
  );
}

/** A GET here is someone following a stale link, not a sign-in. */
export async function onRequestGet() {
  return redirect("/login/");
}
