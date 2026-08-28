/**
 * /unsubscribe?t=… — leave the mailing list, in one click.
 *
 * The privacy policy promises that withdrawing consent is "as easy as giving
 * it", and giving it is one form field. Telling someone to compose an email
 * to a human instead is not that, and since 2024 the large mailbox providers
 * will not accept bulk mail from a sender that does not offer one-click
 * unsubscribe. So this exists before the first newsletter goes out, not after
 * the first complaint.
 *
 * The link carries an opaque random token, not an email address:
 *
 *   - An address in the URL would leak into browser history, referrer
 *     headers, and any proxy log between the reader and here.
 *   - A guessable link would let anyone unsubscribe anyone. The token is 32
 *     random bytes, and the only way to hold one is to have been sent it.
 *
 * The record is deleted, not flagged inactive, because that is what the
 * privacy policy says happens.
 *
 * GET shows a confirmation page. POST is the RFC 8058 one-click path that
 * mail clients call themselves; both do the same thing, because a reader who
 * clicked "unsubscribe" has already made their decision and should not be
 * asked to confirm it on a second screen.
 */

import { page } from "./_lib/page.js";

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

async function removeByToken(env, token) {
  if (!env.SUBSCRIBERS || !TOKEN_PATTERN.test(token || "")) return null;

  const email = await env.SUBSCRIBERS.get(`unsub:${token}`);
  if (!email) return null;

  // Delete the subscription first: if the second delete fails, the person is
  // unsubscribed with a stale token left over, which is harmless. The other
  // order would leave them subscribed with no way back out.
  await env.SUBSCRIBERS.delete(`sub:${email}`);
  await env.SUBSCRIBERS.delete(`unsub:${token}`);
  return email;
}

function done() {
  return page({
    title: "Unsubscribed",
    eyebrow: "Mailing list",
    heading: "You're off the list.",
    robots: "noindex, nofollow",
    body: `<p>Your address has been deleted, not flagged as inactive. There is
              no record of it left, so there is nothing to reactivate and
              nothing to leak.</p>
           <p>Every report stays free to read on the site, and nothing here
              sits behind the mailing list.</p>`,
    actions: `<p class="btn-row">
                <a class="btn btn--solid" href="/reports/">Read the archive</a>
                <a class="btn" href="/">Home</a>
              </p>`,
  });
}

function notFound() {
  return page({
    title: "Link not recognised",
    eyebrow: "Mailing list",
    heading: "That unsubscribe link is no longer valid.",
    robots: "noindex, nofollow",
    status: 404,
    body: `<p>The most likely reason is that it has already been used, in
              which case you are not on the list and nothing more is needed.</p>
           <p>If you are still receiving the report, email
              <a href="mailto:hello@marginco.co.uk">hello@marginco.co.uk</a>
              and it will be dealt with by hand.</p>`,
  });
}

export async function onRequestGet({ request, env }) {
  const token = new URL(request.url).searchParams.get("t");
  return (await removeByToken(env, token)) ? done() : notFound();
}

/** RFC 8058 one-click. Mail clients POST here without a human seeing a page. */
export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  let token = url.searchParams.get("t");

  if (!token) {
    try {
      const form = await request.formData();
      token = form.get("t");
    } catch {
      token = null;
    }
  }

  const email = await removeByToken(env, token);
  // A mail client wants a status code, not a page.
  return new Response(email ? "Unsubscribed\n" : "Not found\n", {
    status: email ? 200 : 404,
    headers: { "content-type": "text/plain; charset=utf-8",
               "cache-control": "no-store" },
  });
}
