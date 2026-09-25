# ADR 044 — Track the Napier Rd culvert permit site (NAPR, WRP034436)

*Status: active — 2026-09-25 (Trisha-directed; config-only, no code change).*

## Context

Trisha found a GFL nSITE registration the monitor did not know about:
`-4552820604746245548`, site name "82-Napier Rd, 0.3-mile North of 5 Mile Rd-Northville Twp".
It holds EGLE Water Resources Division permit **WRP034436 v.1** (Parts 301/303/31 et al.),
issued 2022-08-01 to GFL Environmental, **expires 2027-08-01**, authorizing a new 53-ft
box culvert with 39 cu yd of fill and 6 cu yd of riprap in an **unnamed tributary of
Johnson Creek** on Napier Rd between Five and Six Mile Roads, Northville Twp, Wayne Co.
Copied at issuance to Northville Twp, the Washtenaw drain office, Wayne County, and
Tetra Tech (GFL's consultant). It is Johnson Creek watershed work on the Napier Rd
frontage, next to the expansion parcels, so it belongs with the rest of the case file.

Live fetch of every nSITE profile at add time:

| Profile | Records | Detail |
|---|---|---|
| Documents | 12 | 7 JPA attachments (2022-04-27), permit, plans, placard, permit packet (2022-08) |
| Submissions | 1 | HPE-J21H-ZS5WW, Joint Permit Application, WRD-Resources, Completed |
| Permits | 1 | WRP034436, Resources Minor Project, In Effect, 2022-08-01 to 2027-08-01 |
| Violations / Compliance Actions / Evaluations / Complaints / Public Notices | 0 | |

## Decision

Add one site, srn **`NAPR`**, everywhere a site is registered:

- `facilities:` (Documents) — so its 12 documents are classified and archived like the others.
- `nsite_sites:` — the site identity every profile watcher resolves (ADR 022).
- Tier maps: **Submissions biweekly** (a revised JPA or a Notice of Completion would
  land here), **Permits biweekly** (an in-effect permit with a 2027 expiry; a revision or
  renewal shows here first; same tier RA/WRD use for in-effect permits), every other
  profile **quarterly** (zero records; mis-filing insurance, the tier every dormant site uses).

## Activation / first run

The 12 existing documents are all from 2022. `watcher.max_new_docs_per_run` is 25, so the
daily watcher would otherwise process them as *new* filings and put 2022 records in the
digest. To avoid that, a manual `backfill.yml` run is dispatched immediately after this
lands (backfill never alerts); by the next 6am ET watcher run they are already processed.
The profile watchers baseline their first sighting silently, as for every other site.
