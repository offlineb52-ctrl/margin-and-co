"""
Send the weekly report to the mailing list.

    python send_weekly.py                  # dry run: render it, send nothing
    python send_weekly.py --open           # dry run, then open it in a browser
    python send_weekly.py --send           # actually send

DRY RUN IS THE DEFAULT AND --send IS THE ONLY WAY PAST IT. An email cannot be
recalled, the list is real people who gave consent for one specific thing, and
there is no undo. So the safe path is the one you get by accident.

WHAT THE EMAIL CONTAINS
-----------------------
A teaser and a link. Not the report.

That is deliberate. The full report is a table, two charts and several hundred
words; delivering it by email means it renders differently in every client,
cannot be corrected once sent, and drops out of the archive that gives the
project its credibility. A short summary and a link keeps one canonical copy
of every published number, on the site, where it can be checked.

WHY IT SENDS ONE MESSAGE PER PERSON
-----------------------------------
Never CC, never a shared BCC batch. One request per recipient, so no
subscriber ever learns another subscriber's address. A single mistaken CC
would disclose the whole list and be unfixable.

REQUIREMENTS
------------
  RESEND_API_KEY   in the environment, for --send only
  wrangler         logged in, to read the subscriber list from KV
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
REPORT_DIR = PROJECT_ROOT / "reports" / "output"
SITE = "https://marginco.co.uk"
MAIL_FROM = os.environ.get("MAIL_FROM", "Margin & Co. <hello@send.marginco.co.uk>")
KV_BINDING = "SUBSCRIBERS"

# The marginco-subscribers namespace. Not a secret -- an id alone grants
# nothing, and reading the list still needs a Cloudflare credential.
SUBSCRIBERS_KV_ID = "2e2397b82a0c40b9a923ee9f49b18029"

# Resend's own guidance is well under this; the pause exists so a long list
# does not arrive as a burst that looks like a spam run to the receiving side.
SEND_PAUSE_SECONDS = 0.6


# --------------------------------------------------------------------------
# Subscriber list, read from Cloudflare KV through wrangler
# --------------------------------------------------------------------------

def _wrangler(args: List[str]) -> str:
    """Run wrangler and return stdout, or raise with its stderr attached."""
    proc = subprocess.run(
        ["npx", "--yes", "wrangler@latest", *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"wrangler {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def load_subscribers(namespace_id: str) -> List[str]:
    """Every address currently on the list."""
    raw = _wrangler(["kv", "key", "list",
                     "--namespace-id", namespace_id, "--remote"])
    start = raw.find("[")
    if start < 0:
        return []
    keys = json.loads(raw[start:])
    return sorted(k["name"][len("sub:"):] for k in keys
                  if k.get("name", "").startswith("sub:"))


def ensure_unsubscribe_token(namespace_id: str, email: str,
                             dry_run: bool) -> str:
    """Return this subscriber's unsubscribe token, creating one if needed.

    Stored as `unsub:<token>` -> email, so /unsubscribe can resolve a token
    without the address ever appearing in a URL.
    """
    record_raw = _wrangler(["kv", "key", "get", f"sub:{email}",
                            "--namespace-id", namespace_id, "--remote"])
    try:
        record = json.loads(record_raw[record_raw.find("{"):])
    except (ValueError, json.JSONDecodeError):
        record = {"email": email}

    token = record.get("unsub_token")
    if token:
        return token

    token = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")
    if dry_run:
        return token       # not written; the dry run must change nothing

    record["unsub_token"] = token
    _wrangler(["kv", "key", "put", f"sub:{email}", json.dumps(record),
               "--namespace-id", namespace_id, "--remote"])
    _wrangler(["kv", "key", "put", f"unsub:{token}", email,
               "--namespace-id", namespace_id, "--remote"])
    return token


# --------------------------------------------------------------------------
# The email itself
# --------------------------------------------------------------------------

def latest_free_report() -> Dict[str, Any]:
    files = sorted(REPORT_DIR.glob("week*_free.json"))
    if not files:
        raise SystemExit("No free report found. Run ./publish.sh N first.")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def build_email(report: Dict[str, Any], unsubscribe_url: str) -> Dict[str, str]:
    week = report.get("week")
    headline = report.get("headline", "")
    review = (report.get("sections", {}) or {}).get("week_in_review") or {}
    tested = review.get("combinations_tested")
    survivors = review.get("survivors")
    url = f"{SITE}/"

    subject = f"Margin & Co. — week {week}: {headline}"

    lines = [
        f"Week {week}",
        "",
        headline,
        "",
    ]
    if tested and survivors is not None:
        lines += [f"{tested:,} indicator-and-stock combinations were tested. "
                  f"{survivors} scored 8 or above.", ""]
    if review.get("change_note"):
        lines += [review["change_note"], ""]
    lines += [
        f"Read the full report: {url}",
        "",
        "Research, not investment advice. Nothing here is a recommendation.",
        "",
        f"Unsubscribe: {unsubscribe_url}",
    ]
    text = "\n".join(lines)

    def esc(value: Any) -> str:
        return (str(value).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    html = f"""<!doctype html>
<html lang="en-GB"><body style="margin:0;padding:24px;background:#faf9f7;
  font-family:Georgia,'Times New Roman',serif;color:#1a1a1a;line-height:1.6;">
  <div style="max-width:560px;margin:0 auto;">
    <p style="font-family:Helvetica,Arial,sans-serif;font-size:12px;
       letter-spacing:.08em;text-transform:uppercase;color:#6b6b6b;
       margin:0 0 24px;">Margin &amp; Co. &middot; Week {esc(week)}</p>

    <h1 style="font-size:22px;line-height:1.3;margin:0 0 16px;font-weight:600;"
      >{esc(headline)}</h1>

    {f'<p style="margin:0 0 16px;">{tested:,} indicator-and-stock combinations were tested. {esc(survivors)} scored 8 or above.</p>' if tested and survivors is not None else ''}

    {f'<p style="margin:0 0 24px;color:#4a4a4a;">{esc(review.get("change_note", ""))}</p>' if review.get("change_note") else ''}

    <p style="margin:0 0 28px;">
      <a href="{url}" style="display:inline-block;background:#0B4F9C;color:#fff;
         text-decoration:none;padding:12px 22px;font-family:Helvetica,Arial,sans-serif;
         font-size:15px;">Read the full report</a>
    </p>

    <p style="font-family:Helvetica,Arial,sans-serif;font-size:12px;
       color:#6b6b6b;border-top:1px solid #e0ddd8;padding-top:16px;margin:0;">
      Research, not investment advice. Nothing here is a recommendation.<br>
      <a href="{unsubscribe_url}" style="color:#6b6b6b;">Unsubscribe</a>
    </p>
  </div>
</body></html>"""

    return {"subject": subject, "text": text, "html": html}


def send_one(api_key: str, to: str, mail: Dict[str, str],
             unsubscribe_url: str) -> None:
    payload = {
        "from": MAIL_FROM,
        "to": [to],
        "subject": mail["subject"],
        "html": mail["html"],
        "text": mail["text"],
        # RFC 8058. Without both headers the large providers treat bulk mail
        # as lacking one-click unsubscribe and filter it accordingly.
        "headers": {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Resend returned {response.status}")


# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Send the weekly report")
    parser.add_argument("--send", action="store_true",
                        help="actually send. Without this nothing leaves.")
    parser.add_argument("--open", action="store_true",
                        help="open the rendered email in a browser")
    parser.add_argument("--namespace-id",
                        default=os.environ.get("SUBSCRIBERS_KV_ID",
                                               SUBSCRIBERS_KV_ID),
                        help="the SUBSCRIBERS KV namespace id")
    parser.add_argument("--limit", type=int, default=None,
                        help="send to at most N addresses, for a test run")
    args = parser.parse_args(argv)

    report = latest_free_report()
    week = report.get("week")

    preview_url = f"{SITE}/unsubscribe?t=EXAMPLE-TOKEN"
    mail = build_email(report, preview_url)

    preview = REPORT_DIR / f"week{week:02d}_email.html"
    preview.write_text(mail["html"], encoding="utf-8")

    print(f"Week {week}: {report.get('headline', '')}")
    print(f"Subject: {mail['subject']}")
    print(f"Preview: {preview}")

    if args.open:
        webbrowser.open(preview.as_uri())

    if not args.namespace_id:
        print("\nNo --namespace-id given, so the subscriber list was not read.")
        print("Find it under Pages -> Settings -> Bindings, or run:")
        print("  npx wrangler kv namespace list")
        print("\nDRY RUN. Nothing was sent.")
        return 0

    try:
        subscribers = load_subscribers(args.namespace_id)
    except RuntimeError as exc:
        print(f"\nCould not read the subscriber list.\n{exc}", file=sys.stderr)
        print("\nRun `npx wrangler login` once in a terminal, or set "
              "CLOUDFLARE_API_TOKEN, then try again.\nNothing was sent.",
              file=sys.stderr)
        return 1

    if args.limit:
        subscribers = subscribers[:args.limit]

    print(f"Recipients: {len(subscribers)}")

    if not args.send:
        print("\nDRY RUN. Nothing was sent, and no unsubscribe tokens were "
              "written.\nRe-run with --send to deliver it.")
        return 0

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("\nRESEND_API_KEY is not set, so nothing was sent.",
              file=sys.stderr)
        return 1

    if not subscribers:
        print("\nThe list is empty. Nothing to do.")
        return 0

    print(f"\nSending to {len(subscribers)} recipient(s)...")
    sent, failed = 0, []
    for i, email in enumerate(subscribers, 1):
        try:
            token = ensure_unsubscribe_token(args.namespace_id, email,
                                             dry_run=False)
            url = f"{SITE}/unsubscribe?t={token}"
            send_one(api_key, email, build_email(report, url), url)
            sent += 1
            print(f"  [{i}/{len(subscribers)}] sent")
        except (RuntimeError, urllib.error.URLError, OSError) as exc:
            failed.append((email, str(exc)))
            print(f"  [{i}/{len(subscribers)}] FAILED: {exc}", file=sys.stderr)
        time.sleep(SEND_PAUSE_SECONDS)

    print(f"\nSent {sent}. Failed {len(failed)}.")
    for email, why in failed:
        # The address is printed here because this is the operator's own
        # console and a failed delivery needs chasing by hand.
        print(f"  {email}: {why}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
