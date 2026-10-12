"""
Hermetic tests for ADR 066 (Ridge Wood monthly re-verification + provenance
manifest). No network, no creds, no committed data — the report-list HTML is a
synthetic string and every report PDF is built in-process with PyMuPDF (data-guard
CI forbids committed *.pdf/*.html/*.json). Covers:
  - the two new additive ridgewood_client helpers (scrape_all_report_links,
    allowed_report_host) — ridgewood_client's live-path functions are untouched;
  - the pure manifest/decision helpers in ridgewood_reverify;
  - the full run() flow (unchanged / changed / removed / new-month-ignored /
    multi-link / page-restructure guard / crash-retry idempotency / dry-run) driven
    through a fake Sheets service, same idiom as tests/test_ridgewood.py.
"""
import copy
import hashlib
import json
import re

import fitz

import ridgewood_client as rc
import ridgewood_reverify as rr
import sheet_writer as sw

THR = {"h2s_24h_ppb": 72, "h2s_15min_ppb": 750}

_FOOTNOTES = (
    "1 Notifications to be sent to the Ridge Wood Elementary School if a 15-minute "
    "average concentration exceeds 750 ppb (0.75 ppm, USEPA Acute Exposure Guideline "
    "Value) or a 24-hour average concentration exceeds 72 ppb (0.072 ppm).\n"
)
_ALL_CLEAR = "a. No notifications required to be sent to Ridge Wood Elementary for elevated H2S.\n"


def _report_text(month, values, *, all_clear=True):
    y, m = month.split("-")
    narrative = [
        "Arbor Hills Landfill, Inc.",
        "Ridge Wood Elementary Hydrogen Sulfide (H2S) Monitoring: 24-hour average concentrations",
        f"{rc._MONTH_NAMES[int(m)]} {y}",
        "7) Data Report (24-hour Average Concentrations)",
    ]
    if all_clear:
        narrative.append(_ALL_CLEAR.strip())
    table = ["Day", "24-hr Average H2S Concentration"]
    for i, v in enumerate(values, start=1):
        table.append(f"{int(m)}/{i}/{y[2:]}")
        table.append(str(v))
    return "\n".join(narrative + table + [_FOOTNOTES.strip()])


def _build_pdf_bytes(path, month, values, *, all_clear=True):
    """Build a synthetic report PDF to `path` and return its raw bytes. Returning
    bytes (not re-reading per call) lets tests pin EXACTLY which bytes a given
    'month' download returns across a run, so an 'unchanged' assertion is never at
    the mercy of fitz producing non-identical output across two separate saves."""
    doc = fitz.open()
    page = doc.new_page()
    y = 50.0
    for ln in _report_text(month, values, all_clear=all_clear).split("\n"):
        page.insert_text((40, y), ln, fontsize=8)
        y += 11.0
    doc.save(path)
    doc.close()
    with open(path, "rb") as fh:
        return fh.read()


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def page_html(months, *, extra=()):
    links = [f'<a href="/Files/{m}_Arbor Hills H2S Data_24-hour ave.pdf">{m}</a>' for m in months]
    links.extend(extra)
    pad = "<!-- " + "x" * 2100 + " -->"
    return "<html><body>" + "\n".join(links) + pad + "</body></html>"


# ==============================================================================
# Fake Sheets (same idiom as tests/test_ridgewood.py)
# ==============================================================================

def _tab(rng):
    return re.match(r"'([^']+)'", rng).group(1)


def _start_row(rng):
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 1


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self, num_retries=0):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(_tab(range))
        if rows is None:
            return _Req({"values": []})
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


def _seed_reports_tab(fake, rows):
    fake._values._tabs[sw.TAB_RIDGEWOOD] = [list(sw.RIDGEWOOD_REPORT_HEADERS)] + [list(r) for r in rows]


def _rows(fake, tab):
    return fake._values._tabs.get(tab, [])[1:]


def _cfg(**over):
    rw = {"reverify_enabled": True, "page_url": "http://page",
          "base_url": "https://www.arborhillsmonitoring.com", "thresholds": copy.deepcopy(THR)}
    rw.update(over)
    return {"ridgewood": rw}


def _recording_send_ok(sent):
    def send(subj, body, c, recipients=None):
        sent.append((subj, body, recipients))
        return True
    return send


def _write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def _wire(monkeypatch, cfg, *, page_months, sheet_rows, pdf_bytes_by_month,
          drive=True, owners=("owner@example.test",), send_ok=True):
    """Wire ridgewood_reverify.run() against a fake Sheets service + a synthetic
    page + fixed PDF bytes per month (never regenerated — see _build_pdf_bytes)."""
    fake = FakeSheets()
    _seed_reports_tab(fake, sheet_rows)
    sent, uploads = [], []

    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rr, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rr.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: page_html(page_months))

    def fake_download(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        with open(dest, "wb") as fh:
            fh.write(pdf_bytes_by_month[month])
        return dest
    monkeypatch.setattr(rr, "_download_capped", fake_download)

    monkeypatch.setattr(rr.ac, "is_configured", lambda env=rr.FOLDER_ENV: drive)
    monkeypatch.setattr(rr.ac, "folder_id", lambda env=rr.FOLDER_ENV: "FID")
    if drive:
        monkeypatch.setattr(rr.ac, "oauth_drive_service", lambda: object())

        def fake_upload(service, local, name, folder):
            uploads.append(name)
            return f"http://drive/{name}"
        monkeypatch.setattr(rr.ac, "upload_pdf", fake_upload)

    monkeypatch.setattr(rr.ea, "load_owner_emails", lambda c=None: set(owners))

    def fake_send(subj, body, c, recipients=None):
        sent.append((subj, body, recipients))
        return send_ok
    monkeypatch.setattr(rr.ea, "send_email", fake_send)
    return fake, sent, uploads


# ==============================================================================
# ridgewood_client additive helpers (live scrape_report_links/download_report
# untouched — these are NEW functions only)
# ==============================================================================

def test_scrape_all_report_links_keeps_every_link_per_month():
    html = ('<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave.pdf">a</a>'
            '<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave_9999.pdf">b</a>'
            '<a href="/Files/2026-04_Arbor Hills H2S Data_24-hour ave.pdf">c</a>')
    by_month = rc.scrape_all_report_links(html)
    assert len(by_month["2026-05"]) == 2
    assert len(by_month["2026-04"]) == 1
    assert all(u["url"].startswith("https://www.arborhillsmonitoring.com/Files/")
               for u in by_month["2026-05"])


def test_scrape_all_report_links_drops_unparsed_month():
    html = '<a href="/GFLEnvironmental/Files/Report_202012_old.pdf">old</a>'
    assert rc.scrape_all_report_links(html) == {}


def test_allowed_report_host_https_and_matching_host_only():
    hosts = {"www.arborhillsmonitoring.com"}
    assert rc.allowed_report_host("https://www.arborhillsmonitoring.com/Files/x.pdf", hosts) is True
    assert rc.allowed_report_host("http://www.arborhillsmonitoring.com/Files/x.pdf", hosts) is False
    assert rc.allowed_report_host("https://evil.example.com/Files/x.pdf", hosts) is False
    assert rc.allowed_report_host("not a url", hosts) is False


# ==============================================================================
# Pure decision helpers
# ==============================================================================

def test_should_run_gate():
    assert rr._should_run({"ridgewood": {"reverify_enabled": False}})[0] is False
    assert rr._should_run({})[0] is False
    assert rr._should_run({"ridgewood": {"reverify_enabled": True}})[0] is True


def test_latest_version_picks_last_by_list_order():
    manifest = {"months": {"2026-05": [
        {"fetched_at": "2026-05-01T00:00:00Z", "content_hash": "old"},
        {"fetched_at": "2026-06-10T00:00:00Z", "content_hash": "new"},
    ]}}
    assert rr.latest_version(manifest, "2026-05")["content_hash"] == "new"
    assert rr.latest_version(manifest, "2099-01") is None


def test_latest_version_is_not_fooled_by_identical_fetched_at_timestamps():
    """A round-3 review found: two versions appended within the same wall-clock
    SECOND (now_iso is second-resolution) share an identical fetched_at string.
    max(key=fetched_at) breaks that tie by keeping the FIRST (oldest) one it
    sees, silently returning a stale version as "current". List order (append
    order, which never ties) is the fix."""
    same_ts = "2026-10-12T00:00:00Z"
    manifest = {"months": {"2026-05": [
        {"fetched_at": same_ts, "content_hash": "old", "status": "original"},
        {"fetched_at": same_ts, "content_hash": "new", "status": "removed-from-source 2026-10-12"},
    ]}}
    assert rr.latest_version(manifest, "2026-05")["content_hash"] == "new"


def test_seed_version_uses_sheet_hash_when_present():
    row = {"report_title": "T", "source_url": "u", "fetched_at": "2026-07-15T10:00:00",
           "content_hash": "abc123", "archive_link": "http://drive/x.pdf"}
    seed = rr.seed_version(row, "fallback title", "fallback url", "deadbeef", "2026-10-12T00:00:00Z")
    assert seed["content_hash"] == "abc123"       # the Sheet's real recorded hash wins
    assert seed["fetched_at"] == "2026-07-15T10:00:00"  # the REAL original date, not today
    assert seed["status"] == "original"


def test_seed_version_blank_sheet_hash_falls_back_to_download():
    row = {"content_hash": "", "report_title": "", "source_url": "", "archive_link": "", "fetched_at": ""}
    seed = rr.seed_version(row, "T", "u", "deadbeef", "2026-10-12T00:00:00Z")
    assert seed["content_hash"] == "deadbeef"     # "can't verify yet" -> never a false revision
    assert seed["fetched_at"] == "2026-10-12T00:00:00Z"


def test_seed_version_no_sheet_row_at_all():
    seed = rr.seed_version(None, "T", "u", "deadbeef", "2026-10-12T00:00:00Z")
    assert seed["content_hash"] == "deadbeef" and seed["report_title"] == "T"


def test_reverify_month_first_check_matches_baselines_silently():
    manifest = {"months": {}}
    row = {"content_hash": "h1", "report_title": "T", "source_url": "u",
           "fetched_at": "2026-07-15T00:00:00", "archive_link": ""}
    outcome, versions = rr.reverify_month(manifest, "2026-05", row, "T", "u", "h1", "2026-10-12", "now")
    assert outcome == "baseline" and len(versions) == 1
    assert versions[0]["status"] == "original" and versions[0]["last_verified"] == "2026-10-12"


def test_reverify_month_first_check_already_differs_from_sheet_gets_seed_and_revision():
    manifest = {"months": {}}
    row = {"content_hash": "h1-old", "report_title": "T", "source_url": "u",
           "fetched_at": "2026-07-15T00:00:00", "archive_link": "http://drive/old.pdf"}
    outcome, versions = rr.reverify_month(manifest, "2026-05", row, "T", "u", "h2-new", "2026-10-12", "now")
    assert outcome == "changed" and len(versions) == 2
    assert versions[0]["content_hash"] == "h1-old" and versions[0]["status"] == "original"
    assert versions[1]["content_hash"] == "h2-new" and versions[1]["status"] == "revised 2026-10-12"


def test_reverify_month_unchanged_vs_changed_with_existing_history():
    manifest = {"months": {"2026-05": [
        {"fetched_at": "2026-07-15T00:00:00", "content_hash": "h1", "status": "original"},
    ]}}
    outcome, versions = rr.reverify_month(manifest, "2026-05", None, "T", "u", "h1", "2026-10-12", "now")
    assert outcome == "unchanged" and versions == []
    outcome2, versions2 = rr.reverify_month(manifest, "2026-05", None, "T", "u", "h2", "2026-10-12", "now")
    assert outcome2 == "changed" and len(versions2) == 1
    assert versions2[0]["content_hash"] == "h2" and versions2[0]["status"] == "revised 2026-10-12"


def test_is_retry_event_scoped_to_most_recent_not_ever_logged():
    """Pins the round-3 fix directly: a hash collision with an OLDER logged event
    for the same month is NOT a retry if something newer has since been logged."""
    last_event = {"2026-05": ("reappeared", "H1", "http://drive/x.pdf")}
    # Retrying the exact most-recent event -> recognized.
    assert rr._is_retry_event(last_event, "2026-05", "reappeared", "H1") == "http://drive/x.pdf"
    # A DIFFERENT event type with the same hash is not a retry of THIS event.
    assert rr._is_retry_event(last_event, "2026-05", "removed-from-source", "H1") is None
    # A different hash is not a retry.
    assert rr._is_retry_event(last_event, "2026-05", "reappeared", "H2") is None
    # A month with no logged history at all is not a retry.
    assert rr._is_retry_event(last_event, "2099-01", "reappeared", "H1") is None


def test_apply_staged_merges_additions_before_removal_flags():
    manifest = {"months": {}}
    seed = {"fetched_at": "2026-07-15T00:00:00Z", "content_hash": "h1", "status": "original",
            "last_verified": None, "report_title": "T", "source_url": "u", "archive_link": "orig"}
    rr._apply_staged(manifest, {"2026-05": [seed]}, ["2026-05"], "2026-10-12", "2026-10-12T00:00:00Z")
    versions = manifest["months"]["2026-05"]
    assert len(versions) == 2   # the seed is NEVER mutated — removal appends a new version
    assert versions[0] is seed and versions[0]["status"] == "original"
    v = rr.latest_version(manifest, "2026-05")
    assert v["status"] == "removed-from-source 2026-10-12" and v["last_verified"] == "2026-10-12"
    assert v["content_hash"] == "h1" and v["archive_link"] == "orig"   # carried forward, not reset


def test_format_owner_alert_sections_and_never_mentions_public_routing():
    changed = [("2026-05", "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "http://drive/new.pdf", "note here")]
    removed = [("2026-04", "http://drive/old.pdf")]
    multi = [("2026-03", ["http://x/1.pdf", "http://x/2.pdf"])]
    subject, body = rr.format_owner_alert(changed, removed, multi)
    assert "OWNER-ONLY" in body and "never routed" in body
    assert "2026-05" in body and "note here" in body
    assert "2026-04" in body and "http://drive/old.pdf" in body
    assert "2026-03" in body and "http://x/2.pdf" in body


def test_revision_note_calls_out_new_exceedance():
    row = {"max_24h": "3", "alert": "ok"}
    verdict = {"exceed_24h": True, "alert": True, "value_token": "85"}
    note = rr._revision_note(True, verdict, row)
    assert "EXCEEDANCE" in note and "85 ppb" in note and "3 ppb" in note


def test_revision_note_no_text_layer():
    assert "scanned" in rr._revision_note(False, {}, None)


def test_manifest_round_trip_atomic_and_deterministic(tmp_path):
    path = str(tmp_path / "manifest.json")
    m = {"months": {"2026-05": [{"content_hash": "h1", "fetched_at": "t0", "status": "original",
                                 "report_title": "T", "source_url": "u", "archive_link": "",
                                 "last_verified": None}]}}
    rr.save_manifest(m, path)
    assert not (tmp_path / "manifest.json.tmp").exists()   # no leftover temp file
    loaded = rr.load_manifest(path)
    assert loaded["months"] == m["months"]
    assert loaded["schema_version"] == rr.SCHEMA_VERSION
    with open(path) as fh:
        raw1 = fh.read()
    rr.save_manifest(loaded, path)
    with open(path) as fh:
        raw2 = fh.read()
    # Only generated_at (a timestamp) may differ between two saves of the same data.
    d1, d2 = json.loads(raw1), json.loads(raw2)
    d1.pop("generated_at")
    d2.pop("generated_at")
    assert d1 == d2
    assert raw1.endswith("\n")


def test_load_manifest_missing_file_returns_empty_shape(tmp_path):
    m = rr.load_manifest(str(tmp_path / "nope.json"))
    assert m == {"schema_version": rr.SCHEMA_VERSION, "generated_at": "", "months": {}}


# ==============================================================================
# run() — full flow via fake Sheets + monkeypatched network/drive/email
# ==============================================================================

def test_gate_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(rr, "load_config", lambda: {"ridgewood": {"reverify_enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(rr.dc, "sheets_service", boom)
    monkeypatch.setattr(rr.rc, "fetch_page", boom)
    assert rr.run() == 0


def test_no_archived_months_yet_is_noop(monkeypatch):
    fake = FakeSheets()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rr, "load_config", lambda: _cfg())
    monkeypatch.setattr(rr.dc, "sheets_service", lambda: fake)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch the page"))
    monkeypatch.setattr(rr.rc, "fetch_page", boom)
    assert rr.run() == 0


def test_first_run_matching_sheet_hash_baselines_silently_no_alert(monkeypatch, tmp_path):
    pdf_path = str(tmp_path / "may.pdf")
    blob = _build_pdf_bytes(pdf_path, "2026-05", ["<1"] * 10)
    h = _hash(blob)
    sheet_row = ["2026-05", "T", "<1", 10, "ok", "u", h, "http://drive/orig.pdf", "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-05"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-05": blob})
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: setattr(rr, "_saved", m))
    assert rr.run() == 0
    assert sent == [] and uploads == []
    assert _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) == []
    assert rr._saved["months"]["2026-05"][0]["status"] == "original"
    assert rr._saved["months"]["2026-05"][0]["content_hash"] == h


def test_second_run_unchanged_bumps_last_verified_no_alert(monkeypatch, tmp_path):
    pdf_path = str(tmp_path / "may.pdf")
    blob = _build_pdf_bytes(pdf_path, "2026-05", ["<1"] * 10)
    h = _hash(blob)
    sheet_row = ["2026-05", "T", "<1", 10, "ok", "u", h, "http://drive/orig.pdf", "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-05"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-05": blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})
    assert rr.run() == 0   # first run: baseline
    assert rr.run() == 0   # second run: same bytes -> unchanged
    assert sent == [] and uploads == []
    v = saved["months"]["2026-05"][0]
    assert v["last_verified"] is not None and v["content_hash"] == h


def test_changed_report_mirrors_logs_and_alerts(monkeypatch, tmp_path):
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-06", ["<1"] * 5)
    new_blob = _build_pdf_bytes(str(tmp_path / "new.pdf"), "2026-06", ["<1", "85", "3"])
    sheet_row = ["2026-06", "T", "<1", 5, "ok", "u", _hash(old_blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-06": new_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    assert rr.run() == 0
    assert len(sent) == 1
    subject, body, recipients = sent[0]
    assert recipients == ["owner@example.test"]
    assert "revised" in subject and "EXCEEDANCE" in body
    assert len(uploads) == 1 and "revised" in uploads[0] and _hash(new_blob)[:8] in uploads[0]
    rows = _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)
    assert len(rows) == 1 and rows[0][0] == "2026-06" and rows[0][1] == "revised"
    # the earlier copy's version entry is preserved (two versions: original + revised)
    versions = saved["months"]["2026-06"]
    assert len(versions) == 2
    assert versions[0]["content_hash"] == _hash(old_blob) and versions[0]["status"] == "original"
    assert versions[1]["content_hash"] == _hash(new_blob) and "revised" in versions[1]["status"]
    assert versions[1]["archive_link"] == uploads[0] or versions[1]["archive_link"].endswith(uploads[0])


def test_changed_report_drive_not_configured_still_logs_and_alerts(monkeypatch, tmp_path):
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-06", ["<1"] * 5)
    new_blob = _build_pdf_bytes(str(tmp_path / "new.pdf"), "2026-06", ["<1"] * 6)
    sheet_row = ["2026-06", "T", "<1", 5, "ok", "u", _hash(old_blob), "", "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-06": new_blob}, drive=False)
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: None)
    assert rr.run() == 0
    assert len(sent) == 1 and uploads == []
    rows = _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)
    assert rows[0][5] == ""   # New Archive Link blank — mirror skipped, record kept


def test_changed_report_empty_owner_list_fails_loudly_and_does_not_advance_manifest(monkeypatch, tmp_path):
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-06", ["<1"] * 5)
    new_blob = _build_pdf_bytes(str(tmp_path / "new.pdf"), "2026-06", ["<1"] * 6)
    sheet_row = ["2026-06", "T", "<1", 5, "ok", "u", _hash(old_blob), "", "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-06": new_blob}, owners=())
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    assert rr.run() == 1
    assert sent == []
    # Sheet row IS already durable even though the alert never went out.
    assert len(_rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)) == 1
    assert "2026-06" not in (saved.get("months") or {})   # manifest NOT advanced for this month


def test_changed_report_email_send_failure_does_not_advance_but_keeps_sheet_row(monkeypatch, tmp_path):
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-06", ["<1"] * 5)
    new_blob = _build_pdf_bytes(str(tmp_path / "new.pdf"), "2026-06", ["<1"] * 6)
    sheet_row = ["2026-06", "T", "<1", 5, "ok", "u", _hash(old_blob), "", "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-06": new_blob}, send_ok=False)
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    assert rr.run() == 1
    assert len(sent) == 1   # the attempt was made
    assert len(_rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)) == 1
    assert "2026-06" not in (saved.get("months") or {})


def test_retry_after_failed_alert_skips_reupload_and_finishes_advancing(monkeypatch, tmp_path):
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-06", ["<1"] * 5)
    new_blob = _build_pdf_bytes(str(tmp_path / "new.pdf"), "2026-06", ["<1"] * 6)
    sheet_row = ["2026-06", "T", "<1", 5, "ok", "u", _hash(old_blob), "", "2026-07-15T10:00:00"]
    cfg = _cfg()
    fake, sent, uploads = _wire(
        monkeypatch, cfg, page_months=["2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-06": new_blob}, send_ok=False)
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})
    assert rr.run() == 1                      # first attempt: email fails
    assert len(uploads) == 1 and len(_rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)) == 1

    # Second run: same change, now the email succeeds.
    rr.ea.send_email = lambda subj, body, c, recipients=None: (sent.append((subj, body, recipients)) or True)
    assert rr.run() == 0
    assert len(uploads) == 1                  # NOT re-uploaded
    assert len(_rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)) == 1   # NOT re-logged
    assert len(sent) == 2                     # the alert was retried and this time sent
    versions = saved["months"]["2026-06"]
    assert len(versions) == 2 and versions[1]["archive_link"].endswith(uploads[0])


def test_removed_from_source_confirmed_on_two_fetches_alerts_and_flags(monkeypatch, tmp_path):
    # Two archived months; only 2026-04 stays listed (so the page scrape itself
    # isn't empty — an all-zero scrape is Stream G's own "bad fetch" case, not a
    # removal; see ridgewood_archiver's identical "no report links -> abort").
    apr_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 5)
    may_blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    rows = [
        ["2026-04", "T", "<1", 5, "ok", "u", _hash(apr_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(may_blob), "http://drive/may.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": apr_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))

    assert rr.run() == 0
    assert len(sent) == 1 and "removed" in sent[0][0]
    revision_rows = _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)
    assert revision_rows[0][0] == "2026-05" and revision_rows[0][1] == "removed-from-source"
    versions = saved["months"]["2026-05"]
    assert len(versions) == 2   # original preserved, removal APPENDED (never mutated in place)
    assert versions[0]["status"] == "original"
    assert versions[-1]["status"].startswith("removed-from-source")
    assert "2026-04" not in saved["months"] or saved["months"]["2026-04"][0]["status"] == "original"


def test_removed_month_that_stays_missing_is_not_re_alerted(monkeypatch, tmp_path):
    apr_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 5)
    may_blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    rows = [
        ["2026-04", "T", "<1", 5, "ok", "u", _hash(apr_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(may_blob), "http://drive/may.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": apr_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})

    assert rr.run() == 0
    assert rr.run() == 0           # still missing next run
    assert len(sent) == 1          # NOT re-alerted
    assert len(_rows(fake, sw.TAB_RIDGEWOOD_REVISIONS)) == 1


def test_retry_after_failed_alert_on_removal_does_not_duplicate_sheet_row(monkeypatch, tmp_path):
    """A review found the 'revised' path had a retry idempotency guard but
    'removed-from-source' did not: a failed owner alert left the manifest
    un-advanced (by design), and the NEXT run's removal loop re-logged a second,
    duplicate Sheet row for the identical removal every time it retried."""
    apr_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 5)
    may_blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    rows = [
        ["2026-04", "T", "<1", 5, "ok", "u", _hash(apr_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(may_blob), "http://drive/may.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": apr_blob}, owners=())   # empty owners -> alert always fails
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})

    assert rr.run() == 1   # removal detected, but the empty owner list fails the alert
    assert rr.run() == 1   # retry: same removal, same failure
    assert rr.run() == 1
    assert sent == []
    removed_rows = [r for r in _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) if r[1] == "removed-from-source"]
    assert len(removed_rows) == 1   # NOT one per retry
    assert removed_rows[0][5] == "http://drive/may.pdf"   # New Archive Link carried forward (not blanked)
    assert removed_rows[0][6] == "u"                      # Source URL carried forward too
    # The manifest itself is also never advanced across all three failed attempts.
    assert "2026-05" not in saved.get("months", {})


def test_retry_after_failed_alert_on_reappearance_does_not_duplicate_sheet_row(monkeypatch, tmp_path):
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    other_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 3)
    rows = [
        ["2026-04", "T", "<1", 3, "ok", "u", _hash(other_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": other_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})
    assert rr.run() == 0   # run 1: 2026-05 flagged removed (owners present -> succeeds)
    sent.clear()

    # 2026-05 reappears unchanged; the owner alert now fails for every retry.
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: page_html(["2026-04", "2026-05"]))

    def fake_download2(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        _write_bytes(dest, other_blob if month == "2026-04" else blob)
    monkeypatch.setattr(rr, "_download_capped", fake_download2)
    monkeypatch.setattr(rr.ea, "load_owner_emails", lambda c=None: set())   # now fails

    assert rr.run() == 1
    assert rr.run() == 1
    assert sent == []
    reappeared_rows = [r for r in _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) if r[1] == "reappeared"]
    assert len(reappeared_rows) == 1   # NOT one per retry
    assert len(saved["months"]["2026-05"]) == 2   # original + removed only -- reappearance never advanced


def test_new_month_not_yet_archived_is_left_alone(monkeypatch, tmp_path):
    may_blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    june_blob = _build_pdf_bytes(str(tmp_path / "june.pdf"), "2026-06", ["<1"] * 5)
    sheet_row = ["2026-05", "T", "<1", 5, "ok", "u", _hash(may_blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    # 2026-06 is on the page but NOT yet in Stream G's dedup set (no sheet row for it).
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-05", "2026-06"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-05": may_blob, "2026-06": june_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    assert rr.run() == 0
    assert "2026-06" not in saved.get("months", {})   # Stream G's job, never touched here
    assert sent == []


def test_page_restructure_guard_aborts_without_flagging_removals(monkeypatch, tmp_path):
    blobs = {}
    sheet_rows = []
    for i in range(1, 5):
        month = f"2026-0{i}"
        b = _build_pdf_bytes(str(tmp_path / f"{month}.pdf"), month, ["<1"] * 3)
        blobs[month] = b
        sheet_rows.append([month, "T", "<1", 3, "ok", "u", _hash(b), "", "2026-07-15T10:00:00"])
    # Only ONE of the four archived months is still listed -> 75% missing, over the cap.
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-01"], sheet_rows=sheet_rows,
        pdf_bytes_by_month=blobs)
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH:
                        (_ for _ in ()).throw(AssertionError("must not save")))
    assert rr.run() == 1
    assert sent == []
    assert _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) == []


def test_multi_link_month_is_flagged_in_owner_alert(monkeypatch, tmp_path):
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    sheet_row = ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    fake = FakeSheets()
    _seed_reports_tab(fake, [sheet_row])
    sent = []
    cfg = _cfg()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rr, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rr.dc, "sheets_service", lambda: fake)
    html = page_html([], extra=[
        '<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave.pdf">a</a>',
        '<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave_7777.pdf">b</a>',
    ])
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: html)
    monkeypatch.setattr(rr, "_download_capped", lambda url, dest, timeout=60: _write_bytes(dest, blob))
    monkeypatch.setattr(rr.ea, "load_owner_emails", lambda c=None: {"owner@example.test"})
    monkeypatch.setattr(rr.ea, "send_email", _recording_send_ok(sent))
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: None)
    assert rr.run() == 0
    assert len(sent) == 1
    assert "multiple posted links" in sent[0][0]
    assert "MULTIPLE LINKS POSTED" in sent[0][1]

    # Same two links still posted next run -> NOT re-alerted (the Sheet row is the
    # idempotency key, same anti-noise philosophy as removed-from-source).
    assert rr.run() == 0
    assert len(sent) == 1
    assert len([r for r in _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) if r[1] == "multi-link"]) == 1


def test_reappeared_with_identical_content_corrects_stale_removed_status(monkeypatch, tmp_path):
    """A month flagged removed-from-source, then later found listed again with
    BYTE-IDENTICAL content, must not be left permanently marked removed (the bug
    an independent review found) — a fresh 'original' version corrects it, without
    mutating or losing the removed version's own history."""
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    other_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 3)
    rows = [
        ["2026-04", "T", "<1", 3, "ok", "u", _hash(other_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": other_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})
    assert rr.run() == 0   # run 1: 2026-05 flagged removed
    assert saved["months"]["2026-05"][-1]["status"].startswith("removed-from-source")
    sent.clear()

    # Run 2: 2026-05 is back on the page with IDENTICAL bytes to what we had before removal.
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: page_html(["2026-04", "2026-05"]))

    def fake_download2(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        _write_bytes(dest, other_blob if month == "2026-04" else blob)
    monkeypatch.setattr(rr, "_download_capped", fake_download2)

    assert rr.run() == 0
    versions = saved["months"]["2026-05"]
    assert len(versions) == 3   # original, removed-from-source, reappeared -- all preserved
    assert versions[1]["status"].startswith("removed-from-source")   # untouched, not rewritten
    assert versions[-1]["status"] == "original"
    assert versions[-1]["content_hash"] == _hash(blob)
    assert len(sent) == 1 and "reappeared" in sent[0][0]


def test_removed_again_with_same_hash_after_reappearing_is_a_new_event_not_a_stale_retry(
        monkeypatch, tmp_path):
    """A round-3 review found: removed (hash H) -> reappeared (hash H, same
    content) -> removed AGAIN (still hash H, nothing ever changed) is a
    genuinely new, third removal event -- not a retry of the FIRST removal, even
    though both removals key on the identical content hash H. The naive "was
    (month, event, hash) ever logged" idempotency check (an earlier version of
    this fix) would wrongly treat the second removal as an unresolved retry of
    the first and silently drop its Sheet row. The fix scopes idempotency to
    "is this the month's MOST RECENT logged event" instead."""
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    other_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 3)
    rows = [
        ["2026-04", "T", "<1", 3, "ok", "u", _hash(other_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf", "2026-07-15T10:00:00"],
    ]
    fake, sent, _ = _wire(
        monkeypatch, _cfg(), page_months=["2026-04"], sheet_rows=rows,
        pdf_bytes_by_month={"2026-04": other_blob})
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    monkeypatch.setattr(rr, "load_manifest", lambda path=rr.MANIFEST_PATH: saved or
                        {"schema_version": 1, "generated_at": "", "months": {}})

    assert rr.run() == 0   # run 1: removed (hash H)
    sent.clear()

    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: page_html(["2026-04", "2026-05"]))

    def fake_download2(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        _write_bytes(dest, other_blob if month == "2026-04" else blob)
    monkeypatch.setattr(rr, "_download_capped", fake_download2)
    assert rr.run() == 0   # run 2: reappeared (same hash H)
    sent.clear()

    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: page_html(["2026-04"]))
    assert rr.run() == 0   # run 3: removed again (still hash H) -- a NEW event

    versions = saved["months"]["2026-05"]
    assert len(versions) == 4   # original, removed, reappeared(=original), removed again
    assert versions[-1]["status"].startswith("removed-from-source")
    assert len(sent) == 1 and "removed" in sent[0][0]   # alerted for the SECOND removal
    removed_rows = [r for r in _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) if r[1] == "removed-from-source"]
    assert len(removed_rows) == 2   # NOT collapsed into one -- two real removal events


def test_download_failure_is_skipped_not_changed_or_removed(monkeypatch, tmp_path):
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    sheet_row = ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(), page_months=["2026-05"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-05": blob})

    def boom(url, dest, timeout=60):
        raise rr.ReverifyDownloadError("boom")
    monkeypatch.setattr(rr, "_download_capped", boom)
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))
    assert rr.run() == 1          # the run is marked red...
    assert sent == []             # ...but nothing is alerted as changed/removed
    assert "2026-05" not in saved.get("months", {})


def test_multi_link_sheet_write_failure_does_not_abort_other_months(monkeypatch, tmp_path):
    """A review found the multi-link Sheet write sat OUTSIDE the per-month
    try/except, so a Sheets failure there would propagate out of the loop and
    discard every other month's already-decided progress in the same run."""
    multi_blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    other_blob = _build_pdf_bytes(str(tmp_path / "apr.pdf"), "2026-04", ["<1"] * 3)
    rows = [
        ["2026-04", "T", "<1", 3, "ok", "u", _hash(other_blob), "http://drive/apr.pdf", "2026-07-15T10:00:00"],
        ["2026-05", "T", "<1", 5, "ok", "u", _hash(multi_blob), "http://drive/may.pdf", "2026-07-15T10:00:00"],
    ]
    fake = FakeSheets()
    _seed_reports_tab(fake, rows)
    cfg = _cfg()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rr, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rr.dc, "sheets_service", lambda: fake)
    html = page_html(["2026-04"], extra=[
        '<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave.pdf">a</a>',
        '<a href="/Files/2026-05_Arbor Hills H2S Data_24-hour ave_7777.pdf">b</a>',
    ])
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: html)

    def fake_download(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        _write_bytes(dest, other_blob if month == "2026-04" else multi_blob)
    monkeypatch.setattr(rr, "_download_capped", fake_download)

    def boom_on_multi_link(service, sheet_id, month, event, *a, **k):
        if event == "multi-link":
            raise RuntimeError("transient Sheets 503")
    monkeypatch.setattr(rr.sw, "append_ridgewood_revision_row", boom_on_multi_link)
    monkeypatch.setattr(rr.ac, "is_configured", lambda env=rr.FOLDER_ENV: False)
    saved = {}
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: saved.update(m))

    assert rr.run() == 1   # 2026-05's multi-link write failure marks the run red...
    # ...but 2026-04 (processed before the failure, in month-sorted order) still baselined.
    assert saved["months"]["2026-04"][0]["status"] == "original"
    assert "2026-05" not in saved.get("months", {})   # the failed month itself recorded nothing


def test_disallowed_host_link_is_skipped(monkeypatch, tmp_path):
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    sheet_row = ["2026-05", "T", "<1", 5, "ok", "u", _hash(blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    fake = FakeSheets()
    _seed_reports_tab(fake, [sheet_row])
    cfg = _cfg()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rr, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rr.dc, "sheets_service", lambda: fake)
    html = ('<a href="https://evil.example.com/Files/2026-05_Arbor Hills H2S Data_24-hour ave.pdf">a</a>'
            + "<!-- " + "x" * 2100 + " -->")
    monkeypatch.setattr(rr.rc, "fetch_page", lambda url=None, timeout=60: html)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download a disallowed host"))
    monkeypatch.setattr(rr, "_download_capped", boom)
    monkeypatch.setattr(rr, "save_manifest", lambda m, path=rr.MANIFEST_PATH: None)
    assert rr.run() == 1


def test_shipped_config_reverify_disabled_and_workflow_scheduled():
    """Shipped-config pin. Unlike test_nsite_violations.py's parked-workflow guard
    (which tolerates a workflow parked outside .github/workflows/ as a valid
    interim state), this PR's workflow is ALWAYS committed directly — there is no
    parked state to allow for — so both conditions are asserted unconditionally,
    not as an "X or Y" escape hatch (an earlier draft of this test had `scheduled`
    required unconditionally on one line and then `... or scheduled` on the next,
    which is always true and can never fail)."""
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert (root / ".github" / "workflows" / "ridgewood-reverify.yml").exists(), (
        "the workflow file has gone missing entirely")
    assert cfg["ridgewood"]["reverify_enabled"] is False, (
        "ridgewood.reverify_enabled shipped true -- activation is Trisha's call, "
        "not this PR's")


def test_dry_run_bypasses_disabled_gate_and_writes_nothing(monkeypatch, tmp_path):
    blob = _build_pdf_bytes(str(tmp_path / "may.pdf"), "2026-05", ["<1"] * 5)
    old_blob = _build_pdf_bytes(str(tmp_path / "old.pdf"), "2026-05", ["<1"] * 4)
    sheet_row = ["2026-05", "T", "<1", 4, "ok", "u", _hash(old_blob), "http://drive/orig.pdf",
                 "2026-07-15T10:00:00"]
    fake, sent, uploads = _wire(
        monkeypatch, _cfg(reverify_enabled=False), page_months=["2026-05"], sheet_rows=[sheet_row],
        pdf_bytes_by_month={"2026-05": blob})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run must not write"))
    monkeypatch.setattr(rr, "save_manifest", boom)
    assert rr.run(dry_run=True) == 0
    assert sent == [] and uploads == []
    assert _rows(fake, sw.TAB_RIDGEWOOD_REVISIONS) == []
