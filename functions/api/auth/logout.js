/**
 * POST /api/auth/logout — end the session.
 *
 * The server-side record is deleted as well as the cookie being cleared.
 * Clearing only the cookie would leave a valid session sitting in storage,
 * which is the difference between signing out and merely looking signed out.
 *
 * POST only, so that a stray link or a prefetching browser cannot sign
 * someone out; combined with SameSite=Lax this also blocks cross-site
 * logout requests.
 */

import { redirect } from "../../_lib/page.js";
import { destroySession, clearedCookieHeader } from "../../_lib/auth.js";

export async function onRequestPost({ request, env }) {
  if (env.AUTH) await destroySession(env, request);
  return redirect("/", { "set-cookie": clearedCookieHeader() });
}

export async function onRequestGet() {
  return redirect("/members/");
}
