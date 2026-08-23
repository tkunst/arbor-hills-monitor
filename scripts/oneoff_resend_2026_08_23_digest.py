"""One-off corrective re-send of the 2026-08-23 Sunday digest with fixed links.

This morning's digest (10 documents) was sent before the referer-rejection
fix (ADR 007's 2026-08-23 addendum) merged, so its links use the raw nSITE
form. All 10 documents already have real Drive mirrors (from the ordinary
nightly archive.yml catch-up). This script re-sends the same 10 items with
those Drive links substituted, plus an apology, to the exact same audience
send_digest() would have used.

Delete this file (and the temporary workflow that runs it) once it's sent —
this is a one-time correction, not a capability the repo carries forward.
"""
import email_alerts as ea
from config_loader import load_config

SUBJECT = "Arbor Hills N2688 digest — corrected links (re-send of this morning's email)"

BODY = """\
This morning's Arbor Hills (N2688) digest went out with document links that \
can show a "Server Error" page instead of opening for some recipients — a \
bug in how the links were built, not a problem with any of the documents \
themselves. It's fixed going forward. This is the same 10 documents from \
this morning's digest, re-sent with corrected links. Apologies for the \
duplicate email.

ACTION ITEMS (deadlines / notices):

- 2026-08-20  Submission PDF
    No discharge occurred May-July 2026; no PFAS sampling was conducted during the reporting period.
    https://drive.google.com/file/d/1IzsU7Q3hCZOmjwcjmehTdZRnmsbh05J_/view?usp=drivesdk

- 2026-08-20  nForm Document
    August 2025 bat acoustic survey indicated probable absence of Indiana and Northern Long-eared Bats in project area; closest Mitchell's Satyr Butterfly population over 25 air miles away; closest Eastern Massasauga Rattlesnake occurrences over 3-7 air miles away with significant development barriers.
    https://drive.google.com/file/d/1Lx2uoSRHon081_1elzKPZI-e-f06Hcyl/view?usp=drivesdk

- 2026-08-20  nForm Document
    Expansion area mitigation totals 64.49 acres across 11 tiers; Tier 9 is the largest at 16.93 acres with base elevation 949 ft and top-of-berm elevation 950.9 ft.
    https://drive.google.com/file/d/1S9OpwOnqptaXn7KQxR3yG1q86y6Gyy9r/view?usp=drivesdk

- 2026-08-20  nForm Document
    No specific factual measurements or violations reported; this is a delineation methodology and site-characterization document without quantitative environmental readings.
    https://drive.google.com/file/d/1cux9P1wH5UuKzBRf3daJjJq8QRVTkh8S/view?usp=drivesdk

- 2026-08-20  nForm Document
    No specific factual measurements or regulatory violations stated; this is a technical drawing showing proposed expansion infrastructure and wetland impact areas.
    https://drive.google.com/file/d/1XgCmrbiogDL__jSDwBuqHP3GnrdHucQo/view?usp=drivesdk

- 2026-08-20  nForm Document
    Approximately 33.29 acres of regulated wetland impact (26.61 acres of forested wetland, 6.68 acres of emergent wetland) and 3.90 acres of non-regulated ponds/waterbodies; proposed mitigation at 1:1.5 to 2:1 ratios totaling 64.49 acres off-site within the same watershed.
    https://drive.google.com/file/d/1lBgc2zBaZUxVYCayE0D95oiqsEM_84O3/view?usp=drivesdk

- 2026-08-20  Submission PDF
    Arbor Hills Landfill expansion project will impact approximately 33.29 acres of regulated wetland; facility receives 2 million cubic yards waste annually and has approximately 3 years of remaining permitted airspace.
    https://drive.google.com/file/d/1Ji_400xMk_bWmLgsnmf8OzEiUGOCUFhY/view?usp=drivesdk

- 2026-08-21  nForm Document
    No specific factual readings or violation data. This is a mitigation design document; it contains planned acreages (3.78 acres impacted, 6.49 acres to be created) and soil/hydrologic specifications, but no measured environmental data or regulatory violations.
    https://drive.google.com/file/d/1qfgw21jIeVww--fFkHxWntwHPLNH11FS/view?usp=drivesdk

- 2026-08-21  Submission PDF
    3.78 acres permanent wetland impact (2.14 acres PEM, 1.64 acres PFO) to eliminate PFAS source; mitigation ratio 1.5:1 (PEM) and 2.0:1 (PFO) for 6.49 acres creation off-site
    https://drive.google.com/file/d/18QuJxAc6qH1m0adfY_JeNQcP33xJ1Fz0/view?usp=drivesdk

OTHER NEW DOCUMENTS:

- 2026-07-29  [evidence/notable/R2]  Test Observation (07/29/2026)
    Required stack test 20 months overdue (due Dec 2024, conducted July 29, 2026); Violation Notice issued July 10, 2026; Flare 391 measured at 1693F, Flare 392 at 1474F.
    https://drive.google.com/file/d/10Xsz8qf0cNGvvRHhyOV5hNAwbdmPWlvw/view?usp=drivesdk

This is an automated monitor of Arbor Hills Landfill (N2688) regulatory filings.
"""


def run() -> int:
    cfg = load_config()
    recipients = ea.merge_extra_recipients(ea.resolve_recipients(cfg), "DIGEST_RECIPIENTS_EXTRA")
    sent = ea.send_email(SUBJECT, BODY, cfg, recipients=recipients)
    print(f"[oneoff-resend] sent={sent} recipients={len(recipients)}")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(run())
