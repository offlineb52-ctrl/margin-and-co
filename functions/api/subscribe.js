/**
 * Email list signup — a Cloudflare Pages Function.
 *
 * DESIGN CONSTRAINTS, AND WHY
 * ---------------------------
 * 1. **No JavaScript on the page.** This is a plain HTML form POST, and the
 *    response is a full HTML page. That keeps the site's Content Security
 *    Policy at `script-src 'none'`, which makes cross-site scripting
 *    structurally impossible rather than merely unlikely. A JS-driven signup
 *    would have meant relaxing that, and the trade was not worth it.
 *
 * 2. **No third party holds the list.** Addresses go into Cloudflare KV on
 *    the same account that already serves the site. Nothing is shared with a
 *    mailing provider, which is exactly what the privacy policy promises.
 *
 * 3. **Store the minimum.** Email, timestamp, and a record of consent. No IP
 *    address, no user agent, no name, no fingerprint. Data you never collect
 *    is data you can never leak, and it is one fewer thing to disclose.
 *
 * 4. **Fail closed and quietly.** Bots get a success page; they should learn
 *    nothing from the response. Genuine errors say so plainly.
 */

const MAX_EMAIL_LENGTH = 254;               // RFC 5321
const EMAIL_PATTERN = /^[^\s@,;:<>()[\]\\]+@[^\s@.]+(\.[^\s@.]+)+$/;

/** Minimal HTML escape for anything echoed back into the page. */
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** A response page that matches the rest of the site. */
function page({ title, heading, body, status = 200 }) {
  const html = `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} — Margin &amp; Co.</title>
<meta name="robots" content="noindex">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Inter:wght@400;500;600&display=swap">
<link rel="stylesheet" href="/css/site.css">
</head>
<body>
<header class="site-header">
  <div class="wrap site-header__inner">
    <a class="wordmark" href="/">Margin <span>&amp;</span> Co.</a>
    <nav class="site-nav" aria-label="Primary">
      <a href="/">Latest</a>
      <a href="/live/">Live</a>
      <a href="/reports/">Archive</a>
      <a href="/methodology/">Methodology</a>
      <a href="/about/">About</a>
    </nav>
  </div>
</header>
<main class="wrap hero">
  <p class="eyebrow eyebrow--accent">Email list</p>
  <h1>${esc(heading)}</h1>
  <div class="prose">${body}</div>
  <p class="btn-row"><a class="btn" href="/">Back to the research</a></p>
</main>
</body>
</html>`;

  return new Response(html, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "strict-origin-when-cross-origin",
      "x-frame-options": "DENY",
    },
  });
}

export async function onRequestGet() {
  // The form posts. A bare GET is someone poking at the endpoint.
  return Response.redirect("https://marginco.co.uk/#subscribe", 303);
}

export async function onRequestPost({ request, env }) {
  let form;
  try {
    form = await request.formData();
  } catch {
    return page({
      title: "Couldn't read that",
      heading: "Something went wrong.",
      body: "<p>The form could not be read. Please try again.</p>",
      status: 400,
    });
  }

  // Honeypot: a field hidden from humans by CSS. Anything that fills it in is
  // automated. Return the ordinary success page so the bot learns nothing.
  if ((form.get("website") || "").trim() !== "") {
    return page({
      title: "Subscribed",
      heading: "You're on the list.",
      body: "<p>Check your inbox for the next report.</p>",
    });
  }

  const email = (form.get("email") || "").trim().toLowerCase();
  const consent = form.get("consent");

  if (!email || email.length > MAX_EMAIL_LENGTH || !EMAIL_PATTERN.test(email)) {
    return page({
      title: "Check that address",
      heading: "That doesn't look like an email address.",
      body: "<p>Have another go — nothing has been saved.</p>",
      status: 400,
    });
  }

  if (!consent) {
    return page({
      title: "Consent needed",
      heading: "I need your permission first.",
      body: "<p>UK data protection law requires explicit consent before I can "
          + "store your address. Please tick the box and submit again.</p>",
      status: 400,
    });
  }

  if (!env.SUBSCRIBERS) {
    // The KV binding is missing. Say so honestly rather than pretending to
    // have saved an address that went nowhere.
    return page({
      title: "Signup unavailable",
      heading: "The list isn't accepting signups right now.",
      body: "<p>This is a configuration problem at my end, not yours. Email "
          + "<a href=\"mailto:hello@marginco.co.uk\">hello@marginco.co.uk</a> "
          + "and I'll add you manually.</p>",
      status: 503,
    });
  }

  const existing = await env.SUBSCRIBERS.get(`sub:${email}`);
  if (existing) {
    return page({
      title: "Already subscribed",
      heading: "You're already on the list.",
      body: "<p>No need to sign up twice — the next report will reach you.</p>",
    });
  }

  await env.SUBSCRIBERS.put(`sub:${email}`, JSON.stringify({
    email,
    subscribed_at: new Date().toISOString(),
    consent: true,
    consent_text: "Weekly research report only. No sharing, no advertising.",
    source: "marginco.co.uk",
  }));

  return page({
    title: "Subscribed",
    heading: "You're on the list.",
    body: "<p>You'll get the weekly report — which indicators survived, which "
        + "didn't, and how the live paper portfolio did.</p>"
        + "<p>No advertising, and your address is never shared. To leave, email "
        + "<a href=\"mailto:hello@marginco.co.uk\">hello@marginco.co.uk</a> and "
        + "the record is deleted, not just deactivated.</p>",
  });
}
