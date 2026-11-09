# DRAFT NOTES: stream-s1 page

Internal notes for the draft page `site/stream-s1/index.html`. Not part of the page.

Source article: `Lotext/documents/arbor-hills/draft/DRAFT-arborhillsmonitor-stream-outfall-FACT-page-2026-09-18.md`.

## Publish gates (verbatim from the draft's header comment)

```text
⛔ DRAFT — NOT FOR PUBLISH. Do NOT git mv this to public/ until every gate below clears.
This is a candidate page for arborhillsmonitor.org (the FACT domain = description/facts only, NO analysis).

PUBLISH GATES (all required, in order):
  1. All legal pages live on arborhillsmonitor.org (Terms, Privacy, Disclaimer/No-Reliance,
     Corrections/Retractions/Takedown, IP/Fair-Use) — Trisha's stated precondition.
  2. Fact-only rewrite verified against the FACT/POLICY firewall (run the policy-firewall-audit
     skill on this page): zero asks, zero "should/warrants/deserves/matters" conclusions, zero
     advocacy. Report facts; let the reader conclude. (The old §5 "What we are asking" is CUT.)
  3. Pre-publish 2-blind-refuter gate (number-heavy page): every figure, quote, permit number,
     reachcode, and date re-verified against the primary source.
  4. ⚖️ JENNIFER DUKARSKI (retained counsel) SIGN-OFF — mandatory. TCI received a C&D for an
     article on THIS SAME stream/outfall subject; publishing a similar page on our PUBLIC domain
     carries real C&D/removal risk. See attorney-correspondence/johnson-creek-outreach-cease-and-
     desist-risk-review-2026-09-15.md (10 ranked items + mitigations). This is now an attorney-
     client review, not self-issue-spotting.
  5. Image-rights check (see IMAGE NOTES below): use OUR work-product images; do NOT reproduce
     GFL's Figure 7 delineation map or GFL's projected slide/engineering-sheet photos on a PUBLIC
     page (C&D items 2-4 flag these as the clearest removal-demand targets).

SOURCE: adapted from outreach/johnson-creek-stream-outfall-package-2026-09-11/BRIEF-...-EDITED-FOR-CLARITY.md
RECIPE APPLIED (Trisha 2026-09-18): cut §1 (scope/permit-status) + §5 (asks); start at §2; keep §3;
keep §4 rewritten to description-only; "earlier proposal to discharge" explicitly attributed to the
prior operator (Advanced Disposal Services, pre-GFL); images inline (ours), not attachments;
keep the caveats.
```

## Other internal comments from the draft (verbatim, left out of the HTML)

```text
 IMAGE (inline): a rights-clear (public-domain / CC0 / U.S. government) photo of typical stream-confluence
     RIPRAP, captioned generically ("Riprap: loose rock placed to armor a channel. Illustrative; not this site.").
     TO SOURCE before publish: USDA NRCS / USACE / Wikimedia Commons public-domain; verify the license and
     credit the source. Do NOT use a GFL/engineering-firm image. 
```

```text
 OPEN DATA GAP (2026-09-18, Trisha-flagged "grab if ever seen"): the stormwater Outfall 001
     (MIS210766) discharge-point LOCATION is not yet pinned. The Remediation-Area site map + GFL's
     Fig 2 discharge-permit schematic show only Outfall 001A (MI0045713 — the remediation discharge
     from Pond 1 + Pond 2, with the flow-meter / sampling manhole). If the stormwater SWPPP or a site
     plan showing Outfall 001's location is ever obtained, capture its coordinates here. This does NOT
     change the north/south conclusion — both permits attach to the existing south-side facility. 
```

```text
 IMAGE (inline): our USGS-NHD downstream-trace map (creek → Johnson Drain → Rouge). Build/export
     from our data; do NOT embed GFL's Figure 7. Caption: "USGS National Hydrography Dataset trace from
     the on-parcel stream to Johnson Drain and the Rouge. Source: USGS NHDPlus HR / NLDI." 
```

```text
 IMAGE (inline): map-johnson-creekshed-wide.png (MapWashtenaw creekshed, public layer). Caption:
     "The Johnson Creekshed. Source: Washtenaw County MapWashtenaw." 
```

```text
 IMAGE NOTES (rights):
  USE (our work-product or public government layers, low removal-risk):
    - our USGS-NHD trace map (built from our data)
    - map-johnson-creekshed-wide.png (MapWashtenaw public layer)
    - the public MapWashtenaw aerial of the on-parcel channel (attribute to Washtenaw County GIS)
  DESCRIBE, do NOT reproduce on a PUBLIC page (C&D items 2-4):
    - GFL's Figure 7 delineation map (reference via the public delineation report link instead)
    - GFL's projected slide photos + engineering-sheet photos (slide-4A FULL/ZOOM, IMG_4274/4278)
    - the not-yet-public county SWMP Facility Inclusion Request (describe; do not host)
  Dukarski to confirm the image set before publish. 
```

## Notes from building this draft (2026-11-09)

- Not done yet: the Sources section on the page is a visible placeholder. The draft says to carry over the Sources table from the brief, links intact, and re-verify each link in the refutation gate.
- Not done yet: none of the three planned images (the riprap illustration, the USGS-NHD trace map, the MapWashtenaw creekshed map) are on the page.
- Photos used: ECT Appendix D Photo No. 15 (hero) and No. 16, re-encoded at JPEG quality 82 with metadata stripped (the originals are 1159 px wide, so they were not resized). Captions quote `delineation-photos-2024-05/CAPTIONS.md` verbatim. The wetland photo strip was left out because the article does not discuss the wetland complex.
- Photo rights: CAPTIONS.md says counsel did not separately review these ECT/GFL site photos, and gate 5 above says not to reproduce GFL images on a public page. Clear these with counsel before publishing, or replace them.
- Stream 1 vs Stream S1: the photo log says "Stream 1" and "Stream 2". The memo text says "Two (2) perennial or perennial/intermittent streams (S1 and S2)", but the log does not say that Stream 1 is S1. Putting Photo 15 at the top of an S1 page implies they are the same stream. Confirm against the memo's photo-location figure.
- Bales email quote (EGLE WRD, October 22, 2026): placed in "Where the water goes" as its own paragraph. Confirm what "that" in the email refers to (the question or the map that was sent) and that it is the on-parcel stream S1. Also confirm that we may quote it with attribution.
- `robots` is set to `noindex, nofollow`, so `scripts/gen_sitemap.py` leaves the page out of the sitemap. Nothing on the site links to it.
- Before publishing: remove the noindex tag and the draft banners, and move this file out of `site/` (anything under `site/**` is deployed on merge). Then regenerate the sitemap and add an internal link, or the orphan test will fail.
