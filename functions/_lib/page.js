/**
 * HTML responses for Functions.
 *
 * These pages are rendered by Workers rather than by the static site build,
 * so they cannot use the build's templates. They deliberately reuse the same
 * stylesheet and markup classes, so a member never sees a page that looks
 * like it belongs to a different site.
 *
 * `/css/site.css` is referenced rather than the content-hashed filename,
 * because a Worker cannot know the hash the build produced. That file is
 * published with a short cache lifetime for exactly this reason.
 */

/** Escape anything interpolated into HTML. Applied without exception. */
export function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

const SECURITY_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  // Authenticated pages must never be stored by a shared cache, and never
  // resurrected by a back button after sign-out.
  "cache-control": "no-store, no-cache, must-revalidate, private",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-frame-options": "DENY",
  "content-security-policy":
    "default-src 'self'; script-src 'none'; "
    + "style-src 'self' https://fonts.googleapis.com; "
    + "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
    + "object-src 'none'; base-uri 'self'; form-action 'self'; "
    + "frame-ancestors 'none'",
};

export function nav(signedIn = false) {
  return `
    <nav class="site-nav" aria-label="Primary">
      <a href="/">Latest</a>
      <a href="/live/">Live</a>
      <a href="/reports/">Archive</a>
      <a href="/methodology/">Methodology</a>
      ${signedIn
        ? '<a href="/members/">Members</a>'
        : '<a href="/join/">Join</a>'}
    </nav>`;
}

export function page({
  title, heading, eyebrow = "", body, status = 200,
  headers = {}, signedIn = false, actions = "",
}) {
  const html = `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} — Margin &amp; Co.</title>
<meta name="robots" content="noindex, nofollow">
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
    ${nav(signedIn)}
  </div>
</header>
<main class="wrap hero">
  ${eyebrow ? `<p class="eyebrow eyebrow--accent">${esc(eyebrow)}</p>` : ""}
  <h1>${esc(heading)}</h1>
  <div class="prose">${body}</div>
  ${actions}
</main>
<footer class="site-footer">
  <div class="wrap site-footer__base">
    <span>&copy; ${new Date().getFullYear()} Margin &amp; Co. Research, not investment advice.
      &middot; <a href="/terms/">Terms</a> &middot; <a href="/privacy/">Privacy</a></span>
  </div>
</footer>
</body>
</html>`;

  return new Response(html, {
    status,
    headers: { ...SECURITY_HEADERS, ...headers },
  });
}

export function redirect(location, headers = {}) {
  return new Response(null, {
    status: 303,
    headers: { location, "cache-control": "no-store", ...headers },
  });
}
