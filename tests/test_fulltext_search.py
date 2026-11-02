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
    monkeypatch.setattr(fts.findings_feed, "redact_names",
                        lambda t: t.replace("Quigley", "a resident") if t else t)
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

def test_prepare_content_caps_and_keeps_distinct_tail_words():
    head = "alpha " * 30
    tail = "AHWW312R 158.3 AHWW312R alpha " + "x" * 50 + " AHW272R4"
    out = fts.prepare_content(head + tail, cap=100)
    assert len(out.split("\n")[0]) <= 100
    tail_words = out.split("\n")[1].split()
    assert tail_words.count("AHWW312R") == 1          # each distinct word once
    assert "AHW272R4" in tail_words and "158.3" in tail_words
    assert "alpha" not in tail_words                   # already in the head
    assert not any(len(w) > fts.TAIL_MAX_WORD_LEN for w in tail_words)


def test_short_text_is_unchanged_apart_from_whitespace():
    assert fts.prepare_content("a  b\n\n\nc") == "a b\nc"


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
            raise TimeoutError("x")

    counts = cache.run(str(index), str(tmp_path / "c"), 10, fetcher=Boom())
    assert counts["errors"] == 1
    m = json.loads((tmp_path / "c" / "manifest.json").read_text())
    assert m[fts.doc_key(DRIVE)] == {"status": "error:TimeoutError", "fetched": m[fts.doc_key(DRIVE)]["fetched"],
                                      "attempts": 1}


def test_index_collect_records_only_published_non_hand_curated(tmp_path):
    idx = _script("build_fulltext_index")
    other = "https://drive.google.com/file/d/2bbbbbbbbbbbbbbbbbbbbbbbbbb/view"
    for link in (DRIVE, other):
        (tmp_path / (fts.doc_key(link) + ".txt")).write_text("text of " + link[-12:])
    entries = [_entry(), _entry(source="x", link=other)]
    records, cov = idx.collect_records(entries, str(tmp_path), 1000)
    assert [r["url"] for r in records] == [DRIVE]
    assert cov == {"documents": 2, "full_text": 1, "eligible": 1}


# --- flag ----------------------------------------------------------------------

def test_flag_ships_off():
    flag = _script("fulltext_flag")
    with open(os.path.join(_REPO, "config.yml"), encoding="utf-8") as f:
        assert flag.enabled(f.read()) is False


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
