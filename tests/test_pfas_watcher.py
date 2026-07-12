"""pfas_watcher.py — the activation gate, the pure diff/email helpers, and the
full baseline / unchanged / changed / fetch-fail flows driven through a fake
Sheets service (no network, no creds). The gate test is the same class of
test-first check that caught the "runs before the flag is set" gap in
wds_archiver (ADR 009 Addendum). The fetch-fail-without-baseline test pins the
loud-exit-1 activation behavior (residual risk #1 in ADR 012)."""
import re

import pytest

import pfas_watcher as pw
import pfas_client as pc
import sheet_writer as sw
from test_pfas_client import build_page


# --- gate (pure) ----------------------------------------------------------------

def test_should_run_false_when_disabled():
    ok, reason = pw._should_run({"pfas": {"enabled": False}})
    assert ok is False
    assert "pfas.enabled" in reason


def test_should_run_false_when_key_absent():
    ok, reason = pw._should_run({})
    assert ok is False
    assert "pfas.enabled" in reason


def test_should_run_true_when_enabled():
    ok, reason = pw._should_run({"pfas": {"enabled": True}})
    assert ok is True
    assert reason == ""


# --- diff / body (pure) ---------------------------------------------------------

def test_summarize_diff_reports_added_and_removed():
    note, body = pw.summarize_diff("line one\nline two", "line one\nline two changed")
    assert re.match(r"\+\d+/-\d+ lines", note)
    assert body  # non-empty unified diff
    assert "changed" in body


def test_summarize_diff_link_only_change_has_empty_body():
    # Identical visible text on both sides — a hash change driven purely by a
    # link/structure change. The note must say so and the diff body is empty.
    note, body = pw.summarize_diff("same text", "same text")
    assert note == "link/structure change (no visible-text diff)"
    assert body == ""


def test_summarize_diff_caps_length():
    old = "\n".join(f"old{i}" for i in range(200))
    new = "\n".join(f"new{i}" for i in range(200))
    _, body = pw.summarize_diff(old, new, max_lines=10)
    assert "truncated" in body
    assert len(body.splitlines()) <= 11  # 10 + the truncation marker


def test_format_change_body_includes_essentials():
    body = pw.format_change_body("My Page", "http://x", "+1/-0 lines", "+ added line")
    assert "My Page" in body and "http://x" in body
    assert "+1/-0 lines" in body and "+ added line" in body


def test_format_change_body_falls_back_when_no_diff():
    body = pw.format_change_body("My Page", "http://x", "link/structure change", "")
    assert "no line-level text diff" in body


# --- fake Sheets service --------------------------------------------------------

def _tab(rng):
    return re.match(r"'([^']+)'", rng).group(1)


def _start_row(rng):
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 1


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs  # {name: [row, ...]} with a header at index 0 once set

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(_tab(range))
        if rows is None:
            raise KeyError("no such tab")  # real API raises; _tab_rows -> []
        return _Req({"values": [list(r) for r in rows[_start_row(range) - 1:]]})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(_tab(range), []).extend(list(r) for r in body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        rows = self._tabs.setdefault(_tab(range), [])
        start = _start_row(range) - 1
        for i, row in enumerate(body["values"]):
            idx = start + i
            while len(rows) <= idx:
                rows.append([])
            rows[idx] = list(row)
        return _Req({})


class FakeSheets:
    def __init__(self):
        self._values = _Values({})

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId):
        return _Req({"sheets": [{"properties": {"title": t}} for t in self._values._tabs]})

    def batchUpdate(self, spreadsheetId, body):
        for req in body["requests"]:
            self._values._tabs.setdefault(req["addSheet"]["properties"]["title"], [])
        return _Req({})

    def values(self):
        return self._values


PAGE = build_page()
CFG = {
    "pfas": {
        "enabled": True,
        "min_content_chars": 200,
        "pages": [{"label": "Arbor Hills PFAS", "url": "http://example/arbor-hills"}],
    }
}


def _wire(monkeypatch, fetch, cfg=CFG):
    """Point run() at a fake Sheet, a canned fetch, and a captured mailer."""
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(pw, "load_config", lambda: cfg)
    monkeypatch.setattr(pw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(pw.pc, "fetch_page", fetch)
    monkeypatch.setattr(pw.ea, "send_email", lambda subj, body, c: sent.append((subj, body)))
    return fake, sent


def _data_rows(fake):
    return fake._values._tabs.get(sw.TAB_PFAS, [])[1:]  # drop the header row


def test_disabled_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, lambda url, **k: PAGE,
                       cfg={"pfas": {"enabled": False}})
    assert pw.run() == 0
    assert sent == []
    assert sw.TAB_PFAS not in fake._values._tabs  # never even created the tab


def test_first_run_baselines_silently(monkeypatch):
    fake, sent = _wire(monkeypatch, lambda url, **k: PAGE)
    rc = pw.run()
    assert rc == 0
    rows = _data_rows(fake)
    assert len(rows) == 1
    assert rows[0][3] == "baseline"       # Change column
    assert sent == []                     # baseline never alerts


def test_unchanged_second_run_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, lambda url, **k: PAGE)
    pw.run()                              # baseline
    rc = pw.run()                         # same page again
    assert rc == 0
    assert len(_data_rows(fake)) == 1     # no new row
    assert sent == []


def test_changed_page_records_row_and_emails(monkeypatch):
    pages = [PAGE, PAGE.replace("Content posted January 2021", "Content posted August 2026")]
    seq = iter(pages)
    fake, sent = _wire(monkeypatch, lambda url, **k: next(seq))
    pw.run()                              # baseline (old page)
    rc = pw.run()                         # changed page
    assert rc == 0
    rows = _data_rows(fake)
    assert len(rows) == 2
    assert rows[1][3] == "changed"
    assert len(sent) == 1
    subj, body = sent[0]
    assert "changed" in subj.lower()
    assert "August 2026" in body          # the diff shows the actual edit


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    calls = {"n": 0}

    def fetch(url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return PAGE
        raise pc.PFASFetchError("transient blip")

    fake, sent = _wire(monkeypatch, fetch)
    pw.run()                              # baseline
    rc = pw.run()                         # fetch fails, but baseline exists
    assert rc == 0                        # not loud — a blip must not fail the job
    assert len(_data_rows(fake)) == 1     # baseline preserved, not diffed
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud(monkeypatch):
    def fetch(url, **k):
        raise pc.PFASFetchError("bot wall on the runner")

    fake, sent = _wire(monkeypatch, fetch)
    rc = pw.run()
    assert rc == 1                        # activation-time block surfaces loudly
    assert _data_rows(fake) == []         # nothing baselined
    assert sent == []


def test_content_error_without_baseline_exits_loud(monkeypatch):
    # A served error page (no <main>) at activation time is also loud.
    fake, sent = _wire(monkeypatch, lambda url, **k: "<html><body>Access Denied</body></html>")
    rc = pw.run()
    assert rc == 1
    assert _data_rows(fake) == []
