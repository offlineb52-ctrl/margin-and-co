/**
 * GET /api/auth/verify?token=… — redeem a sign-in link.
 *
 * The token is consumed before a session is issued, so a link cannot be used
 * twice. That matters more than it sounds: sign-in links end up in forwarded
 * emails, shared screens, and browser history on shared machines.
 */

import { page, redirect } from "../../_lib/page.js";
import {
  consumeLoginToken, createSession, sessionCookie, upsertMember,
} from "../../_lib/auth.js";

function invalid() {
  return page({
    title: "Link expired",
    eyebrow: "Sign in",
    heading: "That link has expired or been used.",
    body: `<p>Sign-in links work once and last 15 minutes — that is deliberate,
             and it is what stops an old link in an inbox from becoming a way
             into your account.</p>
           <p>Request a fresh one and it will arrive in a few seconds.</p>`,
    status: 400,
    actions: '<p class="btn-row"><a class="btn btn--solid" href="/login/">Get a new link</a></p>',
  });
}

export async function onRequestGet({ request, env }) {
  if (!env.AUTH) return invalid();

  const token = new URL(request.url).searchParams.get("token");
  const email = await consumeLoginToken(env, token);
  if (!email) return invalid();

  await upsertMember(env, email, { last_login_at: new Date().toISOString() });
  const sessionId = await createSession(env, email);

  // Straight to the members area, with the session cookie attached.
  return redirect("/members/", { "set-cookie": sessionCookie(sessionId) });
}
