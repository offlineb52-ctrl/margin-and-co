/**
 * Gate for everything under /members/.
 *
 * Runs before any members page is served. An unauthenticated request never
 * reaches the content — the check is not a redirect bolted onto a public page,
 * it is a wall in front of the whole directory.
 */

import { page } from "../_lib/page.js";
import { readSession } from "../_lib/auth.js";

export async function onRequest(context) {
  const { request, env, next, data } = context;

  const session = env.AUTH ? await readSession(env, request) : null;

  if (!session) {
    return page({
      title: "Members only",
      eyebrow: "Members",
      heading: "You need to be signed in to read this.",
      body: `<p>Membership is free while the daily research is being built out.
               Sign in with your email address — there is no password to
               create, and none is stored.</p>`,
      status: 401,
      actions: `<p class="btn-row">
                  <a class="btn btn--solid" href="/login/">Sign in</a>
                  <a class="btn" href="/join/">Create an account</a>
                </p>`,
    });
  }

  // A half-authenticated session must not reach members content. This is
  // checked server-side against the stored session, not against anything the
  // client holds, so the flag cannot be edited away in a cookie.
  if (session.pending_2fa) {
    return page({
      title: "Two-factor required",
      eyebrow: "Sign in",
      heading: "One more step.",
      status: 401,
      body: `<p>Your email link checked out, but this account has two-factor
             authentication switched on. Enter the code from your
             authenticator to finish signing in.</p>`,
      actions: `<p class="btn-row"><a class="btn btn--solid"
                   href="/api/auth/2fa/challenge">Enter my code</a></p>`,
    });
  }

  // Make the signed-in member available to the page that runs next.
  data.member = session;

  const response = await next();

  // Authenticated pages must never be cached by a shared cache.
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store, no-cache, must-revalidate, private");
  headers.set("x-robots-tag", "noindex, nofollow");
  return new Response(response.body, { ...response, headers });
}
