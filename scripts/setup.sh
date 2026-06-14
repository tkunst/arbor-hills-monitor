#!/usr/bin/env bash
# setup.sh — fresh clone -> runnable in under 10 minutes.
# Creates a venv, installs deps, and checks for the OCR binary + credentials.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "==> Creating virtualenv (.venv) with $PY"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "==> Checking for ocrmypdf (needed only to OCR image-only PDFs)"
if command -v ocrmypdf >/dev/null 2>&1; then
  echo "    ocrmypdf: $(command -v ocrmypdf)"
else
  echo "    ocrmypdf NOT found. Install it:"
  echo "      macOS:  brew install ocrmypdf"
  echo "      Debian: sudo apt-get install -y ocrmypdf"
fi

echo "==> Running the test suite (hermetic — no credentials needed)"
pytest -q

echo
echo "==> Setup complete."
echo "    To run against Drive/Sheets/email you also need credentials:"
echo "      cp .env.example .env   # then fill it in"
echo "      see scripts/setup_gcp.md for the GCP service account steps"
