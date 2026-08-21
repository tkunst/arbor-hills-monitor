"""Guardrail: config.yml is committed to a PUBLIC repo, so any email address in
it is a public statement of who the monitor is associated with (see the
2026-08-21 Conservancy/Drinan cleanup, commit 546a5d8). This fails loudly if a
new address shows up that isn't on the explicit allow-list below, so a future
edit can't silently re-introduce the same leak. Add private recipients instead
via ALERT_RECIPIENTS_EXTRA (alert_recipients), GFL_AIR_WATCH_RECIPIENTS_EXTRA
(gfl_air.watch_alert_recipients), or DIGEST_RECIPIENTS_EXTRA (digest-only) --
see email_alerts.merge_extra_recipients()."""
import os
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Every address intentionally committed to config.yml, and why it's safe to be
# public. Anything else found in config.yml fails this test.
ALLOWED_EMAILS_IN_CONFIG = {
    "arbor-hills@trishakunst.com",  # Trisha's own catch-all -- the monitor owner
    "hwangr@umich.edu",             # Roland Hwang -- sitting elected official, already a named public ally
}

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yml"
)


def _emails_in_config() -> set[str]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return set(EMAIL_RE.findall(f.read()))


def test_config_yml_has_no_unapproved_email_addresses():
    unexpected = _emails_in_config() - ALLOWED_EMAILS_IN_CONFIG
    assert not unexpected, (
        f"New email address(es) found committed to config.yml (a PUBLIC repo "
        f"file): {sorted(unexpected)}. Do not commit a recipient address here -- "
        f"add it privately instead via ALERT_RECIPIENTS_EXTRA (alert_recipients), "
        f"GFL_AIR_WATCH_RECIPIENTS_EXTRA (gfl_air.watch_alert_recipients), or "
        f"DIGEST_RECIPIENTS_EXTRA (digest-only) -- see "
        f"email_alerts.merge_extra_recipients(). If this address is genuinely "
        f"meant to be public (e.g. a sitting elected official already disclosed "
        f"as an ally), add it to ALLOWED_EMAILS_IN_CONFIG in this test with a "
        f"one-line reason."
    )


def test_allowed_emails_list_is_not_stale():
    # Catches drift the other way: an allow-listed address no longer actually in
    # config.yml (e.g. removed in a later privacy pass) should be pruned too, so
    # it doesn't silently mask a real future regression on the same address.
    stale = ALLOWED_EMAILS_IN_CONFIG - _emails_in_config()
    assert not stale, (
        f"ALLOWED_EMAILS_IN_CONFIG lists address(es) no longer present in "
        f"config.yml: {sorted(stale)}. Prune them from the allow-list."
    )
