#!/usr/bin/env bash
# privacy_scan.sh — pre-publication privacy gate for a PUBLIC repo (local, hook-driven).
#
# Fails (exit 1) if any configured privacy term appears in content being pushed.
# The terms are NEVER stored in this repo — that would publish exactly the
# identifiers we're protecting. They come from ~/.config/privacy-gate/terms.txt
# (one term per line), or the PRIVACY_TERMS env var (override, used by tests).
# Install the hook with scripts/install-hooks.sh.
#
# Fail-closed: if no terms are configured this EXITS NON-ZERO rather than
# silently passing — a privacy gate that no-ops is worse than none.
#
# Checks performed:
#   1. TEXT   — git grep every scanned commit's tree for the terms (skips
#               binaries automatically).
#   2. PDFs   — for every PDF in the scanned commits: if it has NO extractable
#               text layer it is NON-SEARCHABLE and is BLOCKED (its image content
#               can't be verified — run `ocrmypdf` on it first, which adds a text
#               layer and then exposes any scanned-in names to check #2's grep);
#               if it IS searchable, its extracted text is grepped for the terms.
#
# Modes:
#   privacy_scan.sh                 -> scan the HEAD tree (single snapshot)
#   privacy_scan.sh <ref>           -> scan <ref>'s tree (single snapshot)
#   privacy_scan.sh --range <args>  -> scan EVERY commit `git rev-list <args>`
#                                      returns (catches a term added in one commit
#                                      and removed in a later commit of the same
#                                      push — a tip-only scan would miss it, but
#                                      the blob would stay in public history).
#
# Scope: SKIPS committed data files (*.csv/*.xlsx/*.xls) for the text grep —
# public datasets carry unrelated real names by design. Text matching is
# fixed-string (-F), case-insensitive (-i), so a configured phrase matches only
# exactly — it will not match a longer phrase that merely contains it.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

# ---- resolve terms out-of-band (never from the repo tree) ----
terms_raw=""
if [ -n "${PRIVACY_TERMS:-}" ]; then
  terms_raw="$PRIVACY_TERMS"
elif [ -f "$HOME/.config/privacy-gate/terms.txt" ]; then
  terms_raw="$(cat "$HOME/.config/privacy-gate/terms.txt")"
else
  echo "❌ privacy_scan: no terms configured." >&2
  echo "   Create ~/.config/privacy-gate/terms.txt (one term per line)." >&2
  exit 2
fi

eargs=()
while IFS= read -r line; do
  line="${line%$'\r'}"
  [ -z "${line// /}" ] && continue
  case "$line" in \#*) continue ;; esac
  eargs+=( -e "$line" )
done <<< "$terms_raw"

if [ "${#eargs[@]}" -eq 0 ]; then
  echo "❌ privacy_scan: terms configured but empty after parsing." >&2
  exit 2
fi

# ---- which commit trees to scan ----
commits=()
if [ "${1:-}" = "--range" ]; then
  shift
  [ "$#" -eq 0 ] && { echo "❌ privacy_scan: --range needs rev-list arguments." >&2; exit 2; }
  while IFS= read -r c; do [ -n "$c" ] && commits+=( "$c" ); done < <(git rev-list "$@" 2>/dev/null)
  if [ "${#commits[@]}" -eq 0 ]; then
    echo "✅ privacy gate passed — no new commits in range to scan."
    exit 0
  fi
else
  commits=( "${1:-HEAD}" )
fi

# ---- check #1: text grep each commit's tree ----
text_hits=""
grep_err=0
for c in "${commits[@]}"; do
  set +e
  h="$(git grep -I -i -F -n "${eargs[@]}" "$c" -- \
       '.' ':(exclude)*.csv' ':(exclude)*.xlsx' ':(exclude)*.xls')"
  s=$?
  set +e
  [ "$s" -gt 1 ] && grep_err=1
  [ "$s" -eq 0 ] && text_hits+="${h}"$'\n'
done
[ "$grep_err" -ne 0 ] && { echo "❌ privacy_scan: git grep failed on one or more commits." >&2; exit 2; }

# ---- check #2: PDFs (searchability + text-layer grep), deduped by blob ----
pdf_issues=""
have_pdftotext=1
command -v pdftotext >/dev/null 2>&1 || have_pdftotext=0
declare -A pdf_seen
for c in "${commits[@]}"; do
  while IFS=$'\t' read -r meta path; do
    case "$path" in *.pdf|*.PDF) : ;; *) continue ;; esac
    blob="$(printf '%s' "$meta" | awk '{print $3}')"
    [ -n "${pdf_seen[$blob]:-}" ] && continue
    pdf_seen[$blob]=1
    if [ "$have_pdftotext" -eq 0 ]; then
      pdf_issues+="  [cannot verify PDF — pdftotext not installed] $path"$'\n'
      continue
    fi
    tmp="$(mktemp)"
    git cat-file blob "$blob" > "$tmp" 2>/dev/null
    txt="$(pdftotext -q "$tmp" - 2>/dev/null || true)"
    rm -f "$tmp"
    if [ -z "${txt//[[:space:]]/}" ]; then
      pdf_issues+="  [NON-SEARCHABLE PDF — no text layer, cannot verify] $path"$'\n'
    elif printf '%s' "$txt" | grep -i -F -q "${eargs[@]}"; then
      pdf_issues+="  [PRIVACY TERM found in PDF text layer] $path"$'\n'
    fi
  done < <(git ls-tree -r "$c")
done

# ---- verdict ----
fail=0
if printf '%s\n' "$text_hits" | grep -q .; then
  echo "❌ PRIVACY GATE FAILED — configured term(s) in tracked text (commit:path:line):" >&2
  printf '%s\n' "$text_hits" | grep . >&2
  fail=1
fi
if [ -n "$pdf_issues" ]; then
  echo "❌ PRIVACY GATE FAILED — PDF check:" >&2
  printf '%s' "$pdf_issues" >&2
  echo "   Non-searchable PDFs: make searchable first ('ocrmypdf in.pdf out.pdf', or" >&2
  echo "   vision-ocr for higher accuracy on hard scans) then re-check." >&2
  fail=1
fi
if [ "$fail" -ne 0 ]; then
  echo "" >&2
  echo "This repo is PUBLIC — a leak cannot be un-published. Remove/fix before pushing." >&2
  exit 1
fi

echo "✅ privacy gate passed (${#commits[@]} commit(s) scanned) — no terms in text or PDFs."
exit 0
