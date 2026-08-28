"""
Checks on which accounts the cleanup tool will delete. Run with:

    python -m tests.test_cleanup

This is the only test here guarding an irreversible action. A KV delete cannot
be undone, and these records live beside real members' accounts, so the
classifier has exactly one job: never return True for an address a real person
could hold. Every case below is a way that could go wrong.
"""

from __future__ import annotations

from cleanup_test_accounts import is_test_address

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def test_real_accounts_are_never_deleted():
    """Ordinary address shapes, including the ones members actually use.

    These are invented. Real members' addresses do not belong in a public
    repository, whoever they are -- putting a live account address into a test
    fixture publishes it to anyone who clones this, which is the same mistake
    as printing it on the site. The shapes below cover what the classifier
    needs to see: plain addresses, dotted local parts, subdomained providers,
    and this project's own domain.
    """
    for email in [
        "a.person@gmail.com", "someone@outlook.com", "a.b.c@company.co.uk",
        "first.last@mail.example-provider.net", "hello@marginco.co.uk",
        "j.smith@universityofsomewhere.ac.uk",
    ]:
        check(f"keeps {email}", not is_test_address(email))


def test_reserved_domains_are_deleted():
    """RFC 2606 and 6761 reserve these precisely so nobody can hold one."""
    for email in [
        "brand-new-16111@example.com", "nobody-27070@example.com",
        "x@example.org", "x@example.net", "x@host.invalid", "x@my.test",
        "x@sub.example.com",
    ]:
        check(f"deletes {email}", is_test_address(email))


def test_own_domain_test_prefixes_only():
    """The prefix rule applies on this project's domain and nowhere else."""
    check("deletes flowtest-16147@marginco.co.uk",
          is_test_address("flowtest-16147@marginco.co.uk"))
    check("deletes probe-11043@marginco.co.uk",
          is_test_address("probe-11043@marginco.co.uk"))
    check("keeps a real address on the same domain",
          not is_test_address("adam@marginco.co.uk"))
    # The same prefix elsewhere belongs to a stranger, not to this project.
    check("keeps flowtest@gmail.com", not is_test_address("flowtest@gmail.com"))
    check("keeps probe@gmail.com", not is_test_address("probe@gmail.com"))


def test_lookalike_domains_are_not_deleted():
    """The failure that would cost someone their account.

    A substring match treats real@example.com.attacker.net as reserved,
    because "example.com" appears inside it. The domain must be compared
    exactly, or as a subdomain, and never as a substring.
    """
    for email in [
        "real@example.com.attacker.net", "real@notexample.com",
        "real@example.community", "real@myexample.org",
    ]:
        check(f"keeps {email}", not is_test_address(email))


def test_addresses_containing_the_word_test_are_kept():
    """"test" appears in plenty of ordinary addresses."""
    for email in [
        "test.user@gmail.com", "tester@outlook.com", "contest.winner@yahoo.com",
        "protest@gmail.com", "greatest@gmail.com",
    ]:
        check(f"keeps {email}", not is_test_address(email))


def test_malformed_addresses_are_kept():
    """When in doubt, keep. A junk record is cheaper than a deleted account."""
    for email in ["", "no-at-sign", "weird@@example.com", "@example.com  "]:
        check(f"keeps {email!r}", not is_test_address(email))


def main() -> int:
    print("Margin & Co. — cleanup classifier checks\n")
    for fn in [
        test_real_accounts_are_never_deleted,
        test_reserved_domains_are_deleted,
        test_own_domain_test_prefixes_only,
        test_lookalike_domains_are_not_deleted,
        test_addresses_containing_the_word_test_are_kept,
        test_malformed_addresses_are_kept,
    ]:
        print(f"\n{fn.__name__}:")
        fn()

    print(f"\n{'-' * 60}")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
