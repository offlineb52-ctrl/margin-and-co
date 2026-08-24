# Deploying Margin & Co.

> **Live as of 24 August 2026.** The site is at
> [marginco.co.uk](https://marginco.co.uk), hosted on Cloudflare Pages
> (project `marginco`), built automatically from `main` in this repository.
> The domain is registered at GoDaddy with nameservers pointed at Cloudflare.
>
> Pushing to `main` rebuilds and redeploys the site. Nothing below needs
> repeating unless you are moving to a different domain or host — it is kept
> as a record of how it was set up.

## Original guide: deploying to your own domain

The site in `site/dist/` is plain static files — HTML, CSS and PNGs, no server
and no build toolchain. Any static host will serve it. This guide covers the
two worth using.

---

## Step 0 — set your domain (do this first)

Open `site/siteconfig.py` and change one line:

```python
DOMAIN = "https://yourdomain.com"      # no trailing slash
```

Everything else derives from it: canonical URLs, the sitemap, the Open Graph
tags that generate LinkedIn link previews, and the `CNAME` file. There is no
second place to update.

While you are there, also set `REPO_URL` and `AUTHOR`.

Then rebuild:

```bash
python site/build.py
```

---

## Buying the domain

Any registrar works. A few notes specific to this project:

- **`.com` reads as most credible** for something presented as a research
  publication. `.co.uk` is a reasonable second if the audience is UK-focused.
- Registrars charge wildly different renewal prices. Cloudflare Registrar sells
  at cost with no markup, which is usually the cheapest option over several
  years, and it puts the domain in the same place as the hosting below.
- Avoid a registrar that charges extra for WHOIS privacy — it should be free.

Expect roughly £8–12 a year for a `.com`.

---

## Connecting a GoDaddy domain (marginco.co.uk)

### Do NOT use "Connect domain" or "Forward To Any Site"

GoDaddy's *Connect domain* screen lists Wix, Squarespace, WordPress and friends,
with a "Forward To Any Site" box underneath. None of that applies here, and the
forwarding option in particular should be avoided:

- Forwarding serves a redirect, so visitors land on `something.pages.dev` and
  see that in the address bar, not your domain.
- Search engines treat the domain as an empty redirect, so nothing you publish
  ever ranks under your own name.
- HTTPS on the bare domain is unreliable, which browsers flag.

You want **DNS**, which is a different screen. Back out of *Connect domain*.

### The plan

The domain stays owned at GoDaddy and renews there. Only the *nameservers*
move to Cloudflare, which then answers DNS queries and hosts the site. This is
needed because the bare domain (`marginco.co.uk`, no `www`) cannot be pointed
at a host with a plain CNAME record, and GoDaddy's DNS has no way around that.
Cloudflare does, via CNAME flattening.

### Step 1 — get the site live before touching DNS

Prove the build works first, so that if something breaks later you know it is
DNS and not the site.

1. Create a free account at [dash.cloudflare.com](https://dash.cloudflare.com).
2. **Workers & Pages → Create → Pages → Upload assets**.
3. Name the project `marginco`.
4. Drag the **`site/dist`** folder into the upload box.

It goes live at `marginco.pages.dev` within a minute. Open it and click through
every page. If that works, the site is fine and everything after this is DNS.

### Step 2 — move the nameservers at GoDaddy

1. In Cloudflare: **Add a domain** → enter `marginco.co.uk` → choose the Free
   plan. Cloudflare scans existing records and then shows you **two
   nameservers**, something like `alice.ns.cloudflare.com` and
   `bob.ns.cloudflare.com`. They are specific to your account — copy the two
   you are given, not any you find in a guide.
2. In GoDaddy: **My Products → Domains → marginco.co.uk → Domain Settings**.
3. Scroll to **Nameservers** → **Change** → **I'll use my own nameservers**.
4. Delete GoDaddy's entries, paste Cloudflare's two, save.

`.co.uk` domains are managed by Nominet and usually switch within an hour,
occasionally up to 24. Cloudflare emails you when the domain is active.

### Step 3 — attach the domain to the site

Back in Cloudflare, once the domain shows **Active**:

1. **Workers & Pages → marginco → Custom domains → Set up a domain**.
2. Enter `marginco.co.uk`. Cloudflare creates the DNS record itself.
3. Repeat for `www.marginco.co.uk` so both work.

SSL is issued automatically, usually within a few minutes. When it is done,
`https://marginco.co.uk` serves the site.

### Checking it worked

```bash
dig +short marginco.co.uk
curl -sI https://marginco.co.uk | head -3
```

The `curl` should return `HTTP/2 200`. If you get a redirect to a `.pages.dev`
address, forwarding is still switched on at GoDaddy — remove it.

### Publishing updates afterwards

Direct upload means re-dragging `site/dist` after each weekly run. To make it
automatic, push the repository to GitHub and use **Workers & Pages → Settings →
Builds → Connect to Git** with the build settings in Option A below. The daily
live-portfolio workflow then republishes the site on its own.

---

## Option A — Cloudflare Pages (recommended)

Free, fast globally, free SSL, and it handles the custom domain in two clicks
if the domain is also registered with Cloudflare.

### One-time setup

1. Push this repository to GitHub.
2. Go to **Cloudflare dashboard → Workers & Pages → Create → Pages →
   Connect to Git** and pick the repository.
3. Configure the build:

   | Field | Value |
   |---|---|
   | Framework preset | None |
   | Build command | `python site/build.py` |
   | Build output directory | `site/dist` |
   | Root directory | *(leave blank)* |

4. Under **Environment variables**, add `PYTHON_VERSION` = `3.11`.
5. Deploy. You get a `*.pages.dev` URL immediately.

### Attaching the domain

**Custom domains → Set up a domain →** enter your domain. If it is registered
with Cloudflare, the DNS record is created automatically and HTTPS is live in
about a minute. If it is registered elsewhere, Cloudflare shows the CNAME
record to add at your registrar.

After that, every `git push` rebuilds and redeploys the site.

### Deploying without Git

```bash
npx wrangler pages deploy site/dist --project-name margin-and-co
```

---

## Option B — GitHub Pages

Free and simple, but you must commit the built output.

1. In `.gitignore`, make sure `site/dist/` is **not** ignored.
2. Build and commit:

   ```bash
   python site/build.py
   git add site/dist && git commit -m "Publish week 1" && git push
   ```

3. **Repository → Settings → Pages →** set the source to your branch and the
   folder to `/site/dist`. (If GitHub will not offer a nested folder, either
   build to `/docs` or push `site/dist` to a `gh-pages` branch.)
4. Under **Custom domain**, enter your domain. The build already writes the
   `CNAME` file for you.
5. At your registrar, add these DNS records:

   | Type | Name | Value |
   |---|---|---|
   | A | `@` | `185.199.108.153` |
   | A | `@` | `185.199.109.153` |
   | A | `@` | `185.199.110.153` |
   | A | `@` | `185.199.111.153` |
   | CNAME | `www` | `your-username.github.io` |

6. Wait for DNS, then tick **Enforce HTTPS**.

---

## The weekly routine

```bash
./publish.sh 2            # run the research for week 2 and rebuild the site
./publish.sh 2 --serve    # ...and preview at http://localhost:8000
```

Then read the numbers before you publish anything. If the result is
uninteresting, publish it anyway — that is the entire premise of the project.

To deploy:

```bash
git add -A && git commit -m "Week 2" && git push
```

---

## Before the first public link

- [ ] `site/siteconfig.py` — domain, author and repository URL are real
- [ ] `site/content/about.html` — replace the contact placeholder with your
      email or LinkedIn URL
- [ ] Open the site on a phone as well as a laptop
- [ ] Paste the URL into the
      [LinkedIn Post Inspector](https://www.linkedin.com/post-inspector/) to
      confirm the preview card renders — this is the first thing most readers
      will see of the project
- [ ] Check `/sitemap.xml` and `/robots.txt` load
- [ ] Submit the domain to
      [Google Search Console](https://search.google.com/search-console)

---

## Troubleshooting

**The site loads but has no styling.** `BASE_PATH` in `siteconfig.py` is wrong.
Use `"/"` for a custom domain; use `"/repo-name/"` only if you are serving from
a subdirectory.

**LinkedIn shows no preview image.** The `og:image` must be an absolute URL, so
`DOMAIN` has to be correct and the site must already be live. LinkedIn caches
aggressively — use the Post Inspector to force a refresh.

**Charts are missing after deploying.** They are copied from
`reports/output/`, so the pipeline must have been run before the site build.
`./publish.sh` does both in the right order.

**Cloudflare build fails on `python`.** Set the `PYTHON_VERSION` environment
variable to `3.11`. The build needs no third-party packages — `build.py` uses
only the standard library.

---

## Keeping the live portfolio live

The live page is only meaningful if the daily job actually runs. A book that
skips a fortnight and then catches up in one go is reconstructing history, not
recording it — and the page says so, because every session is flagged with how
it was produced.

### The recommended way: GitHub Actions

`.github/workflows/daily.yml` is already in the repository. It runs at 22:15
UTC Monday to Friday, advances the book, rebuilds the site, and commits the
day's record back to the repo. Nothing needs to be installed and your laptop
does not need to be on.

To enable it: push the repository to GitHub, open **Settings → Actions →
General**, and set workflow permissions to **Read and write**. Then trigger it
once manually from the Actions tab to confirm it works.

The commit history becomes part of the audit trail — each fill carries a git
timestamp showing when it was written, which is difficult to fake after the
fact.

### The manual way

```bash
python live/run_live.py     # advance one session
python -m live.export       # refresh the JSON and charts
python site/build.py        # rebuild
```

Missing days is recoverable: the job replays every session between the last
mark and today, in order, using the same decide-on-close, fill-at-next-open
sequence. It is not the same as having recorded them forward, though, and the
site keeps that distinction visible rather than quietly closing the gap.

### Opening a second portfolio later

Do not change the strategy inside this book. The starting capital, universe and
traded rule are frozen in `live/state/meta.json` on purpose — swapping the rule
underneath an existing equity curve makes the whole track record meaningless.
If you want to trade something else, open a second book alongside it and let
both run.

---

## The email list

Signups are handled by a Cloudflare Pages Function (`functions/api/subscribe.js`)
writing to a KV namespace bound as `SUBSCRIBERS`. No third-party mailing
provider holds the list — which is exactly what the privacy policy promises,
so if you ever move to one, update that page *before* transferring any data.

**Reading the list.** Cloudflare dashboard → Storage & databases → Workers KV →
`marginco-subscribers`. Keys are `sub:<email>`.

**What is stored.** Email, timestamp, and a consent record. Deliberately no IP
address, no user agent, no name. Data never collected cannot leak, and it is
one fewer thing to disclose in a subject access request.

**Unsubscribes.** Currently handled by email to `hello@marginco.co.uk` — delete
the matching `sub:` key. That satisfies GDPR at this scale, but once the list
is more than a few dozen people, add a one-click unsubscribe link; manual
handling stops being credible.

**Sending the emails.** Cloudflare does not send bulk mail. Export the keys and
send through a provider when you are ready. Name that provider in the privacy
policy first.

**Bot protection.** A honeypot field plus server-side validation. There is no
CAPTCHA because every option requires JavaScript, and running any would mean
relaxing `script-src 'none'` — which currently makes cross-site scripting
impossible. If spam signups ever become a real problem, add a Cloudflare rate
limiting rule on `/api/subscribe` rather than reaching for a CAPTCHA.
