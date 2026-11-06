"""Full-text search for the public records page (ADR 062 addendum, 2026-11-02).

The publish gate for the Pagefind bundle lives in fulltext_search /
build_fulltext_index (check_publish_safety cannot read Pagefind's compressed
shards), so these tests pin every rule that keeps it safe: only published
entries, never a hand-curated row, both name redactions applied, the cap, and
the flag that keeps it all off by default."""
import importlib.util
import json
import os

import pytest

import fulltext_search as fts
import name_check

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DRIVE = "https://drive.google.com/file/d/1mkCebMFER5U-CKnZAbYKrmCX23jnWbNR/view?usp=drivesdk"
NSITE = "https://mienviro.michigan.gov/ncore/downloadfile/-6975595422894935530"


def _entry(**kw):
    e = {"date": "2023-08-29", "title": "WOI report", "facility": "Arbor Hills Landfill",
         "type": "report", "severity": "routine", "link": DRIVE}
    e.update(kw)
    return e


# --- keys and eligibility ------------------------------------------------------

def test_doc_key_drive_and_nsite():
    assert fts.doc_key(DRIVE) == "drive_1mkCebMFER5U-CKnZAbYKrmCX23jnWbNR"
    assert fts.doc_key(NSITE) == "nsite_-6975595422894935530"
    assert fts.doc_key("https://mienviro.michigan.gov/ncore/downloadpdf/123") == "nsite_123"


@pytest.mark.parametrize("link", ["", "http://drive.google.com/file/d/abcdefghijk/view",
                                  "https://evil.example/file/d/abcdefghijk",
                                  "https://washtenawcomi.api.civicclerk.com/v1/x", "javascript:alert(1)"])
def test_doc_key_rejects_anything_else(link):
    assert fts.doc_key(link) is None


def test_hand_curated_rows_are_never_full_text_indexed():
    # "source" key present (even blank) marks a hand-curated row, the same
    # signal findings_feed._search_entry and search.js use.
    assert fts.is_eligible(_entry())
    assert not fts.is_eligible(_entry(source=""))
    assert not fts.is_eligible(_entry(source="Township FOIA"))
    assert fts.build_record(_entry(source="x"), "some text") is None


def test_entry_year():
    assert fts.entry_year({"date": "2023-08-29"}) == "2023"
    assert fts.entry_year({"date": "2019"}) == "2019"
    assert fts.entry_year({"date": "2019-05"}) == "2019"
    assert fts.entry_year({}) == ""
    assert fts.entry_year({"date": "n.d."}) == ""


# --- redaction -----------------------------------------------------------------

def test_redact_denylist_replaces_known_names_and_markers(monkeypatch):
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re("Testa"), "Testa")])
    out, n = name_check.redact_denylist("Signed by Testa. attestation stays. Trisha-directed")
    assert "Testa" not in out and "attestation" in out
    assert "[name withheld]" in out and "trisha" not in out.lower()
    assert n == 2


def test_prepare_content_redacts_with_both_lists(monkeypatch):
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re("Testa"), "Testa")])
    monkeypatch.setenv("REDACT_NAMES", "Pat Quigley")
    out = fts.prepare_content("Letter from Quigley to Testa about well AHWW312R")
    assert "Testa" not in out and "Quigley" not in out
    assert "AHWW312R" in out


def test_build_record_redacts_again_at_index_time(monkeypatch):
    # A name added to the denylist AFTER a document was cached must still be
    # withheld from the next bundle.
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re("Newname"), "Newname")])
    rec = fts.build_record(_entry(), "cached text mentions Newname once")
    assert "Newname" not in rec["content"]


# --- cap -----------------------------------------------------------------------

def test_cap_is_a_plain_verbatim_cut_with_no_reordered_tail():
    # Nothing after the cap: an earlier de-duplicated tail produced word orders
    # that are not in the document (shown as "matching text", phrase-matchable).
    text = "alpha " * 30 + "AHWW312R 158.3 AHW272R4"
    out = fts.prepare_content(text, cap=100)
    assert len(out) <= 100 and out == text[: len(out)].strip()
    assert "AHWW312R" not in out


def test_build_record_marks_a_truncated_document():
    long_rec = fts.build_record(_entry(), "word " * 100, cap=50)
    assert long_rec["meta"]["truncated"] == "1"
    assert "truncated" not in fts.build_record(_entry(), "short text", cap=50)["meta"]


def test_short_text_is_unchanged_apart_from_whitespace():
    assert fts.prepare_content("a  b\n\n\nc") == "a b c"


# --- records -------------------------------------------------------------------

def test_build_record_shape_and_year_filter():
    rec = fts.build_record(_entry(), "Peak 158.3 F at AHWW312R")
    assert rec["url"] == DRIVE
    assert rec["meta"]["title"] == "WOI report" and rec["meta"]["date"] == "2023-08-29"
    assert rec["filters"]["year"] == ["2023"]
    assert rec["filters"]["facility"] == ["Arbor Hills Landfill"]


def test_build_record_skips_empty_text():
    assert fts.build_record(_entry(), "   \n ") is None


# --- cache builder (no network) ------------------------------------------------

def test_pending_skips_done_and_dedupes_and_retries_errors():
    cache = _script("build_fulltext_cache")
    other = "https://drive.google.com/file/d/2bbbbbbbbbbbbbbbbbbbbbbbbbb/view"
    third = "https://drive.google.com/file/d/3ccccccccccccccccccccccccccc/view"
    entries = [_entry(), _entry(title="dup row, same Mirror copy"), _entry(link=other),
               _entry(link=third), _entry(source="", link=other)]
    manifest = {fts.doc_key(DRIVE): {"status": "ok"},
                fts.doc_key(other): {"status": "error:Timeout", "attempts": 1},
                fts.doc_key(third): {"status": "error:Timeout", "attempts": cache.MAX_ATTEMPTS}}
    keys = [k for k, _ in cache.pending(entries, manifest)]
    assert keys == [fts.doc_key(other)]


def _make_text_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_cache_run_writes_redacted_text_and_manifest(tmp_path, monkeypatch):
    cache = _script("build_fulltext_cache")
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re("Testa"), "Testa")])
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(), _entry(source="", link=NSITE)]))
    pdf_bytes = _make_text_pdf("Inspector Testa measured AHWW312R at 158.3 F")

    class FakeFetcher:
        def fetch(self, key, entry, dest):
            with open(dest, "wb") as f:
                f.write(pdf_bytes)

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=FakeFetcher())
    assert counts["fetched"] == 1 and counts["eligible"] == 1   # hand-curated row never fetched
    text = (tmp_path / "c" / (fts.doc_key(DRIVE) + ".txt")).read_text()
    assert "AHWW312R" in text and "Testa" not in text
    manifest = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert manifest[fts.doc_key(DRIVE)]["status"] == "ok"


def test_cache_run_records_a_failure_without_stopping(tmp_path):
    cache = _script("build_fulltext_cache")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))

    class Boom:
        def fetch(self, key, entry, dest):
            raise ValueError("not a PDF")   # a real per-document failure: one strike

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Boom())
    assert counts["errors"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert m[fts.doc_key(DRIVE)] == {"status": "error:ValueError", "fetched": m[fts.doc_key(DRIVE)]["fetched"],
                                      "attempts": 1}


def test_index_collect_records_only_published_non_hand_curated(tmp_path):
    idx = _script("build_fulltext_index")
    other = "https://drive.google.com/file/d/2bbbbbbbbbbbbbbbbbbbbbbbbbb/view"
    for link in (DRIVE, other):
        (tmp_path / (fts.doc_key(link) + ".txt")).write_text("text of " + link[-12:])
    entries = [_entry(), _entry(source="x", link=other)]
    records, cov = idx.collect_records(entries, str(tmp_path), 1000)
    assert [r["url"] for r in records] == [DRIVE]
    assert cov == {"documents": 2, "full_text": 1, "eligible": 1, "pending": 1}


# --- flag ----------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("fulltext_search:\n  enabled: true\n  max_bundle_mb: 1\n", True),
    ("fulltext_search:\n  enabled: false\n", False),
    ("fulltext_search:\n  max_bundle_mb: 1\nother:\n  enabled: true\n", False),
    ("x:\n  enabled: true\n", False),
    ("fulltext_search:\n  enabled: True\n", True),
])
def test_flag_reader(text, expected):
    assert _script("fulltext_flag").enabled(text) is expected


def test_flag_reader_agrees_with_yaml():
    import config_loader

    flag = _script("fulltext_flag")
    with open(os.path.join(_REPO, "config.yml"), encoding="utf-8") as f:
        text = f.read()
    assert flag.enabled(text) == bool((config_loader.load_config().get("fulltext_search") or {}).get("enabled"))


def test_bundle_and_cache_dirs_are_gitignored():
    with open(os.path.join(_REPO, ".gitignore"), encoding="utf-8") as f:
        gi = f.read()
    for p in ("site/public-records/fulltext/", ".fulltext-cache/", ".fulltext-bundle/"):
        assert p in gi


def test_a_hanging_extraction_is_an_error_not_a_stalled_run(tmp_path):
    # A malformed PDF once made PyMuPDF spin forever inside fitz.open(); the
    # child process is killed on timeout and the document recorded as an error.
    import subprocess

    cache = _script("build_fulltext_cache")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))

    class Ok:
        def fetch(self, key, entry, dest):
            open(dest, "wb").close()

    def hang(_pdf):
        raise subprocess.TimeoutExpired("extract", 1)

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Ok(), extract=hang)
    assert counts["errors"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert m[fts.doc_key(DRIVE)]["status"] == "error:TimeoutExpired"


def test_secret_names_match_whole_words_only(monkeypatch):
    # On whole documents a bare surname inside another word is common: "Hall"
    # must not turn every "shall" in a permit into a redaction.
    monkeypatch.setenv("REDACT_NAMES", "Jo Hall")
    out = fts.redact_secret_names("The permittee shall notify Jo Hall. Hall agreed; hallway.")
    assert "shall" in out and "hallway" in out
    assert "Hall" not in out.replace("hallway", "")
    assert out.count("[name withheld]") == 2


def test_secret_names_unset_is_a_no_op(monkeypatch):
    monkeypatch.delenv("REDACT_NAMES", raising=False)
    assert fts.redact_secret_names("Jo Hall shall") == "Jo Hall shall"


def test_scanned_documents_are_refetched_after_ocr_window():
    from datetime import date

    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abcdef"]
    entries = [_entry(link=l) for l in links]
    k = [fts.doc_key(l) for l in links]
    manifest = {
        k[0]: {"status": "no_text", "pages": 3, "text_pages": 0, "fetched": "2026-09-01"},   # due
        k[1]: {"status": "no_text", "pages": 3, "text_pages": 0, "fetched": "2026-10-30"},   # too recent
        k[2]: {"status": "ok", "pages": 10, "text_pages": 6, "fetched": "2026-09-01"},       # partly scanned, due
        k[3]: {"status": "ok", "pages": 10, "text_pages": 10, "fetched": "2026-01-01"},      # complete: never
        # k[4] never fetched -> new work, must come first
        k[5]: {"status": "error:Timeout", "attempts": 1},                                    # retry -> new work
    }
    got = [key for key, _ in cache.pending(entries, manifest, today=date(2026, 11, 2))]
    assert got == [k[4], k[5], k[0], k[2]]


def test_fetching_stops_on_the_time_budget(tmp_path):
    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abc"]
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))
    ticks = iter([0, 0, 61 * 60, 61 * 60, 61 * 60])   # start, doc 1 check, then past 60 min

    class Ok:
        def fetch(self, key, entry, dest):
            open(dest, "wb").close()

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Ok(),
                       extract=lambda _p: ["text"], max_minutes=60, clock=lambda: next(ticks))
    assert counts["fetched"] == 1 and counts["stopped_on_time"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert len(m) == 1   # the rest stay pending for the next run


def test_denylist_redaction_takes_the_full_name_before_the_surname(monkeypatch):
    names = ["Testa", "Anthony Testa"]          # surname listed first, as in KNOWN_NAMES
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re(n), n) for n in names])
    out, _ = name_check.redact_denylist("Signed, Anthony Testa. Testa again.")
    assert "Anthony" not in out and "Testa" not in out


def _bundle(tmp_path, links, built, deny=None):
    idx = _script("build_fulltext_index")
    b = tmp_path / "b"
    b.mkdir()
    (b / "bundle-manifest.json").write_text(json.dumps(
        {"built": built, "links": links, "denylist_sha": deny or idx.denylist_fingerprint()}))
    return b


def test_bundle_ok_only_when_current(tmp_path):
    from datetime import date

    ok = _script("fulltext_bundle_ok")
    other = "https://drive.google.com/file/d/2bbbbbbbbbbbbbbbbbbbbbbbbbb/view"
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(), _entry(link=other)]))
    today = date(2026, 11, 3)
    good = _bundle(tmp_path, [DRIVE], "2026-11-02")
    assert ok.check(str(good), str(index), today)[0] is True
    # a new document published since the build is fine
    index.write_text(json.dumps([_entry(), _entry(link=other), _entry(link=NSITE)]))
    assert ok.check(str(good), str(index), today)[0] is True
    # a withdrawn one is not
    index.write_text(json.dumps([_entry(link=other)]))
    assert ok.check(str(good), str(index), today) == (False, "1 indexed document(s) are no longer published")


def test_bundle_refused_when_old_or_denylist_changed_or_missing(tmp_path):
    from datetime import date

    ok = _script("fulltext_bundle_ok")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    old = _bundle(tmp_path, [DRIVE], "2026-10-30")
    assert ok.check(str(old), str(index), date(2026, 11, 3))[0] is False
    (tmp_path / "b").rename(tmp_path / "old")
    stale = _bundle(tmp_path, [DRIVE], "2026-11-03", deny="0" * 64)
    assert ok.check(str(stale), str(index), date(2026, 11, 3))[1].startswith("bundle was built before")
    assert ok.check(str(tmp_path / "nope"), str(index), date(2026, 11, 3))[0] is False


def test_a_listed_name_wrapped_across_lines_is_still_withheld(monkeypatch):
    names = ["Scott Miller", "Mark Johnson"]          # no surname-only entries
    monkeypatch.setattr(name_check, "KNOWN_NAMES", names)
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re(n), n) for n in names])
    monkeypatch.setenv("REDACT_NAMES", "Pat Quigley")
    out = fts.prepare_content("Contact Scott\nMiller or Mark  \n Johnson today; also Pat\r\nQuigley.")
    for w in ("Scott", "Miller", "Mark", "Johnson", "Pat", "Quigley"):
        assert w not in out, w
    assert out.count("[name withheld]") == 3


def test_a_failed_refresh_keeps_the_good_cached_copy(tmp_path):
    cache = _script("build_fulltext_cache")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    c = tmp_path / "c"
    c.mkdir()
    prior = {"status": "ok", "pages": 10, "text_pages": 4, "chars": 99, "fetched": "2026-09-01"}
    (c / "manifest.json").write_text(json.dumps({fts.doc_key(DRIVE): prior}))

    class Boom:
        def fetch(self, key, entry, dest):
            raise ConnectionError("x")

    cache.run(str(index), str(c), 10, fetcher=Boom())
    m = json.loads((c / "manifest.json").read_text())[fts.doc_key(DRIVE)]
    assert m["status"] == "ok" and m["pages"] == 10 and m["text_pages"] == 4
    assert m["last_refresh_error"] == "ConnectionError" and m["fetched"] != "2026-09-01"


def test_one_unreadable_drive_file_does_not_switch_oauth_off(tmp_path, monkeypatch):
    cache = _script("build_fulltext_cache")
    import archive_client as ac

    calls = []
    monkeypatch.setattr(ac, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(ac, "oauth_drive_service", lambda: object())

    def dl(_svc, file_id, dest):
        calls.append(file_id)
        if file_id == "bad" * 4:
            raise PermissionError("not created by this app")
        with open(dest, "wb") as f:
            f.write(b"%PDF-1.7 x")

    monkeypatch.setattr(ac, "download_file", dl)
    monkeypatch.setattr(cache, "public_drive_download", lambda fid, dest: open(dest, "wb").write(b"%PDF-1.7"))
    f = cache.Fetcher()
    f.fetch("drive_" + "bad" * 4, {}, str(tmp_path / "1.pdf"))
    f.fetch("drive_" + "good" * 3, {}, str(tmp_path / "2.pdf"))
    assert calls == ["bad" * 4, "good" * 3]      # OAuth still used for the second file
    assert f._drive_ok is True


def test_oauth_download_must_be_a_pdf(tmp_path):
    cache = _script("build_fulltext_cache")
    p = tmp_path / "x.pdf"
    p.write_bytes(b"<html>quota</html>")
    with pytest.raises(ValueError):
        cache.check_pdf(str(p))


def test_year_rule_matches_search_js():
    assert fts.entry_year({"date": "2023/05/01"}) == "2023"


def test_cache_keeps_full_uncapped_text_so_late_names_are_still_phrases(tmp_path, monkeypatch):
    # The cache must hold the full text (adjacency intact): a name listed AFTER
    # caching is then withheld as a phrase at index time, wherever it appears.
    cache = _script("build_fulltext_cache")
    monkeypatch.setattr(name_check, "KNOWN_NAMES", [])
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [])
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    long_text = ("filler " * 20000) + "signed Jane Doe for the operator"

    class Ok:
        def fetch(self, key, entry, dest):
            open(dest, "wb").close()

    cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Ok(), extract=lambda _p: [long_text])
    cached = (tmp_path / "c" / (fts.doc_key(DRIVE) + ".txt")).read_text()
    assert "Jane Doe for the operator" in cached          # not de-duplicated / capped
    names = ["Jane Doe"]
    monkeypatch.setattr(name_check, "KNOWN_NAMES", names)
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re(n), n) for n in names])
    rec = fts.build_record(_entry(), cached, cap=10 ** 7)
    assert "for the operator" in rec["content"]
    assert "jane" not in rec["content"].lower() and "doe" not in rec["content"].lower()


def test_common_words_in_listed_names_stay_searchable(monkeypatch):
    # "Mark Johnson" is listed, but "Johnson Creek" (a real drain) and the verb
    # "mark" must stay searchable anywhere in a long document.
    names = ["Mark Johnson"]
    monkeypatch.setattr(name_check, "KNOWN_NAMES", names)
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re(n), n) for n in names])
    out = fts.prepare_content("Johnson Creek outfall; mark the well; Mark Johnson signed.")
    assert "Johnson" in out and "Creek" in out and "mark" in out
    assert "Mark Johnson" not in out


def test_refresh_skips_blank_pages_and_stops_after_three():
    from datetime import date

    cache = _script("build_fulltext_cache")
    today = date(2026, 11, 2)
    old = "2026-09-01"
    assert not cache._needs_refresh({"status": "ok", "pages": 12, "text_pages": 11, "fetched": old}, today)
    assert not cache._needs_refresh({"status": "ok", "pages": 100, "text_pages": 95, "fetched": old}, today)
    assert cache._needs_refresh({"status": "ok", "pages": 100, "text_pages": 60, "fetched": old}, today)
    assert cache._needs_refresh({"status": "no_text", "pages": 2, "text_pages": 0, "fetched": old}, today)
    assert not cache._needs_refresh({"status": "no_text", "pages": 2, "text_pages": 0, "fetched": old,
                                     "refreshes": cache.MAX_REFRESHES}, today)


class _HTTP429(Exception):
    class response:  # requests-style HTTPError shape that retry_policy reads
        status_code = 429


def test_transient_failures_are_not_strikes_and_a_429_stops_the_run(tmp_path):
    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abc"]
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))
    calls = []

    class RateLimited:
        def fetch(self, key, entry, dest):
            calls.append(key)
            raise _HTTP429("slow down")

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=RateLimited())
    assert calls == [fts.doc_key(links[0])]          # stopped after the first 429
    assert counts["stopped_transient"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert m[fts.doc_key(links[0])]["attempts"] == 0   # no strike: retried next run
    assert [k for k, _ in cache.pending(json.loads(index.read_text()), m)][0] == fts.doc_key(links[0])


def test_an_outage_stops_after_a_run_of_connection_errors(tmp_path):
    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abcdefgh"]
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))

    class Down:
        def fetch(self, key, entry, dest):
            raise ConnectionError("network down")

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Down())
    assert counts["transient"] == cache.MAX_CONSECUTIVE_TRANSIENT and counts["stopped_transient"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert all(v["attempts"] == 0 for v in m.values())


def test_drive_html_interstitial_is_transient_and_stops_the_run(tmp_path):
    import retry_policy

    cache = _script("build_fulltext_cache")
    assert retry_policy.is_transient(cache.ThrottledError("quota page"))


def test_nsite_429_wrapped_in_runtimeerror_is_detected():
    cache = _script("build_fulltext_cache")
    try:
        try:
            raise _HTTP429("x")
        except _HTTP429 as inner:
            raise RuntimeError("download failed") from inner
    except RuntimeError as e:
        assert cache._status_in_chain(e) == 429


def test_index_and_cache_agree_on_max_attempts():
    assert _script("build_fulltext_index").MAX_ATTEMPTS == _script("build_fulltext_cache").MAX_ATTEMPTS


def test_pending_count(tmp_path):
    idx = _script("build_fulltext_index")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "a": {"status": "ok"}, "b": {"status": "no_text"},
        "c": {"status": "error:X", "attempts": 1}, "d": {"status": "error:X", "attempts": 3}}))
    assert idx.pending_count({"a", "b", "c", "d", "e"}, str(tmp_path)) == 2   # c (retrying) + e (new)


def test_one_unshared_file_cannot_stall_the_fill(tmp_path):
    # Drive's HTML page for ONE document: the run continues past it, and after
    # MAX_THROTTLED_PER_DOC tries it becomes a strike, so it can't block forever.
    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "ab"]
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))
    bad = fts.doc_key(links[0])

    class OneBad:
        def fetch(self, key, entry, dest):
            if key == bad:
                raise cache.ThrottledError("sign-in page")
            open(dest, "wb").close()

    for _ in range(cache.MAX_THROTTLED_PER_DOC):
        counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=OneBad(), extract=lambda _p: ["t"])
        assert counts["stopped_transient"] == 0          # one document never stops the run
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert m[fts.doc_key(links[1])]["status"] == "ok"
    assert m[bad]["throttled"] == cache.MAX_THROTTLED_PER_DOC and m[bad]["attempts"] == 1


def test_html_from_several_documents_stops_the_run(tmp_path):
    cache = _script("build_fulltext_cache")
    links = [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abcdef"]
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))
    calls = []

    class Throttled:
        def fetch(self, key, entry, dest):
            calls.append(key)
            raise cache.ThrottledError("quota page")

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Throttled())
    assert counts["stopped_transient"] == 1 and len(calls) == cache.MAX_THROTTLED_DOCS_PER_RUN


def test_show_more_recovers_from_a_failed_fetch():
    js = open(os.path.join(_REPO, "site", "public-records", "search.js"), encoding="utf-8").read()
    assert "Couldn't load more results" in js and "moreBtn.disabled = false;" in js


def test_secret_name_with_trailing_punctuation_is_withheld(monkeypatch):
    monkeypatch.setenv("REDACT_NAMES", "Jane Doe Jr.")
    out = fts.redact_secret_names("Letter from Jane Doe Jr. wrote back; Doe Jr. again.")
    assert "Jane" not in out and "Doe" not in out


def test_denylist_name_with_trailing_punctuation_is_withheld(monkeypatch):
    names = ["J. Smith Jr."]
    monkeypatch.setattr(name_check, "_KNOWN_NAME_RES", [(name_check._word_re(n), n) for n in names])
    out, n = name_check.redact_denylist("Signed J. Smith Jr. today; attestation stays.")
    assert "Smith" not in out and n == 1


def test_fulltext_click_fetch_cap_is_present():
    js = open(os.path.join(_REPO, "site", "public-records", "search.js"), encoding="utf-8").read()
    assert "FULLTEXT_MAX_FETCH_PER_CLICK" in js and "fetchedThisClick < FULLTEXT_MAX_FETCH_PER_CLICK" in js
    assert "no readable text" not in js   # the label never claims all the rest are textless


def test_first_name_marker_takes_the_surname_with_it():
    out, n = name_check.redact_denylist("Complaint from Trisha Smithfield, re odor. Trisha-directed. trisha said ok")
    assert "Smithfield" not in out and "trisha" not in out.lower()
    assert "odor" in out and " said ok" in out      # a lower-case next word is left alone
    assert n == 3


def test_fulltext_toggle_runs_do_not_double_deploy_pages_when_off():
    with open(os.path.join(_REPO, ".github", "workflows", "pages.yml"), encoding="utf-8") as f:
        y = f.read()
    gate = "if: github.event.workflow_run.name != 'fulltext-index' || steps.fulltext.outputs.enabled == 'true'"
    assert y.count(gate) == 3


def test_failed_batch_is_retried_not_skipped_and_old_results_cleared():
    js = open(os.path.join(_REPO, "site", "public-records", "search.js"), encoding="utf-8").read()
    i = js.index("function fetchBatch()")
    body = js[i:js.index("}", js.index("then(function (datas)", i))]
    assert "next += batch.length" in body               # only after the batch loaded
    assert "searchResults.appendChild(moreBtn);   // make the offered retry possible" in js


def test_year_select_is_optional_for_old_cached_pages():
    js = open(os.path.join(_REPO, "site", "public-records", "search.js"), encoding="utf-8").read()
    assert "if (filterYear) {\n      fillSelect(filterYear, collectYears(data));" in js


def test_fulltext_only_names_are_withheld_in_full_text_but_never_touch_the_feed(monkeypatch):
    import findings_feed

    monkeypatch.delenv("REDACT_NAMES", raising=False)
    monkeypatch.setenv("FULLTEXT_REDACT_NAMES", "Pat Quigley Doe")
    out = fts.redact_secret_names("Pat Quigley Doe filed it; Doe later; Quigley Doe too.")
    assert "Quigley" not in out and "Doe" not in out
    # The feed's redactor is built from REDACT_NAMES only, so these names can
    # never be relabelled "an EGLE inspector" on the public feed.
    assert findings_feed._build_name_redactor() is None


def test_workflow_passes_the_fulltext_only_secret_to_both_steps():
    with open(os.path.join(_REPO, ".github", "workflows", "fulltext-index.yml"), encoding="utf-8") as f:
        assert f.read().count("FULLTEXT_REDACT_NAMES: ${{ secrets.FULLTEXT_REDACT_NAMES }}") == 2


# --- content-change refetch (CODER-PLAN step 5, 2026-11-06) ----------------------

def _links(n):
    return [f"https://drive.google.com/file/d/{c * 26}/view" for c in "abcdefgh"[:n]]


def test_a_changed_drive_copy_is_refetched_and_an_unchanged_one_is_not():
    from datetime import date

    cache = _script("build_fulltext_cache")
    links = _links(5)
    entries = [_entry(link=l) for l in links]
    k = [fts.doc_key(l) for l in links]
    old = "2026-09-01"   # past the 30-day timer
    manifest = {
        k[0]: {"status": "ok", "pages": 4, "text_pages": 4, "fetched": old, "sha256": "S0"},    # changed
        k[1]: {"status": "no_text", "pages": 3, "text_pages": 0, "fetched": old, "sha256": "S1"},  # same sha: no timer
        k[2]: {"status": "no_text", "pages": 3, "text_pages": 0, "fetched": old},               # no recorded sha: timer
        k[3]: {"status": "no_text", "pages": 3, "text_pages": 0, "fetched": old, "sha256": "S3"},  # no live sha: timer
        k[4]: {"status": "ok", "pages": 4, "text_pages": 4, "fetched": old, "sha256": "S4",
               "change_errors": cache.MAX_CHANGE_ERRORS},                                       # changed, struck out
    }
    live = {k[0]: "NEW", k[1]: "S1", k[2]: "X", k[4]: "NEW"}
    got = [key for key, _ in cache.pending(entries, manifest, today=date(2026, 11, 6), live=live)]
    assert got == [k[0], k[2], k[3]]


def test_live_lookup_asks_only_for_cached_drive_copies_with_a_recorded_sha():
    cache = _script("build_fulltext_cache")
    links = _links(3)
    entries = [_entry(link=l) for l in links] + [_entry(link=NSITE)]
    k = [fts.doc_key(l) for l in links]
    manifest = {k[0]: {"status": "ok", "sha256": "S"}, k[1]: {"status": "ok"},
                k[2]: {"status": "error:X", "sha256": "S"},
                fts.doc_key(NSITE): {"status": "ok", "sha256": "S"}}
    assert cache.live_lookup_keys(entries, manifest) == [k[0]]


def test_run_records_sha_and_refetches_only_on_a_live_change(tmp_path):
    import hashlib

    cache = _script("build_fulltext_cache")
    links = _links(2)
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry(link=l) for l in links]))
    k = [fts.doc_key(l) for l in links]
    pdf_bytes = _make_text_pdf("AHWW312R 158.3 F")

    class F:
        def __init__(self, live):
            self.live, self.fetched, self.asked = live, [], None

        def live_sha256(self, keys):
            self.asked = list(keys)
            return {key: self.live[key] for key in keys if key in self.live}

        def fetch(self, key, entry, dest):
            self.fetched.append(key)
            with open(dest, "wb") as f:
                f.write(pdf_bytes)

    c = str(tmp_path / "c")
    first = F({})
    cache.run(str(index), c, 10, fetcher=first)
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    assert first.fetched == k and all(m[key]["sha256"] == sha for key in k)

    second = F({k[0]: sha, k[1]: "changed-on-drive"})
    counts = cache.run(str(index), c, 10, fetcher=second)
    assert second.asked == k and second.fetched == [k[1]]
    assert counts["live_lookups"] == 2 and counts["live_found"] == 2 and counts["live_changed"] == 1


def test_a_failed_refetch_after_a_change_strikes_change_errors_not_refreshes(tmp_path):
    cache = _script("build_fulltext_cache")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    c = tmp_path / "c"
    c.mkdir()
    prior = {"status": "ok", "pages": 2, "text_pages": 2, "chars": 9, "fetched": "2026-11-01",
             "sha256": "OLD", "refreshes": 1}
    (c / "manifest.json").write_text(json.dumps({fts.doc_key(DRIVE): prior}))

    class Boom:
        def live_sha256(self, keys):
            return {key: "NEW" for key in keys}

        def fetch(self, key, entry, dest):
            raise ValueError("not a PDF")

    cache.run(str(index), str(c), 10, fetcher=Boom())
    m = json.loads((c / "manifest.json").read_text())[fts.doc_key(DRIVE)]
    assert m["status"] == "ok" and m["sha256"] == "OLD"        # good copy kept, still due
    assert m["change_errors"] == 1 and m["refreshes"] == 1


def test_a_failing_live_lookup_falls_back_to_the_timer_rule(tmp_path):
    cache = _script("build_fulltext_cache")
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    c = tmp_path / "c"
    c.mkdir()
    prior = {"status": "no_text", "pages": 3, "text_pages": 0, "chars": 0, "fetched": "2026-09-01",
             "sha256": "S"}
    (c / "manifest.json").write_text(json.dumps({fts.doc_key(DRIVE): prior}))
    fetched = []

    class F:
        def live_sha256(self, keys):
            raise RuntimeError("drive down")

        def fetch(self, key, entry, dest):
            fetched.append(key)
            with open(dest, "wb") as f:
                f.write(_make_text_pdf("now searchable"))

    counts = cache.run(str(index), str(c), 10, fetcher=F())
    assert fetched == [fts.doc_key(DRIVE)] and counts["live_found"] == 0   # timer said due


def test_fetcher_live_sha_without_oauth_is_empty(monkeypatch):
    cache = _script("build_fulltext_cache")
    import archive_client as ac

    monkeypatch.setattr(ac, "is_configured", lambda *a, **k: False)
    f = cache.Fetcher()
    assert f.live_sha256(["drive_" + "a" * 26]) == {} and f._drive_ok is False


def _cached(tmp_path, prior):
    index = tmp_path / "search-index.json"
    index.write_text(json.dumps([_entry()]))
    c = tmp_path / "c"
    c.mkdir(exist_ok=True)
    (c / "manifest.json").write_text(json.dumps({fts.doc_key(DRIVE): prior}))
    return str(index), c


def test_a_changed_copy_that_keeps_getting_html_strikes_out(tmp_path):
    cache = _script("build_fulltext_cache")
    index, c = _cached(tmp_path, {"status": "ok", "pages": 2, "text_pages": 2, "chars": 9,
                                  "fetched": "2026-11-01", "sha256": "OLD"})

    class Html:
        n = 0

        def live_sha256(self, keys):
            return {key: "NEW" for key in keys}

        def fetch(self, key, entry, dest):
            Html.n += 1
            raise cache.ThrottledError("html")

    for _ in range(cache.MAX_THROTTLED_PER_DOC + cache.MAX_CHANGE_ERRORS + 3):
        cache.run(index, str(c), 10, fetcher=Html())
    m = json.loads((c / "manifest.json").read_text())[fts.doc_key(DRIVE)]
    assert m["change_errors"] >= cache.MAX_CHANGE_ERRORS
    assert Html.n < cache.MAX_THROTTLED_PER_DOC + cache.MAX_CHANGE_ERRORS + 3   # stopped refetching


def test_a_struck_out_change_failing_on_the_timer_counts_as_a_refresh(tmp_path):
    cache = _script("build_fulltext_cache")
    index, c = _cached(tmp_path, {"status": "no_text", "pages": 3, "text_pages": 0, "chars": 0,
                                  "fetched": "2026-09-01", "sha256": "OLD", "refreshes": 0,
                                  "change_errors": cache.MAX_CHANGE_ERRORS})

    class Boom:
        def live_sha256(self, keys):
            return {key: "NEW" for key in keys}

        def fetch(self, key, entry, dest):
            raise ValueError("bad")

    cache.run(index, str(c), 10, fetcher=Boom())   # queued by the 30-day timer
    m = json.loads((c / "manifest.json").read_text())[fts.doc_key(DRIVE)]
    assert m["refreshes"] == 1 and m["change_errors"] == cache.MAX_CHANGE_ERRORS


def test_a_fetch_that_never_matches_drive_strikes_instead_of_looping(tmp_path):
    cache = _script("build_fulltext_cache")
    index, c = _cached(tmp_path, {"status": "ok", "pages": 1, "text_pages": 1, "chars": 9,
                                  "fetched": "2026-11-01", "sha256": "OLD"})
    pdf = _make_text_pdf("text")
    fetched = []

    class Odd:
        def live_sha256(self, keys):
            return {key: "DRIVE-SAYS-SOMETHING-ELSE" for key in keys}

        def fetch(self, key, entry, dest):
            fetched.append(key)
            with open(dest, "wb") as f:
                f.write(pdf)

    for _ in range(cache.MAX_CHANGE_ERRORS + 2):
        cache.run(index, str(c), 10, fetcher=Odd())
    assert len(fetched) == cache.MAX_CHANGE_ERRORS
