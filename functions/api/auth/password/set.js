/**
 * POST /api/auth/password/set — set, change, or remove the account password.
 *
 * Requires a full session: this route sits under /api/, not /members/, so the
 * middleware gate does not cover it and the check is made here explicitly. A
 * pending-2FA session is not enough — proving an email link without the second
 * factor must not be a way to set a password and walk past it next time.
 *
 * Changing an existing password requires the current one. Removing it does
 * too. Without that, anyone who found an unattended signed-in browser could
 * lock the real member out by setting a password of their own.
 */

import { page, redirect, esc } from "../../../_lib/page.js";
import {
  readSession, getMember, upsertMember,
} from "../../../_lib/auth.js";
import {
  hashPassword, verifyPassword, passwordProblem, passwordsAvailable,
} from "../../../_lib/password.js";

function result({ heading, body, status = 200, ok = false }) {
  return page({
    title: ok ? "Password updated" : "Password not updated",
    eyebrow: "Security",
    heading,
    status,
    signedIn: true,
    body,
    actions: `<p class="btn-row">
                <a class="btn btn--solid" href="/members/security/">Back to security</a>
              </p>`,
  });
}

export async function onRequestPost({ request, env }) {
  if (!env.AUTH || !passwordsAvailable(env)) {
    return result({
      heading: "Password sign-in is not switched on.",
      status: 503,
      body: "<p>This site is not currently configured for password sign-in.</p>",
    });
  }

  const session = await readSession(env, request);
  if (!session) return redirect("/login/");
  if (session.pending_2fa) return redirect("/api/auth/2fa/challenge");

  const member = await getMember(env, session.email);
  if (!member) return redirect("/login/");

  const form = await request.formData();
  const action = String(form.get("action") || "set");
  const current = String(form.get("current") || "");
  const next = String(form.get("password") || "");
  const confirm = String(form.get("confirm") || "");

  // Anyone who already has a password must prove it before changing it.
  if (member.password_hash) {
    const { valid } = await verifyPassword(env, current, member.password_hash);
    if (!valid) {
      return result({
        heading: "Your current password did not match.",
        status: 403,
        body: `<p>Changing or removing a password needs the current one. If
                  you have lost it, sign out and use an emailed link, then set
                  a new password from here.</p>`,
      });
    }
  }

  if (action === "remove") {
    await upsertMember(env, session.email, {
      password_hash: null, password_set_at: null,
    });
    return result({
      ok: true,
      heading: "Password removed.",
      body: `<p>This account is passwordless again. Sign in with an emailed
                link, exactly as before. There is now no password on this
                account for anyone to steal or guess.</p>`,
    });
  }

  if (next !== confirm) {
    return result({
      heading: "Those two passwords did not match.",
      status: 400,
      body: "<p>The password and its confirmation were different. Nothing has been changed.</p>",
    });
  }

  const problem = passwordProblem(next);
  if (problem) {
    return result({
      heading: "That password was not accepted.",
      status: 400,
      body: `<p>${esc(problem)}</p>
             <p>Nothing has been changed.</p>`,
    });
  }

  // Refusing the email address as a password: it is the other half of the
  // credential and the single most guessable choice for this specific account.
  if (next.toLowerCase().includes(session.email.split("@")[0].toLowerCase())
      && session.email.split("@")[0].length > 3) {
    return result({
      heading: "That password contains your email address.",
      status: 400,
      body: `<p>An attacker already knows the address — it is half of what
                they are trying. Please choose something unrelated to it.</p>`,
    });
  }

  await upsertMember(env, session.email, {
    password_hash: await hashPassword(env, next),
    password_set_at: new Date().toISOString(),
  });

  return result({
    ok: true,
    heading: member.password_hash ? "Password changed." : "Password set.",
    body: `<p>You can now sign in with your email address and this password,
              or carry on using an emailed link — both work, and neither
              disables the other.</p>
           ${member.totp_secret
             ? "<p>Two-factor authentication stays on: a password gets you to "
               + "the same six-digit challenge, not past it.</p>"
             : ""}`,
  });
}

export async function onRequestGet() {
  return redirect("/members/security/");
}
