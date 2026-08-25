/** POST /api/auth/2fa/setup — generate a pending secret. Not yet active. */

import { redirect, page } from "../../../_lib/page.js";
import { readSession, getMember, upsertMember } from "../../../_lib/auth.js";
import { generateSecret } from "../../../_lib/totp.js";

export async function onRequestPost({ request, env }) {
  const session = env.AUTH ? await readSession(env, request) : null;
  if (!session || session.pending_2fa) return redirect("/login/");

  const member = await getMember(env, session.email);
  if (member?.totp_secret) return redirect("/members/security/");

  // Stored as pending: generating a secret must not switch anything on.
  await upsertMember(env, session.email, { totp_pending: generateSecret() });
  return redirect("/members/security/");
}

export async function onRequestGet() {
  return redirect("/members/security/");
}
