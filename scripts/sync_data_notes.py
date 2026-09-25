#!/usr/bin/env python3
"""Keep a data-quality note identical everywhere it is published.

The note lives in ONE file, data-notes/<id>.yml. This script copies it into:

* site pages, between marker comments
      <!-- dq-note:<id>:<variant> --> ... <!-- /dq-note:<id> -->
  variant "callout" -> <p class="data-quality-note">{html}</p>
  variant "footer"  -> {footer_html}           (bare text, for inside an existing <p>)
* FAQ structured data: any JSON-LD Question whose visible answer (<div class="faq">)
  contains a marker gets its acceptedAnswer text rebuilt from that visible answer, so
  search engines see the same words as readers;
* the GitHub release notes and the README inside the release zip, between the HTML
  comments  <!-- dq-note:<id> --> ... <!-- /dq-note:<id> -->  (invisible when rendered).

Usage:
    python scripts/sync_data_notes.py --pages      # rewrite site files in place
    python scripts/sync_data_notes.py --check      # exit 1 if any site file is out of sync
    python scripts/sync_data_notes.py --release [--backup-dir DIR] [--dry-run]
    python scripts/sync_data_notes.py --all        # --pages then --release

The release step re-packs the zip with only README.md changed and refuses to upload
unless every member listed under release.zip_member_md5 is byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = ROOT / "data-notes"
SITE = ROOT / "site"

MARK_RE = re.compile(
    r"(<!-- dq-note:(?P<id>[\w-]+):(?P<variant>[\w-]+) -->)(?P<body>.*?)(<!-- /dq-note:(?P=id) -->)",
    re.S,
)


def load_notes() -> dict[str, dict]:
    notes = {}
    for p in sorted(NOTES_DIR.glob("*.yml")):
        n = yaml.safe_load(p.read_text(encoding="utf-8"))
        notes[n["id"]] = n
    return notes


def render(note: dict, variant: str) -> str:
    if variant == "callout":
        return f'<p class="data-quality-note">{note["html"].strip()}</p>'
    if variant == "footer":
        return note["footer_html"].strip()
    raise ValueError(f"unknown dq-note variant {variant!r}")


def plain_text(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace-collapsed, entities decoded."""
    text = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmlmod.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:)])", r"\1", text)


def apply_markers(src: str, notes: dict) -> str:
    def sub(m: re.Match) -> str:
        note = notes.get(m.group("id"))
        if note is None:
            raise KeyError(f"no data-notes/{m.group('id')}.yml for marker")
        return f"{m.group(1)}{render(note, m.group('variant'))}{m.group(5)}"

    return MARK_RE.sub(sub, src)


FAQ_DIV_RE = re.compile(r'<div class="faq">\s*<h2>(?P<q>.*?)</h2>(?P<body>.*?)</div>', re.S)


def sync_faq_jsonld(src: str) -> str:
    """Rebuild acceptedAnswer text for FAQ questions whose visible answer holds a marker."""
    answers = {}
    for m in FAQ_DIV_RE.finditer(src):
        if "<!-- dq-note:" not in m.group("body"):
            continue
        paras = re.findall(r"<p[^>]*>(.*?)</p>", m.group("body"), re.S)
        answers[plain_text(m.group("q"))] = " ".join(plain_text(p) for p in paras)
    if not answers:
        return src
    out, seen = [], set()
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith('{"@type":"Question"'):
            comma = stripped.endswith(",")
            obj = json.loads(stripped.rstrip(","))
            name = plain_text(obj["name"])
            if name in answers:
                obj["acceptedAnswer"]["text"] = answers[name]
                seen.add(name)
                indent = line[: len(line) - len(line.lstrip())]
                line = indent + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ("," if comma else "")
        out.append(line)
    missing = set(answers) - seen
    if missing:
        raise ValueError(f"FAQ JSON-LD has no Question named {sorted(missing)}; keep the <h2> and the JSON-LD name identical")
    return "\n".join(out)


def site_files() -> list[Path]:
    return sorted(p for p in SITE.rglob("*.html") if "<!-- dq-note:" in p.read_text(encoding="utf-8"))


def synced(src: str, notes: dict) -> str:
    return sync_faq_jsonld(apply_markers(src, notes))


def do_pages(notes: dict, check: bool) -> int:
    drift = []
    for p in site_files():
        src = p.read_text(encoding="utf-8")
        new = synced(src, notes)
        if new != src:
            drift.append(p.relative_to(ROOT))
            if not check:
                p.write_text(new, encoding="utf-8")
    verb = "out of sync" if check else "updated"
    for d in drift:
        print(f"{verb}: {d}")
    if not drift:
        print("site pages: all in sync")
    return 1 if (check and drift) else 0


# ------------------------------------------------------------------ release


def replace_md_block(text: str, note: dict) -> str:
    start, end = f"<!-- dq-note:{note['id']} -->", f"<!-- /dq-note:{note['id']} -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"expected exactly one {start} ... {end} block")
    a, b = text.index(start) + len(start), text.index(end)
    return text[:a] + "\n" + note["release_md"].strip() + "\n" + text[b:]


def gh(*args: str, capture: bool = True) -> str:
    return subprocess.run(["gh", *args], check=True, text=True, capture_output=capture).stdout


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def repack_zip(zbytes: bytes, readme_path: str, note: dict, guards: dict) -> tuple[bytes, bool]:
    zin = zipfile.ZipFile(io.BytesIO(zbytes))
    readme = zin.read(readme_path).decode("utf-8")
    new_readme = replace_md_block(readme, note)
    if new_readme == readme:
        return zbytes, False
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == readme_path:
                data = new_readme.encode("utf-8")
            zout.writestr(info, data, compress_type=info.compress_type)
    out = buf.getvalue()
    check = zipfile.ZipFile(io.BytesIO(out))
    for member, want in guards.items():
        got = md5(check.read(member))
        if got != want:
            raise RuntimeError(f"ABORT: {member} MD5 {got} != published {want}")
    if check.read(readme_path).decode("utf-8") != new_readme:
        raise RuntimeError("ABORT: README round-trip mismatch")
    return out, True


def do_release(note: dict, backup_dir: Path | None, dry_run: bool) -> int:
    rel = note["release"]
    tag, repo = rel["tag"], rel["repo"]
    body = json.loads(gh("release", "view", tag, "-R", repo, "--json", "body"))["body"]
    new_body = replace_md_block(body, note)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        gh("release", "download", tag, "-R", repo, "-p", rel["zip_asset"], "-D", str(td))
        zpath = td / rel["zip_asset"]
        zbytes = zpath.read_bytes()
        new_zip, zip_changed = repack_zip(zbytes, rel["zip_readme_path"], note, rel["zip_member_md5"])
        if backup_dir and (zip_changed or new_body != body):
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{tag}-body.md").write_text(body, encoding="utf-8")
            (backup_dir / rel["zip_asset"]).write_bytes(zbytes)
            print(f"backed up original body + zip to {backup_dir}")
        if new_body != body:
            print(f"release notes: {'would update' if dry_run else 'updating'} {tag}")
            if not dry_run:
                nf = td / "body.md"
                nf.write_text(new_body, encoding="utf-8")
                gh("release", "edit", tag, "-R", repo, "--notes-file", str(nf))
        else:
            print("release notes: in sync")
        if zip_changed:
            print(f"zip README: {'would re-upload' if dry_run else 're-uploading'} {rel['zip_asset']} (CSV MD5s verified identical)")
            if not dry_run:
                zpath.write_bytes(new_zip)
                gh("release", "upload", tag, str(zpath), "-R", repo, "--clobber")
        else:
            print("zip README: in sync")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--backup-dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    notes = load_notes()
    rc = 0
    if a.check:
        rc |= do_pages(notes, check=True)
    if a.pages or a.all:
        rc |= do_pages(notes, check=False)
    if a.release or a.all:
        for n in notes.values():
            if n.get("release"):
                rc |= do_release(n, a.backup_dir, a.dry_run)
    if not (a.check or a.pages or a.release or a.all):
        ap.print_help()
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
