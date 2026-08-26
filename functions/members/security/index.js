/**
 * /members/security — turn two-factor authentication on or off.
 *
 * Enrolment is deliberately two steps. Step one generates a secret and stores
 * it as PENDING; step two requires a working code before it becomes active.
 * Activating on generation alone would lock people out of their own account
 * the moment they mistyped the secret into their app -- the commonest way 2FA
 * goes wrong.
 */

import { page, esc } from "../../_lib/page.js";
import { getMember, upsertMember } from "../../_lib/auth.js";
import { formatSecret, otpauthUri } from "../../_lib/totp.js";
import { passwordsAvailable } from "../../_lib/password.js";

export async function onRequestGet({ request, env, data }) {
  const email = data?.member?.email || "";
  const member = env.AUTH ? await getMember(env, email) : null;
  const url = new URL(request.url);

  const enabled = Boolean(member?.totp_secret);
  const pending = member?.totp_pending;
  const justEnabled = url.searchParams.get("enabled") === "1";
  const recoveryUsed = url.searchParams.get("recovery_used") === "1";
  const codes = justEnabled && member?.recovery_plain ? member.recovery_plain : null;

  let body = "";

  if (recoveryUsed) {
    body += `<div class="note note--flag">
      <p><strong>You signed in with a recovery code.</strong> That code is now
         used up. You have ${esc(String((member?.recovery_codes || []).length))}
         left — if that number is getting low, turn two-factor off and back on
         to get a fresh set.</p></div>`;
  }

  if (codes) {
    body += `<div class="note note--flag">
      <p><strong>Save these recovery codes now. They are shown once.</strong>
         Each works a single time, and they are the only way back in if you
         lose your authenticator.</p>
      <pre>${codes.map(esc).join("\n")}</pre>
      <p>Stored hashed, so I cannot read them back to you — that is deliberate,
         and it is why this is the only time you will see them.</p></div>`;
  }

  if (enabled) {
    body += `
      <div class="stats">
        <div class="stat">
          <div class="stat__label">Two-factor</div>
          <div class="stat__value"><span class="num num--pos">On</span></div>
          <div class="stat__note">authenticator app</div>
        </div>
        <div class="stat">
          <div class="stat__label">Recovery codes left</div>
          <div class="stat__value">${esc(String((member.recovery_codes || []).length))}</div>
          <div class="stat__note">of 8</div>
        </div>
      </div>

      <div class="prose">
        <h2>Turning it off</h2>
        <p>You will need a current code — knowing the password is not enough,
           because there is no password. That is the point: someone who takes
           over your inbox still cannot remove the second factor.</p>
      </div>

      <form class="subscribe" method="post" action="/api/auth/2fa/disable">
        <div class="subscribe__row">
          <label class="subscribe__label" for="code">Six-digit code</label>
          <input class="subscribe__input" type="text" id="code" name="code"
                 inputmode="numeric" pattern="[0-9]*" maxlength="6" required
                 placeholder="123456" autocomplete="one-time-code">
          <button class="btn" type="submit">Turn off two-factor</button>
        </div>
      </form>`;
  } else if (pending) {
    const uri = otpauthUri(pending, email);
    body += `
      <div class="prose">
        <h2>Step 2 of 2 — confirm it works</h2>
        <p>Add this secret to your authenticator app, then enter the code it
           shows. Two-factor is not switched on until a code verifies, so a
           mistyped secret cannot lock you out.</p>

        <h3>Enter this in your app</h3>
        <pre>${esc(formatSecret(pending))}</pre>
        <p>Or open this link on the device with your authenticator:</p>
        <pre style="white-space:pre-wrap;word-break:break-all">${esc(uri)}</pre>

        <div class="note">
          <p><strong>Why there is no QR code.</strong> Generating one usually
             means sending the secret to an image service — handing your entire
             second factor to a third party and into their logs. Drawing it in
             the browser needs JavaScript, which this site does not ship.
             Manual entry keeps the secret on this page.</p>
        </div>
      </div>

      <form class="subscribe" method="post" action="/api/auth/2fa/enable">
        <div class="subscribe__row">
          <label class="subscribe__label" for="code">Six-digit code</label>
          <input class="subscribe__input" type="text" id="code" name="code"
                 inputmode="numeric" pattern="[0-9]*" maxlength="6" required
                 placeholder="123456" autocomplete="one-time-code" autofocus>
          <button class="btn btn--solid" type="submit">Turn on two-factor</button>
        </div>
      </form>`;
  } else {
    body += `
      <div class="prose">
        <p>Sign-in already needs access to your inbox. Two-factor adds a code
           from an app on your phone, so an attacker who takes over your email
           still cannot get in.</p>
        <p>You will need an authenticator app — 1Password, Bitwarden, Aegis,
           Google Authenticator, or the one built into iOS Passwords.</p>
      </div>

      <form class="subscribe" method="post" action="/api/auth/2fa/setup">
        <p class="btn-row">
          <button class="btn btn--solid" type="submit">Set up two-factor</button>
        </p>
      </form>`;
  }

  // ----------------------------------------------------------------
  // Password: optional, and off unless the member turns it on.
  // ----------------------------------------------------------------
  if (passwordsAvailable(env)) {
    const hasPassword = Boolean(member?.password_hash);
    const setAt = member?.password_set_at;

    body += `
      <h2>Password</h2>
      <p>Accounts here sign in with an emailed link and need no password. You
         can add one if you would rather type a password than wait for an
         email. Both then work, and the link keeps working either way — so a
         forgotten password cannot lock you out.</p>`;

    if (hasPassword) {
      body += `
        <div class="note">
          <p><strong>A password is set on this account.</strong>${setAt
            ? ` Last changed ${esc(String(setAt).slice(0, 10))}.` : ""}</p>
        </div>

        <h3>Change it</h3>
        <form class="subscribe" method="post" action="/api/auth/password/set">
          <input type="hidden" name="action" value="set">
          <div class="subscribe__row">
            <label class="subscribe__label" for="current">Current password</label>
            <input class="subscribe__input" type="password" id="current"
                   name="current" required maxlength="128"
                   autocomplete="current-password">
          </div>
          <div class="subscribe__row">
            <label class="subscribe__label" for="new-password">New password</label>
            <input class="subscribe__input" type="password" id="new-password"
                   name="password" required minlength="12" maxlength="128"
                   autocomplete="new-password">
          </div>
          <div class="subscribe__row">
            <label class="subscribe__label" for="confirm">Confirm new password</label>
            <input class="subscribe__input" type="password" id="confirm"
                   name="confirm" required minlength="12" maxlength="128"
                   autocomplete="new-password">
            <button class="btn btn--solid" type="submit">Change password</button>
          </div>
        </form>

        <h3>Remove it</h3>
        <p>Removing the password returns this account to emailed links only.
           Nothing is lost, and there is then no password on the account for
           anyone to guess.</p>
        <form class="subscribe" method="post" action="/api/auth/password/set">
          <input type="hidden" name="action" value="remove">
          <div class="subscribe__row">
            <label class="subscribe__label" for="remove-current">Current password</label>
            <input class="subscribe__input" type="password" id="remove-current"
                   name="current" required maxlength="128"
                   autocomplete="current-password">
            <button class="btn" type="submit">Remove password</button>
          </div>
        </form>`;
    } else {
      body += `
        <form class="subscribe" method="post" action="/api/auth/password/set">
          <input type="hidden" name="action" value="set">
          <div class="subscribe__row">
            <label class="subscribe__label" for="new-password">New password</label>
            <input class="subscribe__input" type="password" id="new-password"
                   name="password" required minlength="12" maxlength="128"
                   autocomplete="new-password">
          </div>
          <div class="subscribe__row">
            <label class="subscribe__label" for="confirm">Confirm password</label>
            <input class="subscribe__input" type="password" id="confirm"
                   name="confirm" required minlength="12" maxlength="128"
                   autocomplete="new-password">
            <button class="btn btn--solid" type="submit">Set a password</button>
          </div>
        </form>

        <p class="meta">At least 12 characters. Length matters far more than
           punctuation — three or four unrelated words beat a short jumble of
           symbols, and are easier to remember.</p>`;
    }

    body += `
      <div class="note">
        <p><strong>How it is stored.</strong> Never in readable form. Your
           password is combined with a secret key held outside the account
           store, then put through 25,000 rounds of PBKDF2 with a random salt
           unique to you. The round count is limited by what this site's
           hosting plan allows for a single request; the separate secret key
           is what makes a copy of the account store useless on its own.
           <a href="/privacy/">The privacy policy</a> says what is kept.</p>
      </div>`;
  }

  // Recovery codes are held in the clear only long enough to display them
  // once. Clearing them here means a refresh of this page cannot show them
  // again, and they exist nowhere afterwards except hashed.
  if (codes) {
    await upsertMember(env, email, { recovery_plain: null });
  }

  return page({
    title: "Security",
    eyebrow: "Members",
    heading: "Security",
    signedIn: true,
    body,
    actions: '<p class="btn-row"><a class="btn" href="/members/">Back to members</a></p>',
  });
}
