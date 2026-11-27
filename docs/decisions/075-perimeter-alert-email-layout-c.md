# ADR 075: Perimeter-air alert emails, layout C — one daily-run screening email, HTML + text

**Status:** accepted and live (2026-11-26; merged after Trisha reviewed a CI test send) · **Stream E** (`gfl_air`) · supersedes ADR 039 §4 (two
emails per run), ADR 039 §2's mandatory-wording list, and ADR 039's 2026-09-11
benchmark-reference addendum. ADR 039's episode engine, thresholds and episode log are
unchanged. Session AHM-AlertsRedesign; mockups and every decision below were reviewed by
Trisha the same day.

## Context

The screening email (ADR 039) was plain text, about 21 KB, mostly fixed legal text, with
the data in the middle. On a daily batch an episode usually opens and closes inside the
same run, so readers got a screening email and a closeout email one second apart about
the same events. Trisha reviewed three layouts built from real feed data (A restyled in
today's order, B data first, C one email per run) and chose C.

## Decision

### 1. One email per daily run

`gfl_air_render.render_run_email` builds ONE screening email per run, sent iff ≥ 1
episode opened or closed (a continuing-only run stays silent, as before). It lists every
episode the run touched: opened (and maybe closed) this run, closed after opening on an
earlier run, and still open at the end of the run (carried over or new). The watcher no
longer fetches ±2-hour context (layout C has no context block).

### 2. Plain text and HTML from one content model

Every sentence of copy lives once in `gfl_air_render.py`; `render_text` and `render_html`
both read it, so the two parts cannot disagree. Sent with `email_alerts.send_email(html=...)`;
the HTML carries `digest_render.FOOTER_SLOT`, so non-owner copies get the ADR 040
unsubscribe footer and owner copies none. Design tokens and the hosted letterhead mark are
shared with the Sunday digest (`digest_render`). Light-only, nested tables, inline styles,
600 px max; long catch-up runs bucket the grid (more than 36 hours: one cell per k hours,
worst state wins) so the HTML stays under `HTML_BUDGET_BYTES` (85 KB, below Gmail's
~102 KB clip that would hide the footer).

### 3. Layout (Trisha, 2026-11-26)

- **Subject** starts with the substance over the benchmark: `[H2S SCREENING ALERT]`,
  `[Methane SCREENING ALERT]`, `[H2S + Methane SCREENING ALERT]`; old data:
  `[HISTORICAL <gas> SCREENING ALERT — NOT A LIVE INCIDENT]`. Then "Arbor Hills perimeter —
  hourly value(s) above CJ benchmark[, then returned below] — stations — ET dates, ET".
- **Lede:** "This N-hour run covered A to B." (N counted from the run's hours), episodes
  opened / returned below / still above, and "Max: X ppb H2S, Y ppm methane."
  (H2S first; "H2S below detection limit" when every H2S reading was BDL).
- **Box at the top:** the title "Automated screening alert — public hourly data" only.
- **Hour grid** per gas with an episode: amber above the benchmark, pale blue at or
  below, lilac suspect sensor, mid grey (#b8bec4) no data; one row per roster station,
  cells aligned by hour. Title "Hourly <gas> by station · Max V". A gas with no episode
  gets "Hourly <gas> by station: not above benchmark · Max V" (or "· all readings below
  detection limit" for H2S, with any station that sent no value named).
- **Episodes:** "MS-4 · methane · peak 87 ppm" with "returned below"/"still above" and
  "duration N hr" boxes; one line: "Above 40 ppm from <date, time> (V). Peak V at <time>.
  Back at or below at <time> (V)." plus a map link. No event ID on the line. The ≥ 5%
  methane flammable-range sentence is kept, only when the peak reaches it.
- **Latest reading per station:** Station · H2S · CH4 · Wind, "As of" under the table.
  A value the source marks BDL shows only "source: BDL"; a suspect sensor shows a lilac
  "suspect sensor" box; no ▲; a station with no rows says "no data at all this run (no
  H2S, methane or wind); last reading <time>" (the time only when it predates the batch).
- **How to read this alert**, in order: Benchmarks (the two CJ action levels as bullets;
  "flagged only when its raw, unrounded value is strictly above"), H2S health benchmarks
  (EGLE ITSL 72 ppb 24-h and 750 ppb 15-min; California OEHHA acute REL 42 µg/m³ ≈ 30 ppb
  1-h; ATSDR acute 70 ppb and intermediate 20 ppb MRLs; EPA IRIS RfC 2 µg/m³ ≈ 1.4 ppb),
  the CARB 30 ppb odor standard on its own line, "Methane has no EGLE health screening
  level", What this alert is and is not, Event details, Where each level is measured,
  Record and timing (incl. detection limits), and a suspect-sensor footnote.

### 4. Copy and legal framing (Trisha's decisions, 2026-11-26)

Kept: "NOT a formal determination that a Consent Judgment action level was exceeded";
"does NOT establish that AHL exceeded, or failed to correct, anything"; AHL (the CJ
defendant), not the operator GFL; 15-minute rolling averages of ~1-minute data vs hourly
public values; the feed does not document how an hourly value is calculated; only AHL's
nonpublic data and ¶6.3 log can show a 15-minute exceedance; "returned below" says nothing
about compliance or correction; strictly above on the raw value; no-data markers never
flagged; daily batch; the episode-log note; HISTORICAL tag; the flammability rule.

Removed or changed by Trisha:

- The "LEAD for EGLE … verification against the required 15-minute rolling-average data is
  necessary" and "NOT the official CJ exceedance count … undercounts" sentences: deleted.
- The whole "Potential Consent Judgment implications" section (¶5.5(D)/(E), the 48-hour
  correction note, the methane note, REQUESTED EGLE REVIEW, AG STATUS): deleted.
- Surface standards (500 ppm SEM methane, 122 ppb surface H2S): never in these emails
  ("We are not reporting surface methane in this report, ever").
- "(¶def T)" style citations read "(Consent Judgment, definition T)".
- The full disclaimer moved from the top to "How to read this alert"; benchmarks lead it.
- "The values below are…" became "The values are…"; no word "own" anywhere.
- Standardized on "benchmark" for the CJ action levels everywhere, including the grid title
  "not above benchmark" (briefly "not above limit"; Trisha 2026-11-26).
- Standardized on "Max" for the run summary too ("Max: X ppb H2S, Y ppm methane.", was
  "Highest:"), matching the grid titles. ERR-labelled values stay out of every max
  (Trisha 2026-11-26).
- "How to read" was reworded for brevity by a copy pass, every number and citation kept.

### 5. Data-quality rules

- **Suspect sensors** = the periods in `gfl_air.suspect_sensors` (config, America/Detroit
  local times), which mirror the perimeter-air dataset's data-quality note — "the same
  rules as our data quality note for the data set uses" (Trisha): a constant non-zero H2S
  value for months, or a constant 0.0 for more than a year while neighbours vary — plus
Update the config list whenever the note changes.
- A value the feed labels **ERR** is NOT a suspect sensor (Trisha changed her ruling the same
  day): it shows the same grey box as BDL, reading "source: ERR", and never sets a max.
- A suspect, BDL (H2S) or no-data value never sets a max. A max that is not an episode
  peak listed below carries its station and time (CLAUDE.md "Assumptions": every max
  carries its reading's own date) — e.g. a methane-only run's H2S max.
- A value marked BDL never shows its number (latest table, episode back-values).

### 6. Urgent and liveness emails

Same look, HTML part added; subjects, triggers, recipients and plain text unchanged
except "GFL's OWN self-reported" → "GFL's self-reported". The urgent HTML shows each
flagged reading with the feed's label in a grey box (e.g. "source: ERR").

## Consequences

- One email per run instead of two; recipients and thresholds unchanged.
- Gmail filters keyed on a subject starting `[SCREENING ALERT]` need `SCREENING ALERT`
  without the leading bracket.
- `format_screening_email` / `format_closeout_email` and their legal-text helpers were
  removed, so a second, contradicting template cannot be wired back in.
- Tests pin the required wording in both parts, the four episode cases, the 7-day bucket
  and byte budget, the roster reconciliation, the BDL/suspect/max rules.

## Residuals

- The suspect-sensor list is hand-kept; a new stuck period shows only after the dataset
  note and `config.yml` are updated.
- Detection limits are inferred from the feed's labels (H2S < 7 ppb, methane ≤ 0 ppm);
  the source publishes none.

## Verification (2026-11-26)

- Real past runs (2026-11-22, 2026-09-14, 2026-09-29) rendered through the real code path
  match the approved mockups; HTML sizes 40 / 49 / 40 KB.
- CI test send: `scoped-test-send` gained a read-only `gfl-air` mode (replays the most
  recent 24-hour run window with an episode from the live feed, subscriber footer shown,
  `[TEST]` subject prefix, one recipient). Run 45905579083 delivered the Nov 21–22 replay to
  Trisha's owner inbox; she approved it ("It looks great").
