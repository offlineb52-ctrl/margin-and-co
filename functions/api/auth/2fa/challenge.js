/**
 * The second-factor challenge, shown between a verified email link and a
 * usable session.
 *
 * Reachable only with a PENDING session — one that proved the link but not
 * the code. A visitor with no session, or with a full one, is sent away.
 */

import { page, redirect, esc } from "../../../_lib/page.js";
import {
  readSession, promoteSession, getMember, upsertMember, rateLimit,
} from "../../../_lib/auth.js";
import { verifyCode, consumeRecoveryCode } from "../../../_lib/totp.js";

function form({ error = "", status = 200 } = {}) {
  return page({
    title: "Two-factor",
    eyebrow: "Sign in",
    heading: "Enter your authenticator code.",
    status,
    body: `
      ${error ? `<div class="note note--flag"><p>${esc(error)}</p></div>` : ""}
      <p>Your email link checked out. Now the six-digit code from your
         authenticator app.</p>

      <form class="subscribe" method="post" action="/api/auth/2fa/challenge">
        <div class="subscribe__row">
          <label class="subscribe__label" for="code">Six-digit code</label>
          <input class="subscribe__input" type="text" id="code" name="code"
                 inputmode="numeric" autocomplete="one-time-code"
                 pattern="[0-9]*" maxlength="6" required
                 placeholder="123456" autofocus>
          <button class="btn btn--solid" type="submit">Verify</button>
        </div>
      </form>

      <div class="prose">
        <h2>Lost your authenticator?</h2>
        <p>Use one of the recovery codes you saved when you turned this on.
           Each works once.</p>
        <form class="subscribe" method="post" action="/api/auth/2fa/challenge">
          <div class="subscribe__row">
            <label class="subscribe__label" for="recovery">Recovery code</label>
            <input class="subscribe__input" type="text" id="recovery"
                   name="recovery" maxlength="20" placeholder="abcde-fghij"
                   autocomplete="off">
            <button class="btn" type="submit">Use recovery code</button>
          </div>
        </form>
        <p>If you have lost both, email
           <a href="mailto:hello@marginco.co.uk">hello@marginco.co.uk</a>.
           There is no automated way round this, which is the point.</p>
      </div>`,
  });
}

export async function onRequestGet({ request, env }) {
  const session = env.AUTH ? await readSession(env, request) : null;
  if (!session) return redirect("/login/");
  if (!session.pending_2fa) return redirect("/members/");
  return form();
}

export async function onRequestPost({ request, env }) {
  const session = env.AUTH ? await readSession(env, request) : null;
  if (!session) return redirect("/login/");
  if (!session.pending_2fa) return redirect("/members/");

  const email = session.email;

  // Brute force is the whole threat model for a six-digit code: a million
  // possibilities is nothing without a limit. Ten attempts per ten minutes
  // makes exhaustive search take years.
  if (!await rateLimit(env, `2fa:${email}`, 10, 10 * 60)) {
    return form({
      error: "Too many attempts. Wait ten minutes and try again.",
      status: 429,
    });
  }

  let body;
  try {
    body = await request.formData();
  } catch {
    return form({ error: "Could not read that. Try again.", status: 400 });
  }

  const member = await getMember(env, email);
  if (!member?.totp_secret) {
    // 2FA was switched off elsewhere while this login was in flight.
    await promoteSession(env, request);
    return redirect("/members/");
  }

  const recovery = String(body.get("recovery") || "").trim();
  if (recovery) {
    const remaining = await consumeRecoveryCode(recovery, member.recovery_codes);
    if (!remaining) {
      return form({ error: "That recovery code is not valid.", status: 401 });
    }
    await upsertMember(env, email, { recovery_codes: remaining });
    await promoteSession(env, request);
    return redirect("/members/security/?recovery_used=1");
  }

  const counter = await verifyCode(
    member.totp_secret, body.get("code"), member.totp_last_counter ?? null
  );
  if (counter === null) {
    return form({
      error: "That code is not right, or has already been used.",
      status: 401,
    });
  }

  // Record the counter so the same code cannot be replayed inside its window.
  await upsertMember(env, email, { totp_last_counter: counter });
  await promoteSession(env, request);
  return redirect("/members/");
}
