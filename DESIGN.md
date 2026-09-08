---
name: AirPods Pro 2 Examination Record
description: A graded examination certificate printed on security stock — the browser surface over the rules engine.
colors:
  paper: "#e9edef"
  paper-field: "#dfe5e8"
  paper-deep: "#cfd8dc"
  ink: "#0d1417"
  ink-soft: "#4a5a63"
  intaglio: "#0b6b4f"
  intaglio-soft: "#7fa899"
  intaglio-hair: "#b9cfc7"
  struck: "#c2371d"
typography:
  masthead:
    fontFamily: "Archivo Narrow, Archivo, system-ui, sans-serif"
    fontSize: "clamp(38px, 6.6vw, 88px)"
    fontWeight: 700
    lineHeight: 0.88
    letterSpacing: "-0.006em"
    textTransform: "uppercase"
  grade:
    fontFamily: "Archivo Narrow, Archivo, system-ui, sans-serif"
    fontSize: "clamp(34px, 5.4vw, 62px)"
    fontWeight: 700
    lineHeight: 0.92
    letterSpacing: "0.005em"
    textTransform: "uppercase"
  title:
    fontFamily: "Archivo, system-ui, -apple-system, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: "24px"
    letterSpacing: "-0.012em"
  subtitle:
    fontFamily: "Archivo, system-ui, -apple-system, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: "24px"
    letterSpacing: "-0.011em"
  body:
    fontFamily: "Archivo, system-ui, -apple-system, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: "24px"
    letterSpacing: "normal"
    fontFeature: "'tnum' 1, 'lnum' 1"
  label:
    fontFamily: "Archivo Narrow, Archivo, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: "16px"
    letterSpacing: "0.18em"
    textTransform: "uppercase"
  caption:
    fontFamily: "Archivo Narrow, Archivo, system-ui, sans-serif"
    fontSize: "9.5px"
    fontWeight: 600
    lineHeight: "16px"
    letterSpacing: "0.14em"
    textTransform: "uppercase"
  fine:
    fontFamily: "Archivo Narrow, Archivo, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 400
    lineHeight: "16px"
    letterSpacing: "normal"
rounded:
  none: "0"
spacing:
  u: "8px"
  1: "8px"
  2: "16px"
  3: "24px"
  4: "32px"
  5: "40px"
  6: "48px"
  7: "56px"
  8: "64px"
  9: "72px"
  12: "96px"
components:
  button-run:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "14px 32px"
  button-run-hover:
    backgroundColor: "{colors.intaglio}"
    textColor: "{colors.paper}"
  button-run-disabled:
    backgroundColor: "transparent"
    textColor: "{colors.ink-soft}"
  panel-cue:
    backgroundColor: "{colors.intaglio}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "3px 7px"
  panel-cue-exposed:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  ledger-row:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    padding: "16px 0 20px"
  notice-fail:
    backgroundColor: "transparent"
    textColor: "{colors.struck}"
    rounded: "{rounded.none}"
    padding: "16px 24px"
  seal:
    backgroundColor: "transparent"
    textColor: "{colors.intaglio}"
    rounded: "{rounded.none}"
    size: "132px"
---

# Design System: AirPods Pro 2 Examination Record

## Overview

**Creative North Star: "The Graded Examination Certificate"**

This surface is a document, not an app screen. It behaves like a printed
examination record on cold security stock: a guilloche border traced by an
actual geometric lathe, a masthead ruled off from the body, a plot of the
specimen, graded rows with their sources in fine print, and a grader's seal
that names who graded it. The buyer holding a phone over a stranger's box and
the buyer at a desk reading listing photographs are looking at the same sheet
at two widths, not at two products.

Density is high and deliberately unpadded. There are no cards, no panels, no
containers, no rounded corners anywhere in the shipped stylesheet — the entire
surface is one hard unit of paper, and its sections are separated by voids and
by a small number of heavy printed rules. The ornament is real: the border, the
underprint behind the grade, and the seal ring all come from one lathe function
(`guilloche.ts`) tracing hypotrochoids on integer teeth so the curves close
without a seam.

The refusals are structural. There is no drop-zone hero, no colour-coded score
badge, and no authenticity percentage, because the pipeline behind the sheet
does not produce one. Coverage and confidence are printed as two separate
figures and never merged. State is carried by a struck mark — a tick, a crossed
box, a dashed box — so every outcome survives colour being removed, and
INCONCLUSIVE reads as an unfilled field rather than a failure.

**Key Characteristics:**
- One hard modular unit (`--u: 8px`); every measure a whole multiple of it
- Four colour roles only; vermilion reserved for struck FAIL marks
- Zero corner radius, zero drop shadows, zero containers
- Drawn ornament from a real lathe, never an image asset
- State is a mark, never a hue
- Tabular lining figures on globally, so numbers align down a column
- One authored motion moment, guarded for reduced motion

## Decisions that look like mistakes

Each of these is deliberate, load-bearing, and invisible in the code that
implements it. A future pass will read every one of them as a defect and
"fix" it. Read the reason before changing any of them.

**`RUN THE EXAMINATION` sits below the fold at 1440x900.** The first viewport's
primary action is `ADD PHOTO`, which appears at rest on every unexposed panel.
RUN does nothing until a photograph exists, so hoisting it above the fold puts
a dead control above a live one, and buying the space costs either the plot
(the signature interaction) or the masthead (the thesis). Verified in review;
the fix was declined with reasons, not overlooked.

**Serial masking lives at the `examine()` boundary, not in any rule's detail
string.** Every report crosses one seam on its way to the page, and masking
there is what stops a rule added later from leaking a serial by wording its
detail differently. Moving this into the rules, the templates, or a component
re-opens the exact hole review caught: the live record printing a stranger's
serial in full while the filed records below it were masked.

**The examination does not navigate, and the plot overlay is never replaced.**
A form POST empties every file input, and the record's own next step is almost
always "add one more photograph". The in-place swap replaces the drawing, the
findings, the record fields and the next steps, and deliberately leaves the
overlay holding the file inputs and their previews alone. Anything that needs
to change on the overlay after a grade — the `is-graded` class, for instance —
goes on the `.plot` figure, which survives. Put it on the overlay and it will
work on a full page load and silently fail on the path visitors actually take.

**`plotPanel()` returns null rather than falling back to a rule's primary
surface.** A mark is only ever drawn on a surface that was actually
photographed. The fallback looks like a null-safety improvement and is not: it
puts a green tick on a blank panel, claiming evidence nobody supplied.

**Uploaded photographs are sent at full resolution, and must not be resized in
the browser.** It looks like an obvious win — a 12MP phone photo is 3-4MB over
a parking-lot connection, and the pipeline downscales to 1568px for the vision
API anyway. But the local letterform measurement reads the *original* file, not
that downscaled copy. A label close-up carries 100-200px glyphs at 4032px and
39-78px at 1568px, straddling the 45px gate the font check needs. Resizing
client-side would silently disable that check on precisely the photographs it
exists for.

**Coverage is never rescoped to the panels the visitor happened to send.** The
denominator is always the full 19.5. Rescoping would let a case-only photoset
read as near-complete and clear a unit whose box nobody photographed.


## Colors

A cold, four-role security-print palette: blue-black ink and register green on
grey-blue stock, with one vermilion reserved for the strike.

### Primary
- **Intaglio Green** (`#0b6b4f`): the register colour. It owns the guilloche
  border, the underprint rosette, the seal ring, the plot's fold lines and
  panel edges, the coverage rail's passed segments, section headings, the
  `Source` keys in fine print, list markers, ruled table heads, the focus ring,
  the selection background, the scrollbar thumb hover, and links. It is the
  colour of the printing itself, not of any state.

### Secondary
- **Struck Vermilion** (`#c2371d`): the strike. It appears only on a FAIL mark's
  box and cross, the failing row's parameter, the `HIGH RISK SUSPECTED` grade
  word and its rule, the diagonal hatch of a failed rail segment, and the
  border of the failure notice. It is never a fill, never a background, never a
  mood.

### Neutral
- **Security Stock** (`#e9edef`): the sheet. Body background, the browser
  theme-color, and the fill behind every plotted mark so a mark reads clean
  over a photograph.
- **Recessed Field** (`#dfe5e8`): the plot's panel fields and the scrollbar
  track — the only tonal step the system uses to recess a surface.
- **Fold Shadow** (`#cfd8dc`): the html-level ground behind the sheet and the
  diagonal hatch of an unexposed panel.
- **Blue-Black Ink** (`#0d1417`): all body text, the cut edge of the carton, the
  masthead rule, the colophon rule, the primary button, and the ink badge on an
  exposed panel.
- **Tinted Ink** (`#4a5a63`): secondary text, field labels, unfilled values,
  informational rows, the dashed box of an unresolved mark. Tinted from the ink
  hue, never a neutral grey.
- **Soft Register** (`#7fa899`): the scrollbar thumb and the plot's dashed
  edges and figures on a blank record.
- **Register Hair** (`#b9cfc7`): the one-pixel ruled line under a ledger row or
  a register table row, and the unfilled grade's baseline.

### Named Rules
**The Four Roles Rule.** Paper, ink, intaglio, struck. Nothing outside these
four roles reaches the sheet. A new need is a tint of an existing role, not a
tenth colour.

**The Strike Reserve Rule.** Vermilion appears only where a mark is struck
through or a finding is disqualifying. It is never a fill, a background, a
hover state, or an emphasis colour.

**The Colour-Removed Rule.** Every state must remain readable with colour
stripped out. A tick, a crossed box, a dashed box, a solid versus dashed border,
a hatched versus solid fill — the glyph or the stroke style carries the meaning
and the hue only reinforces it.

## Typography

**Display Font:** Archivo Narrow (self-hosted variable woff2, 400–700)
**Body Font:** Archivo (self-hosted variable woff2, 100–900)
**Label Font:** Archivo Narrow

**Character:** One grotesque family in two widths. The narrow cut does every
job a printed form gives to condensed type — masthead, grade word, field
labels, fine print, table sources, panel names on the plot — and the regular
cut carries everything meant to be read as prose. Both faces ship split into
latin and latin-ext unicode ranges and are preloaded; `system-ui` sits behind
them only as a swap fallback, never as a design choice.

Tabular lining figures are on globally on `body`
(`font-variant-numeric: tabular-nums lining-nums`), because this sheet is
mostly numbers in columns: weights, coverage, confidence, record numbers, panel
counts.

### Hierarchy
- **Masthead** (Narrow, 700, `clamp(38px, 6.6vw, 88px)`, 0.88, `-0.006em`,
  uppercase): the product name at the head of the sheet. One per page.
- **Grade** (Narrow, 700, `clamp(34px, 5.4vw, 62px)`, 0.92, uppercase): the
  verdict word, always sitting on a 3px rule that takes the verdict's colour.
  An ungraded record drops to `clamp(26px, 3.6vw, 42px)` — the field is smaller
  because it is unfilled, not because it is less important.
- **Title** (Archivo, 600, 19px, 24px, `-0.012em`): filed-record and method
  headings.
- **Subtitle** (Archivo, 600, 17px, 24px, `-0.011em`): a ledger row's parameter
  name; also the masthead standfirst at 17px/28px.
- **Body** (Archivo, 400, 15px, 24px): all prose. Measures are capped by
  content: 46ch in method columns, 56ch for the grade gloss and action note,
  62ch for section notes, 68ch for a finding's detail, 72–82ch for sources and
  limits.
- **Label** (Narrow, 700, 12px, `0.18em`, uppercase): section headings, set in
  the register colour.
- **Caption** (Narrow, 600, 9.5–10.5px, `0.14–0.16em`, uppercase): field keys in
  the record block, table heads, `Source` and `Could not run` keys, row meta.
- **Fine** (Narrow, 400, 11.5px, 16px, tinted ink): the citation line under
  every finding, plot captions, register source cells.

### Named Rules
**The Two Widths Rule.** Narrow is the printed apparatus — mastheads, grades,
labels, keys, figures on the drawing, fine print. Regular is the reading voice.
A third family is never introduced, and a system display face is never
substituted for the self-hosted ones.

**The Tabular Figures Rule.** Figures are tabular and lining everywhere. Any new
numeric readout inherits this; never disable it for a "nicer" proportional set.

**The Uppercase Is Apparatus Rule.** Uppercase + wide tracking marks something
as form furniture (a key, a heading, a stamp). It is never used on a sentence
the buyer has to read.

## Layout

The sheet is a single full-bleed unit: `min-height: 100vh` of paper against a
darker ground, its content column capped at 1560px and padded
`64px 56px 80px` (`8u 7u 10u`), dropping to `48px 24px 64px` under 720px. A
guilloche border sits inside the sheet edge, 22px deep on all four sides;
under 720px the left and right strips are dropped so the narrow column keeps
its measure.

**The unit.** `--u: 8px` is the only rhythm. Every gap, pad, margin, and grid
track is a whole multiple of it — the two half-steps that exist (`0.75u`,
`1.25u`) are optical corrections inside a component, not a second scale. Line
heights are expressed in units too: body prose runs `calc(var(--u) * 3)`, fine
print `calc(var(--u) * 2)`.

**Structure.** The masthead is a two-column grid (title / record fields) ruled
off with a 2px ink line. The examination below it is `1.15fr / 1fr` — plot left,
findings right — with a 64px gutter, the plot column sticky at `24px`. Filed
records are a three-column grid; the method block is
`repeat(auto-fit, minmax(28ch, 1fr))`. Every major section is closed with 96px
(`12u`) of void.

**Sections are split by voids, not hairlines.** The whole page carries exactly
four rule weights: a 2px ink rule under the masthead and above the colophon, a
2px intaglio rule under the findings heading and under table heads, a 3px
verdict-coloured rule under the grade word, and a 1px `intaglio-hair` line
between ledger rows and register rows. Nothing else divides anything.

**Breakpoints.** 1100px collapses the examination and filed records to one
column and unsticks the plot. 900px tightens the ledger grid. 860px stacks the
masthead, left-aligns the record fields, and drops the register's source
column. 720px repads the sheet, drops the side border strips, and switches the
plot to panning. 560px halves the record grid to two columns, hides the
underprint, and shrinks the seal.

**Wide content pans; the page never does.** Anything wider than its column —
the plot, the register table — lives inside its own `overflow-x: auto` box with
`overscroll-behavior-x: contain`. The document body never scrolls sideways.

### Named Rules
**The Whole Unit Rule.** Every measure is `calc(var(--u) * n)` for a whole `n`.
If a value cannot be expressed that way, the layout is wrong, not the unit.

**The Void Rule.** Sections are separated by space and by mass. A hairline
between two sections is a failure to give them enough room; rules are reserved
for the four places listed above.

**The Pan-Don't-Shrink Rule.** A drawing whose type would become illegible at
the phone's column width keeps its size and pans inside its own box
(`min-width: 560px` in an `overflow-x: auto` figure). Shrinking to fit is not
responsive, it is unreadable.

## Elevation & Depth

**No shadows.** There is not one drop shadow, glow, or blur on this surface,
and none should be added. It is a sheet of paper: everything is printed at the
same physical level.

Depth is entirely tonal and material. Three moves carry it: the sheet sits
lighter than the ground behind it (`#e9edef` on `#cfd8dc`); a plot panel is
recessed by filling it one step darker (`#dfe5e8`) and an unexposed panel is
recessed further with a `#cfd8dc` diagonal hatch; and the underprint rosette
sits beneath the grade at 0.1 opacity, with the data band re-laying paper over
it so the ornament stops cleanly at the readout's edge.

The single `box-shadow` in the codebase is `inset 0 0 0 1px var(--intaglio)`,
used as a hairline outline on a de-emphasised panel cue. It is a stroke written
in shadow syntax, not elevation, and it is the only acceptable use of the
property.

### Named Rules
**The Printed-Flat Rule.** Nothing on this sheet floats. Depth comes from tone
(paper → field → hatch) and from ink weight. No `box-shadow` for elevation, no
hard offset shadow, no blur, ever.

## Shapes

Every corner on this surface is square. There is no `border-radius` anywhere in
the shipped stylesheet, and the `rounded` scale has exactly one step: `0`.
Rectangles are the whole form vocabulary — the sheet, the plot panels, the
state marks, the buttons, the panel cues, the notice box, the rail segments.

Stroke style, not colour, is the second axis of meaning. Solid means resolved
and cut; dashed means unresolved, unexposed, or folded. The carton's cut edge
is a 2.4px solid ink outline traced once around the whole net; its interior
folds are 1px intaglio dashed (`9 5`). An unresolved mark is a dashed box
(`3 2.5`); a resolved one is solid. An unrun rail segment is a dashed 1px
outline; a run one is solid and filled. A failed rail segment is filled with a
-45° repeating stripe rather than a flat vermilion block, because a flat fill
would be vermilion used as a mood.

The only curves on the sheet are lathe-generated: the border strip's
interwoven sinusoids, the seal ring, and the underprint rosette. They are
computed as hypotrochoids on integer teeth so the pen returns exactly to its
start — 53 teeth against 8 for the open rosette and seal, 37 against 6 for the
inner ring, two full wave periods per 120px border tile.

### Named Rules
**The Square Corner Rule.** Radius is 0. Everywhere. A rounded corner on this
sheet is a bug.

**The Drawn Ornament Rule.** Guilloche is generated by `lib/guilloche.ts`, never
shipped as an image. Teeth must be integers or the curve leaves a seam; pen
distance under the rolling radius gives petals, over it gives loops.

## Components

### Buttons
- **Shape:** square (0 radius), 2px solid border matching its own fill.
- **Primary ("Run the examination"):** ink fill, paper text, Narrow 700 14px
  uppercase at `0.16em`, padded `14px 32px` (`1.75u 4u`).
- **Hover / Focus:** background and border shift to intaglio over 160ms
  `cubic-bezier(0.16, 1, 0.3, 1)`. Focus uses the global 2px intaglio ring at
  3px offset.
- **Disabled (running):** fill drops out entirely, text goes tinted ink, border
  goes `intaglio-hair`, cursor `progress`. Meanwhile the grade word pulses on a
  1.6s opacity loop — the disabled button is not the only signal that work is
  happening.

### Marks (the three state glyphs)
The system's smallest and most important component. Three drawn SVG glyphs on a
14-unit box, used at 18px in the ledger and 19 user-units on the plot:
- **PASS:** intaglio box with an intaglio tick.
- **FAIL:** vermilion box with a vermilion cross drawn corner to corner.
- **UNKNOWN:** tinted-ink box with a dashed stroke and no interior glyph.
Strokes are square-capped. On the plot the box is filled with paper so a mark
stays legible over a photograph, and each mark carries its ledger number
beneath it in Narrow 700. Every one of the three is distinguishable in
greyscale.

### Ledger row
A three-column grid (`4u / 3u / 1fr`): zero-padded index in intaglio Narrow, the
state mark, then the body — parameter name, a caps meta line carrying tier and
weight (or `informational, does not vote`), the finding's detail, and the cited
source in fine print with an intaglio `Source` key. Rows are separated by a 1px
`intaglio-hair` rule; the last row has none. A failing row turns its parameter
name vermilion in addition to its struck mark. Two modes exist: `condensed`
prints every row but only argues the struck ones, and `resolvedOnly` lists what
ran and names the rest on a single `Could not run` line.

### Grade block
The verdict word set large in Narrow, underlined by a 3px rule that takes the
verdict's own colour — ink by default, intaglio for GENUINE, vermilion for
HIGH RISK SUSPECTED, and a *dashed* tinted-ink rule for INCONCLUSIVE so the
outcome reads as unfilled rather than failed. Beneath it: a one-sentence gloss
capped at 56ch, then the data band.

**The coverage rail** is a flex row of segments, one per weighted rule, each
`flex-grow` set to that rule's weight, 16px tall with 3px gaps. Unrun segments
are dashed outlines; passed are solid intaglio fills; failed are a vermilion
diagonal stripe. Beneath it a definition list prints **Examination coverage**
(`x of y evidence weight`) and, only on a decided record, **Confidence in the
finding**. These are two numbers and are never merged, never turned into a
percentage, and never given a colour ramp.

### Grader's seal
A 132px circular lathe ring at 0.55 opacity (108px under 560px) with the
lettering set inside it, not on it: "GRADED BY / RULES ENGINE / n CHECKS" in
Narrow. Its accessible label states outright that no language model returned
the verdict. It appears only on a graded record.

### The plot (signature component)
The carton drawn flat as a net — front, back, label, sides, plus case and buds
— at a fixed viewBox, with findings marked at the panel they were read from.
The cut edge is one continuous 2.4px ink path around the whole net so it reads
as one folded object; folds are lighter dashed intaglio inside it. Unexposed
panels are hatched and carry an "ADD PHOTO" cue **visible at rest**, because the
meet-up buyer's phone has no hover and an unexposed panel is the primary
action; an exposed panel's cue drops to "REPLACE" and only appears on hover or
`:focus-visible`. Photographs load into the panel at 0.58 opacity with
`mix-blend-mode: multiply` so the drawing shows through.

Two honesty behaviours are load-bearing and must be preserved: `plotPanel()`
returns null when the source surface was never photographed, so a tick can
never land on a surface nobody shot; and on an ungraded record the panels print
what each surface is *worth* in evidence weight, swapping to marks once the
record fills.

### Ruled record fields
The masthead's record block and the grade readouts share one pattern: a 9.5px
tinted-ink caps key over a 15–16px value, with `of` and unit words set in Narrow
tinted ink so the figure itself carries the weight. An unfilled value prints a
tinted em-dash pair, never a zero.

### Named Rules
**The Two Figures Rule.** Coverage and confidence are printed as two labelled
numbers, side by side, in the same size. They are never averaged, never
combined into a score, and no authenticity percentage exists on this surface.

**The Unfilled Field Rule.** An empty record is a blank ruled form, not an error
or an empty state illustration. Every check is already listed, the grade field
is ruled and captioned "Not yet graded", and the values print as dashes.

**The Motion Budget Rule.** One authored moment: on a fresh result, marks strike
onto the sheet in ledger order (420ms, 55ms apart), the grade word resolves out
of blur and wide tracking (`grade-strike`, 520ms), and the rail segments scale
in — all on `cubic-bezier(0.16, 1, 0.3, 1)`, all cut to 1ms under
`prefers-reduced-motion`. Everything else is a 140–180ms opacity or colour
transition. No parallax, no scroll animation, no looping decoration.

## Do's and Don'ts

### Do:
- **Do** express every measure as `calc(var(--u) * n)` with a whole `n`; `--u`
  is 8px and it is the only rhythm on the sheet.
- **Do** carry state with a drawn glyph and a stroke style first — tick, cross,
  dashed box, solid vs. dashed border, hatch vs. fill — and let colour only
  reinforce it.
- **Do** keep vermilion for struck FAIL marks, the disqualifying grade word and
  its rule, and the failure notice border. Nothing else.
- **Do** theme the browser's own surfaces from the palette: selection, scrollbar
  track and thumb, `:focus-visible` ring at 2px/3px offset, link underline
  offset at 3px. They are part of the printing.
- **Do** keep tabular lining figures on for every numeric readout.
- **Do** generate guilloche from `lib/guilloche.ts` with integer teeth so the
  curve closes; use `rosette()` for grounds, `sealRing()` for stamps, `strip()`
  for edges.
- **Do** let a drawing pan inside its own `overflow-x: auto` box rather than
  shrink below legibility, and keep `overscroll-behavior-x: contain` on it.
- **Do** give a primary action on a touch surface a cue that is visible at rest;
  hover-only affordances are invisible to half the audience.
- **Do** print every finding's cited source in fine print directly beneath it.
- **Do** keep the print stylesheet working: interactive furniture hides, the
  examination goes to two even columns, the border drops out.

### Don't:
- **Don't** add a border-radius. The radius scale has one value and it is `0`.
- **Don't** add a drop shadow, glow, or blur for elevation. Depth is tonal:
  paper → recessed field → hatch.
- **Don't** merge coverage and confidence, or introduce an authenticity
  percentage, a score ring, or a colour-graded meter.
- **Don't** style INCONCLUSIVE as a failure. It takes tinted ink and a dashed
  rule — the visual language of an unfilled field.
- **Don't** use vermilion as a fill, a background, a hover, or an emphasis
  colour; a failed rail segment is striped precisely so it is never a flat block
  of it.
- **Don't** introduce a fifth colour role, a third type family, a system display
  face, or an icon font. The three state glyphs and the hardware silhouettes are
  drawn paths at a declared stroke weight.
- **Don't** ship guilloche as an image asset, and don't pass non-integer teeth
  to `trace()` — the curve will not close and the seam is visible.
- **Don't** divide two sections with a hairline. Use a void. The only rules on
  the sheet are the masthead/colophon 2px ink lines, the 2px intaglio heading
  rules, the 3px grade rule, and the 1px `intaglio-hair` row lines.
- **Don't** plot a mark on a panel whose source photograph does not exist;
  `plotPanel()` returning null is a correctness guarantee, not an edge case.
- **Don't** publish a whole serial number anywhere on the sheet.
- **Don't** add a second animated moment. One strike, guarded for reduced
  motion, is the budget.
