/**
 * POST /api/auth/request — ask for a sign-in link.
 *
 * THE IMPORTANT BEHAVIOUR: this endpoint gives the same answer whether or not
 * the address exists. Telling a stranger "no account with that email" turns
 * the sign-in form into a tool for discovering who has an account here, which
 * is a privacy leak and the first step of a targeted attack. Members and
 * non-members alike are told to check their inbox.
 */

import { page, redirect, esc } from "../../_lib/page.js";
import {
  normaliseEmail, createLoginToken, rateLimit, upsertMember, getMember,
} from "../../_lib/auth.js";

// Configurable, because which address you can send from depends on what you
// verified in Resend. Verifying the root domain lets you send as
// hello@marginco.co.uk; verifying a subdomain (which avoids merging SPF
// records) means sending as something like hello@send.marginco.co.uk instead.
// Set MAIL_FROM in Pages settings if the default is not what you verified.
const DEFAULT_SENDER = "Margin & Co. <hello@marginco.co.uk>";

/** Send the link. Returns true if the provider accepted it. */
async function sendLoginEmail(env, email, link, isNew) {
  if (!env.RESEND_API_KEY) return { ok: false, reason: "not_configured" };

  const subject = isNew
    ? "Confirm your Margin & Co. account"
    : "Your Margin & Co. sign-in link";

  const text = [
    isNew ? "Welcome to Margin & Co." : "Here is your sign-in link.",
    "",
    link,
    "",
    "This link works once and expires in 15 minutes.",
    "If you did not request it, ignore this email — no account was created or",
    "changed, and nobody can sign in without the link above.",
    "",
    "Margin & Co. — marginco.co.uk",
    "Research, not investment advice.",
  ].join("\n");

  try {
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.RESEND_API_KEY}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        from: env.MAIL_FROM || DEFAULT_SENDER,
        to: [email], subject, text,
      }),
    });
    return { ok: response.ok, reason: response.ok ? null : `http_${response.status}` };
  } catch (err) {
    return { ok: false, reason: "network" };
  }
}

function checkYourInbox(email) {
  return page({
    title: "Check your inbox",
    eyebrow: "Sign in",
    heading: "Check your inbox.",
    body: `<p>If <strong>${esc(email)}</strong> is a valid address, a sign-in
             link is on its way. It works once and expires in 15 minutes.</p>
           <p>No password is involved — there isn't one to forget, reuse, or
              have stolen. If the email doesn't arrive within a minute or two,
              check your spam folder, then
              <a href="/login/">request another link</a>.</p>`,
    actions: '<p class="btn-row"><a class="btn" href="/">Back to the research</a></p>',
  });
}

export async function onRequestGet() {
  return redirect("/login/");
}

export async function onRequestPost({ request, env }) {
  let form;
  try {
    form = await request.formData();
  } catch {
    return page({
      title: "Error", heading: "Something went wrong.",
      body: "<p>The form could not be read. Please try again.</p>", status: 400,
    });
  }

  // Honeypot, same as the newsletter form.
  if ((form.get("website") || "").trim() !== "") {
    return checkYourInbox("your address");
  }

  const email = normaliseEmail(form.get("email"));
  if (!email) {
    return page({
      title: "Check that address", eyebrow: "Sign in",
      heading: "That doesn't look like an email address.",
      body: "<p>Have another go — nothing has been sent.</p>", status: 400,
    });
  }

  if (!env.AUTH) {
    return page({
      title: "Unavailable", eyebrow: "Sign in",
      heading: "Accounts aren't available right now.",
      body: `<p>This is a configuration problem at my end, not yours. Email
             <a href="mailto:hello@marginco.co.uk">hello@marginco.co.uk</a>.</p>`,
      status: 503,
    });
  }

  // Two limits: one stops an address being mailed repeatedly, the other stops
  // one source enumerating many addresses.
  const perAddress = await rateLimit(env, `login:${email}`, 5, 15 * 60);
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const perSource = await rateLimit(env, `loginip:${ip}`, 20, 15 * 60);

  if (!perAddress || !perSource) {
    // Still the neutral answer: a rate-limit message would confirm the address
    // had been tried before.
    return checkYourInbox(email);
  }

  const existing = await getMember(env, email);
  await upsertMember(env, email, {});

  const token = await createLoginToken(env, email);
  const link = `${new URL(request.url).origin}/api/auth/verify?token=${token}`;

  const sent = await sendLoginEmail(env, email, link, !existing);

  if (!sent.ok && sent.reason === "not_configured") {
    return page({
      title: "Email not configured", eyebrow: "Sign in",
      heading: "Sign-in email isn't switched on yet.",
      body: `<p>The account was created, but no email provider is configured,
             so the link could not be sent. This is a setup step the site owner
             still has to complete — see <code>DEPLOY.md</code>.</p>`,
      status: 503,
    });
  }

  return checkYourInbox(email);
}
