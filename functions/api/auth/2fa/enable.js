/** POST /api/auth/2fa/enable — verify a code, then activate. */

import { redirect, page, esc } from "../../../_lib/page.js";
import { readSession, getMember, upsertMember, rateLimit } from "../../../_lib/auth.js";
import { verifyCode, generateRecoveryCodes } from "../../../_lib/totp.js";

export async function onRequestPost({ request, env }) {
  const session = env.AUTH ? await readSession(env, request) : null;
  if (!session || session.pending_2fa) return redirect("/login/");

  const email = session.email;
  const member = await getMember(env, email);
  if (!member?.totp_pending) return redirect("/members/security/");

  if (!await rateLimit(env, `2fasetup:${email}`, 10, 10 * 60)) {
    return page({
      title: "Slow down", eyebrow: "Security", heading: "Too many attempts.",
      signedIn: true, status: 429,
      body: "<p>Wait ten minutes and try again.</p>",
    });
  }

  const form = await request.formData().catch(() => null);
  const counter = await verifyCode(member.totp_pending, form?.get("code"), null);

  if (counter === null) {
    return page({
      title: "Code not accepted", eyebrow: "Security",
      heading: "That code wasn't right.", signedIn: true, status: 400,
      body: `<p>Two-factor has <strong>not</strong> been switched on, so
             nothing is locked. Check your app's clock is correct and try
             again.</p>
             <p class="btn-row"><a class="btn btn--solid"
                href="/members/security/">Back to setup</a></p>`,
    });
  }

  const { plain, hashes } = await generateRecoveryCodes();

  await upsertMember(env, email, {
    totp_secret: member.totp_pending,
    totp_pending: null,
    totp_last_counter: counter,
    recovery_codes: hashes,
    // Held only long enough to show once, then cleared by the security page.
    recovery_plain: plain,
    totp_enabled_at: new Date().toISOString(),
  });

  return redirect("/members/security/?enabled=1");
}

export async function onRequestGet() {
  return redirect("/members/security/");
}
