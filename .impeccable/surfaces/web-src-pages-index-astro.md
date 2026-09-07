---
version: 1
slug: "web-src-pages-index-astro"
primary_target: "web/src/pages/index.astro"
related_targets: []
---

---
version: 1
slug: "web-src-pages-index-astro"
primary_target: "web/src/pages/index.astro"
related_targets: []
---

## Scope

The verification surface itself: a buyer brings photographs of an AirPods Pro 2 box and case and receives an Evidence Ledger. Visitor mode: **Operate** — the visitor completes an examination. A first-time visitor still has to understand within seconds what this is and why Apple's own serial lookup does not settle it, but that understanding is carried by the sheet doing its job, not by a marketing hero above it.

## Audience and job

A buyer standing over a stranger's box at a meet-up, phone in daylight, minutes to decide; and the same buyer at a desk working through listing photos before travelling. Both are primary (PRODUCT.md `## Users`). The job: find out what the photos already prove, what they cannot, and which photograph would settle it.

## Proof and content

Real: the nine rules and their weights, every source string in `knowledge/airpods_pro_2.yaml`, the panel vocabulary, the measured corpus result (20 blind-labelled posts, 12/20 agreement, 0 fakes passed, 0 genuine accused). Demonstration ledgers are authored at full fidelity from real corpus posts and labelled synthetic. Nothing about accuracy, pricing, users, or endorsement is invented.

## Constraints

- The LLM never renders a verdict; the surface must make the extraction/decision boundary visible rather than claim it.
- No percentage authenticity score anywhere. Coverage is coverage; a grade is a grade.
- INCONCLUSIVE is a first-class outcome with equal dignity to a verdict, never styled as a failure.
- Colour is never the sole signal for any state (an outdoor phone screen, and the product's own accessibility principle).
- Uploaded photographs carry strangers' serial numbers; the free-tier retention disclosure is part of the surface, not a footnote nobody reads.

## Direction contract

THESIS: The verdict is a graded examination certificate, not a badge. Refuses the drop-zone hero with a colour-coded score.

OWN-WORLD: Security-paper #E9EDEF, blue-black #0D1417, intaglio green #0B6B4F owning border, plot and rail; vermilion #C2371D only on struck FAIL marks. Archivo/Archivo Narrow, tabular figures. One hard unit; sections split by voids, not hairlines. State is a mark, never a hue.

STORY: A buyer over a stranger's box sees what is still unphotographed, reads every finding with its citation, and can walk or pay.

FIRST VIEWPORT: Full-bleed sheet, guilloche border. Left: the box unfolded as a net — front, back, label, sides, case lid — findings plotted on the panel they came from; unexposed panels are the primary action. Right: graded rows, source in fine print beneath, grade struck across their head. Empty is a blank ruled record.

FORM: Graded Examination Report; #5 of 7, assigned by roll (my #1: field-guide plate); seed 11c09197.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Memorable moment

The box net. Findings are marked at the panel they were read from, and the panels nobody photographed sit unexposed on the sheet — so the gap in the evidence is the same object as the instruction for closing it.

## Decided during the build

- **It prints.** A certificate that cannot be printed is not one. `@media print` drops the guilloche, the actions, the legend and the marketing sections, and keeps the record two-column.
- **Serials are masked wherever the sheet prints them**, at the `examine()` boundary rather than per rule, so a rule added later cannot word its way around it. The buyer reads the serial off the box for Apple's lookup.
- **`RUN THE EXAMINATION` sits below the fold deliberately.** The first viewport's action is `ADD PHOTO` on the unexposed panels; RUN does nothing until a photograph exists, and hoisting a dead control above a live one would have cost either the plot or the thesis.
- **The examination does not navigate when scripting is on.** The next step is almost always "add one more photograph", and a navigation empties every file input.

## Unresolved

- Hosting and the API key path for a public deployment. The server needs `GEMINI_API_KEY` and the Python checkout beside it (or `VERIFIER_ROOT` pointing at one); both failure modes now surface as named notices rather than generic errors.
- Rate limiting is real: free-tier examinations measured 8s to 89s across a session, and one hit the 120s timeout. A public deployment needs a queue or a paid key before it meets more than one visitor at a time.
