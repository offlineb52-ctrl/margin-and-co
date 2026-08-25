/**
 * POST /api/auth/2fa/disable — requires a current code.
 *
 * Being signed in is not enough. If a stolen session could switch the second
 * factor off, the second factor would only be protecting the login form.
 */

import { redirect, page } from "../../../_lib/page.js";
import { readSession, getMember, upsertMember, rateLimit } from "../../../_lib/auth.js";
import { verifyCode } from "../../../_lib/totp.js";

export async function onRequestPost({ request, env }) {
  const session = env.AUTH ? await readSession(env, request) : null;
  if (!session || session.pending_2fa) return redirect("/login/");

  const email = session.email;
  const member = await getMember(env, email);
  if (!member?.totp_secret) return redirect("/members/security/");

  if (!await rateLimit(env, `2fadisable:${email}`, 10, 10 * 60)) {
    return page({
      title: "Slow down", eyebrow: "Security", heading: "Too many attempts.",
      signedIn: true, status: 429, body: "<p>Wait ten minutes.</p>",
    });
  }

  const form = await request.formData().catch(() => null);
  const counter = await verifyCode(
    member.totp_secret, form?.get("code"), member.totp_last_counter ?? null
  );

  if (counter === null) {
    return page({
      title: "Code not accepted", eyebrow: "Security",
      heading: "That code wasn't right.", signedIn: true, status: 400,
      body: `<p>Two-factor is still on.</p>
             <p class="btn-row"><a class="btn" href="/members/security/">Back</a></p>`,
    });
  }

  await upsertMember(env, email, {
    totp_secret: null, totp_pending: null,
    recovery_codes: null, recovery_plain: null,
    totp_last_counter: null, totp_enabled_at: null,
  });

  return redirect("/members/security/");
}

export async function onRequestGet() {
  return redirect("/members/security/");
}
