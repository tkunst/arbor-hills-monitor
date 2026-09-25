"""Guards for scripts/sync_data_notes.py (hermetic: no network, no `gh`).

The data-quality note in data-notes/*.yml is published on several site pages. These
tests fail CI if a page drifts from the YAML, so a note edited in one place can never
silently disagree with the others. Fix a failure with:
    .venv/bin/python scripts/sync_data_notes.py --pages
"""
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_data_notes", ROOT / "scripts" / "sync_data_notes.py")
sdn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sdn)


def test_site_pages_match_data_notes():
    notes = sdn.load_notes()
    drift = [
        str(p.relative_to(ROOT))
        for p in sdn.site_files()
        if sdn.synced(p.read_text(encoding="utf-8"), notes) != p.read_text(encoding="utf-8")
    ]
    assert not drift, f"out of sync with data-notes/ (run scripts/sync_data_notes.py --pages): {drift}"


def test_every_marker_points_at_a_known_note():
    notes = sdn.load_notes()
    for p in sdn.site_files():
        for m in sdn.MARK_RE.finditer(p.read_text(encoding="utf-8")):
            assert m.group("id") in notes, f"{p}: unknown note id {m.group('id')}"
            sdn.render(notes[m.group("id")], m.group("variant"))  # raises on unknown variant


def test_jsonld_on_marked_pages_still_parses():
    for p in sdn.site_files():
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', p.read_text(encoding="utf-8"), re.S):
            json.loads(block)


def test_faq_jsonld_answer_equals_visible_answer():
    notes = sdn.load_notes()
    note_text = sdn.plain_text(notes["perimeter-h2s"]["html"])
    faq = (ROOT / "site" / "faq" / "index.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', faq, re.S).group(1))
    hits = [q for q in data["mainEntity"] if note_text in q["acceptedAnswer"]["text"]]
    assert len(hits) == 1


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return buf.getvalue()


NOTE = {"id": "t", "release_md": "NEW NOTE"}


def test_repack_zip_changes_only_readme_and_checks_md5():
    readme = "# x\n<!-- dq-note:t -->\nold\n<!-- /dq-note:t -->\nend\n"
    z = _zip({"d/README.md": readme, "d/a.csv": b"1,2\n"})
    guards = {"d/a.csv": sdn.md5(b"1,2\n")}
    out, changed = sdn.repack_zip(z, "d/README.md", NOTE, guards)
    assert changed
    got = zipfile.ZipFile(io.BytesIO(out))
    assert "NEW NOTE" in got.read("d/README.md").decode() and "old" not in got.read("d/README.md").decode()
    assert got.read("d/a.csv") == b"1,2\n"
    assert sdn.repack_zip(out, "d/README.md", NOTE, guards) == (out, False)  # idempotent


def test_repack_zip_aborts_on_md5_mismatch():
    z = _zip({"d/README.md": "<!-- dq-note:t -->x<!-- /dq-note:t -->", "d/a.csv": b"1"})
    with pytest.raises(RuntimeError, match="ABORT"):
        sdn.repack_zip(z, "d/README.md", NOTE, {"d/a.csv": "0" * 32})


def test_replace_md_block_requires_exactly_one_block():
    with pytest.raises(ValueError):
        sdn.replace_md_block("no markers here", NOTE)
