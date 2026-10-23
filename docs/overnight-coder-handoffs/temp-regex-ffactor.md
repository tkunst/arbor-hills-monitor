# Handoff: coder:temp-regex-ffactor

Copied verbatim from the Lotext overnight-coder queue (2026-10-23).

- **Dependency:** null
- **Handoff:** none needed; this block is the spec.
- **Readiness:** LOCAL or CLOUD (offline-testable). Small change to a live path (`email_alerts._TEMP_RE`, used by the free-text fallback and `review_flags` KIND_TEXT_TEMP); ends at a DRAFT PR.
- **What happened:** the 2026-10-16 N1504 filing (nSITE doc 4935451998137733993, Drive `1LSB8ra3mfH10HLYEVeAhBeNl3k6AzGlz`, ICT sulfur test report for Arbor Hills Energy) raised a "9340F above the 250F ceiling" owner notice. The source is the AtmAA lab sheet "Calculated values for Specific Volume, BTU, and F (factor)": OCR reads the F-subscript-d factor as `Fy (factor) 9340 F,, (factor) 11356 F, (factor) 1729`. `_TEMP_RE` matches `9340 F` because `F` followed by `,` satisfies `f\b`. No alert fired and no data was written, but the review flag and email were noise.
- **Do:** (a) In `_TEMP_RE` (or a guard around its matches), reject a match when the `F` is an F-factor: the `F` is followed within a few characters (allowing OCR junk like `,`, `y`, `d`, `w`, `c`, spaces) by `factor` or `-factor`, or the number sits inside a span that mentions "F-factor" / "F (factor)" / "Method 19". (b) Keep every current true positive: "180F", "180 F", "180°F", "180 degrees F", "2,145°F", "145 F." at sentence end. (c) Tests: add this document's exact OCR line as a negative fixture, plus `F-factor 9,340`, `Fd factor = 9340`, `Fc 1729`; re-run the whole suite. (d) Consider whether `F` immediately followed by a comma and then `(factor` is a pattern worth a named constant with a comment pointing here.
- **Goal (paste this):**

  ```
  Follow docs/overnight-coder.md. Goal + full spec: the coder:temp-regex-ffactor block in /Volumes/Samsung-Pro-2TB/Lotext/documents/overnight-coder-queue.md -- read that block first, it IS the goal. Copy it to docs/overnight-coder-handoffs/temp-regex-ffactor.md as the PR's first commit.

  In one line: stop email_alerts._TEMP_RE from reading EPA Method 19 F-factors ("Fd (factor) 9340", OCR "9340 F,, (factor)") as Fahrenheit, keep every existing true positive, add the 10/16 N1504 OCR line as a negative test. Start from a fresh branch off origin/main. End at a DRAFT PR.
  ```
