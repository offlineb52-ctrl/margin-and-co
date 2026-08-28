"""
Remove test member records from the live account store.

    python cleanup_test_accounts.py              # dry run: list, delete nothing
    python cleanup_test_accounts.py --delete     # actually delete

DRY RUN IS THE DEFAULT. A KV delete is immediate and permanent -- there is no
trash to recover from -- and these records sit in the same namespace as real
members' accounts. So the safe path is the one you get by accident.

HOW A RECORD IS JUDGED A TEST
-----------------------------
Only addresses in the domains RFC 2606 reserves for exactly this purpose
(example.com, example.org, example.net, and .invalid/.test), plus the
`flowtest-*` and `probe-*` prefixes on this project's own domain, which were
created by the sign-in flow tests on 25 August.

Nothing else is ever considered, however much it looks like a test. A real
member losing their account because their address happened to contain the
word "test" is a far worse outcome than a few junk records surviving, so the
rule is an allowlist of patterns that cannot occur naturally, not a guess.

Every address that does NOT match is printed too, so you can see what is being
kept and check that nothing real is about to go.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent

# The marginco-auth namespace.
AUTH_KV_ID = "872d019c083c46a4b916076271dfddd6"

# Domains reserved by RFC 2606 and RFC 6761 so that they can never belong to
# a real person. An address here cannot receive mail and cannot be a member.
RESERVED_DOMAINS = ("example.com", "example.org", "example.net",
                    "invalid", "test", "localhost")

# Prefixes used by this project's own sign-in flow tests on its own domain.
TEST_PREFIXES = ("flowtest-", "probe-", "deploycheck-")
OWN_DOMAIN = "@marginco.co.uk"


def is_test_address(email: str) -> bool:
    """True only for addresses that cannot belong to a real person.

    The domain is compared exactly, never as a substring. A substring test
    would treat real@example.com.attacker.net as reserved, because the string
    "example.com" appears inside it -- and deleting a real account on the
    strength of a lookalike domain is precisely the mistake worth engineering
    out of a tool whose whole job is irreversible deletion.
    """
    lowered = email.strip().lower()
    if lowered.count("@") != 1:
        return False
    local, domain = lowered.split("@", 1)
    # A malformed address cannot be reasoned about, so it is kept. A leftover
    # junk record costs nothing; a wrongly deleted account cannot be undone.
    if not local or not domain:
        return False

    # Exact match, or a subdomain of a reserved name.
    for reserved in RESERVED_DOMAINS:
        if domain == reserved or domain.endswith("." + reserved):
            return True

    if domain == OWN_DOMAIN.lstrip("@"):
        return any(local.startswith(p) for p in TEST_PREFIXES)
    return False


def wrangler(args: List[str]) -> str:
    proc = subprocess.run(["npx", "--yes", "wrangler@latest", *args],
                          cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def list_members() -> List[str]:
    raw = wrangler(["kv", "key", "list", "--namespace-id", AUTH_KV_ID, "--remote"])
    start = raw.find("[")
    if start < 0:
        return []
    return [k["name"] for k in json.loads(raw[start:])
            if k.get("name", "").startswith("member:")]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Delete test member records")
    parser.add_argument("--delete", action="store_true",
                        help="actually delete. Without this nothing changes.")
    args = parser.parse_args(argv)

    try:
        keys = list_members()
    except RuntimeError as exc:
        print(f"Could not read the account store.\n{exc}", file=sys.stderr)
        print("\nRun `npx wrangler login` once, then try again.", file=sys.stderr)
        return 1

    doomed: List[Tuple[str, str]] = []
    kept: List[str] = []
    for key in keys:
        email = key[len("member:"):]
        (doomed.append((key, email)) if is_test_address(email)
         else kept.append(email))

    print(f"{len(keys)} member record(s) in the store.\n")

    print(f"KEEPING {len(kept)} real account(s):")
    for email in sorted(kept):
        print(f"  {email}")

    print(f"\nDELETING {len(doomed)} test record(s):")
    for _, email in sorted(doomed, key=lambda x: x[1]):
        print(f"  {email}")

    if not doomed:
        print("\nNothing to do.")
        return 0

    if not args.delete:
        print("\nDRY RUN. Nothing was deleted.")
        print("Check the KEEPING list above is right, then re-run with --delete.")
        return 0

    print()
    failures = []
    for key, email in doomed:
        try:
            wrangler(["kv", "key", "delete", "--namespace-id", AUTH_KV_ID,
                      "--remote", key])
            print(f"  deleted {email}")
        except RuntimeError as exc:
            failures.append((email, str(exc)))
            print(f"  FAILED  {email}: {exc}", file=sys.stderr)

    print(f"\nDeleted {len(doomed) - len(failures)} of {len(doomed)}.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
