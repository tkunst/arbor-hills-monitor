# Overnight-coder handoff: coder:mienviro-referrer-fix

*Copied verbatim from the Lotext overnight-coder queue (`documents/overnight-coder-queue.md`, 2026-11-08) as this PR's first commit.*

### coder:mienviro-referrer-fix -- MiEnviro links on arborhillsmonitor.org return 401 for visitors; add a no-referrer policy site-wide -- Trisha-directed 2026-11-08 (AH comms #267, Dave's report)

- **Repo:** `arbor-hills-monitor` (site/ → arborhillsmonitor.org). Branch `mienviro-referrer-fix` off `main`.
- **Readiness: READY.**
- **Evidence (2026-11-08 curl):** `https://mienviro.michigan.gov/ncore/downloadpdf/-3909858558664182473` returns 200 with no Referer, 401 with `Referer: https://arborhillsmonitor.org/` (the browser default for cross-site links). The site sets no referrer policy.
- **Goal box:**
  > Follow docs/overnight-coder.md. Goal: MiEnviro download links on the public site fail with a 401 for visitors because browsers send `Referer: https://arborhillsmonitor.org/`. Add `<meta name="referrer" content="no-referrer">` to every page the site serves (fix the generators/templates that write site/, not just the built HTML, so it survives rebuilds; include site/methane-migration/, site/public-records/ pages and any other page linking mienviro.michigan.gov). Add a test that fails if any generated page lacks the meta tag. Verify with curl that a MiEnviro link returns 200 with no Referer. Branch mienviro-referrer-fix off main; open a PR and stop. Do NOT merge or push to main; publishing needs Trisha's go.

