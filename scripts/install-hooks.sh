#!/usr/bin/env bash
# One-time per clone: point git at the committed hooks and check that the local
# privacy term list exists. This file is committed to a PUBLIC repo, so it
# contains NO privacy terms — the terms live only in ~/.config/privacy-gate/terms.txt.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
git -C "$root" config core.hooksPath .githooks
chmod +x "$root/.githooks/pre-push" "$root/scripts/privacy_scan.sh" 2>/dev/null || true

cfg="$HOME/.config/privacy-gate/terms.txt"
if [ -f "$cfg" ]; then
  echo "✅ privacy pre-push hook installed (core.hooksPath=.githooks)."
  echo "   Local term list: $cfg"
else
  echo "⚠️  Hook installed, but local term list is MISSING: $cfg"
  echo "   The pre-push gate is FAIL-CLOSED and will block pushes until it exists."
  echo "   Create it (one term per line). Do NOT commit terms into this public repo:"
  echo "     mkdir -p ~/.config/privacy-gate && \$EDITOR ~/.config/privacy-gate/terms.txt"
fi
