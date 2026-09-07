"""Geometric font comparison against San Francisco.

"Wrong font" is the most-cited counterfeit tell in the owner's catalogue (10 of
25 listings) and the one a vision LLM is worst at. That is not a prompting
problem. A vision encoder downsamples to patch embeddings before it reasons, and
glyph identity lives in exactly the geometry that step discards - so the model is
asked an absolute question ("is this font correct?") about information it no
longer has, with no reference to be correct against.

The fix is to stop asking. Apple sets its packaging in San Francisco, the box
strings are fixed and known, and SF ships with macOS - so the reference is a
font file, not a photograph. The LLM transcribes; Python renders the same
characters in SF and measures the difference. Same division of labour as the
rest of this pipeline.

WHAT WAS MEASURED, and what it ruled out:

  * Line advance-width ratio (width / cap-height for a known string) separates
    SF from Helvetica/Arial by only 5-10%, which handheld perspective distortion
    can exceed. Needs rectification first.

  * Cross-ratio of collinear baseline points is EXACTLY perspective-invariant,
    which is mathematically appealing and useless here: it discriminates fonts
    by 0.0-0.8%. The projective normalisation that removes camera angle also
    removes the advance-width signal that distinguishes the fonts.

  * Per-glyph ink aspect ratio works. Font differences reach 8-22% while blur
    moves the ratio only 1-4%, because blur expands ink horizontally and
    vertically at similar rates and the ratio largely survives. It is also a
    local measurement, so perspective distortion across a single character is
    negligible.

  * Font METRICS are not ink. An early pass using textbbox() suggested the digit
    "1" was a 38% discriminator; measured as rendered ink it is 2.8%, and it
    drifts 12.5% under blur. The difference was side bearings. Everything here
    measures rendered ink.

Glyphs are therefore selected by measured signal-to-noise, not by intuition.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

#: San Francisco, as shipped with macOS. This is the reference.
SF_FONT_PATH = "/System/Library/Fonts/SFNS.ttf"

#: Fonts counterfeit packaging is known or likely to substitute.
SUBSTITUTE_FONT_PATHS = {
    "Helvetica": "/System/Library/Fonts/Helvetica.ttc",
    "Arial": "/System/Library/Fonts/Supplemental/Arial.ttf",
    "Verdana": "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "Tahoma": "/System/Library/Fonts/Supplemental/Tahoma.ttf",
}

#: Render size for reference glyphs. Large enough that quantisation is not a
#: factor; aspect ratio is scale-invariant so the exact value does not matter.
_REFERENCE_SIZE = 160

#: Ink threshold on an 8-bit greyscale render.
_INK_THRESHOLD = 200

#: Punctuation short enough to be removed by the glyph height filter. Full-height
#: marks like brackets are NOT here - they survive segmentation and must remain
#: in the expected character sequence.
_SHORT_MARKS = ".,;:·"

#: Glyphs whose ink aspect ratio separates SF from substitutes by at least 3x
#: the drift blur induces. Measured, not chosen: uppercase P, N, X, Y, H and W
#: fall below that bar, as do lowercase i, l, h and w - all excluded because
#: their blur drift rivals the font signal. Lowercase i and l are the worst in
#: the set (S/N 0.3 and 0.1): they are thin enough that blur destroys the aspect
#: ratio entirely.
#:
#: Lowercase matters because label lines are digit-heavy and digits are tabular.
#: "Designed by Apple in California" appears on every Apple box and offered a
#: single usable glyph while this set was uppercase-only; with lowercase it
#: offers six letters, clearing the three-letter minimum.
USABLE_GLYPHS = frozenset("5ETCR3S4GOZF8K1092" + "acfkmrstz")

#: Passed the synthetic signal-to-noise screen but failed against real print.
#: Both Q's on a confirmed-genuine North American label measured -8.6% and
#: -12.1% against the SF reference - systematic, not noise. SF's Q carries a
#: tail that crosses the bowl, and how much of it survives printing and
#: segmentation varies. Synthetic renders cannot surface this; only genuine
#: photographs can, which is why the empirical baseline matters more than the
#: theoretical screen.
EMPIRICALLY_EXCLUDED_GLYPHS = frozenset("Q")

#: A single glyph deviating this far from SF is worth reporting. Set from the
#: measured blur ceiling (~4%) with headroom, NOT from a labelled corpus - no
#: threshold here has been validated against known-genuine photographs yet.
PROVISIONAL_DEVIATION_THRESHOLD = 8.0


@dataclass(frozen=True)
class GlyphComparison:
    character: str
    measured_aspect: float
    reference_aspect: float

    @property
    def deviation_percent(self) -> float:
        return 100.0 * (self.measured_aspect - self.reference_aspect) / self.reference_aspect

    @property
    def is_usable(self) -> bool:
        return self.character.upper() in USABLE_GLYPHS


def ink_aspect_ratio(image: Image.Image, threshold: int = _INK_THRESHOLD) -> float | None:
    """Width/height of the inked bounding box. None when there is no ink."""
    grey = image.convert("L")
    mask = grey.point(lambda pixel: 255 if pixel < threshold else 0)
    box = mask.getbbox()
    if box is None or box[3] == box[1] or box[2] == box[0]:
        return None
    return (box[2] - box[0]) / (box[3] - box[1])


def render_glyph(
    character: str, font_path: str = SF_FONT_PATH, size: int = _REFERENCE_SIZE, blur: float = 0.0
) -> Image.Image:
    font = ImageFont.truetype(font_path, size)
    canvas = Image.new("L", (size * 3, size * 3), 255)
    ImageDraw.Draw(canvas).text((size, size), character, font=font, fill=0)
    if blur:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    return canvas


@lru_cache(maxsize=256)
def reference_aspect(character: str, font_path: str = SF_FONT_PATH) -> float | None:
    """Ink aspect ratio of a character as San Francisco renders it."""
    try:
        return ink_aspect_ratio(render_glyph(character, font_path))
    except OSError:
        return None


def compare_glyph(character: str, image: Image.Image) -> GlyphComparison | None:
    """Measure one photographed glyph against its San Francisco reference."""
    measured = ink_aspect_ratio(image)
    expected = reference_aspect(character)
    if measured is None or expected is None:
        return None
    return GlyphComparison(character, measured, expected)


def measure_glyph_signal_to_noise(
    character: str, blur_levels: tuple[float, ...] = (2.0, 5.0, 8.0)
) -> tuple[float, float, float] | None:
    """Return ``(signal, noise, ratio)`` for one glyph.

    Signal is the largest deviation any substitute font shows from SF; noise is
    how far SF's own ratio moves under blur. This is the function that produced
    USABLE_GLYPHS, kept so the selection can be re-derived rather than trusted.
    """
    baseline = reference_aspect(character)
    if baseline is None:
        return None

    deviations = []
    for path in SUBSTITUTE_FONT_PATHS.values():
        value = reference_aspect(character, path)
        if value is not None:
            deviations.append(abs(100.0 * (value - baseline) / baseline))
    if not deviations:
        return None

    drift = []
    for blur in blur_levels:
        blurred = ink_aspect_ratio(render_glyph(character, blur=blur))
        if blurred is not None:
            drift.append(abs(100.0 * (blurred - baseline) / baseline))

    signal = max(deviations)
    noise = max(drift) if drift else 0.1
    return signal, noise, signal / max(noise, 0.1)


def derive_usable_glyphs(minimum_ratio: float = 3.0) -> set[str]:
    """Re-derive the glyph set from measurement. Used by the test suite.

    Returns the synthetic screen only; glyphs excluded by evidence from real
    photographs are subtracted separately, since no amount of rendering can
    predict them.
    """
    usable = set()
    for character in string.ascii_uppercase + string.ascii_lowercase + string.digits:
        result = measure_glyph_signal_to_noise(character)
        if result and result[2] >= minimum_ratio:
            usable.add(character)
    return usable - EMPIRICALLY_EXCLUDED_GLYPHS


def segment_glyphs(
    image: Image.Image,
    *,
    threshold: int = _INK_THRESHOLD,
    min_ink_pixels: int = 12,
) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
    """Split a cropped, upright text line into individual glyph images.

    Connected-component labelling over the inked pixels, returned left to right.
    Pure Python: a text line is a few hundred thousand pixels at most, and
    avoiding numpy/OpenCV keeps this runnable anywhere the rest of the pipeline
    runs.

    Note that components are ink-connected, so a dotted or split glyph ("i", or
    a character broken by print wear) arrives as separate pieces. Callers match
    components against known text positionally rather than assuming one
    component per character.
    """
    grey = image.convert("L")
    width, height = grey.size
    pixels = grey.load()

    visited = bytearray(width * height)
    components: list[tuple[int, int, int, int]] = []

    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if visited[index] or pixels[start_x, start_y] >= threshold:
                continue
            # Iterative flood fill; recursion would blow the stack on a long line.
            stack = [(start_x, start_y)]
            visited[index] = 1
            min_x = max_x = start_x
            min_y = max_y = start_y
            count = 0
            while stack:
                x, y = stack.pop()
                count += 1
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        n_index = ny * width + nx
                        if not visited[n_index] and pixels[nx, ny] < threshold:
                            visited[n_index] = 1
                            stack.append((nx, ny))
            if count >= min_ink_pixels:
                components.append((min_x, min_y, max_x + 1, max_y + 1))

    components.sort(key=lambda box: box[0])
    components = _merge_stacked(components)
    components = _drop_outliers(components)
    return [(box, image.crop(box)) for box in components]


def raw_components(
    image: Image.Image, *, threshold: int = _INK_THRESHOLD, min_ink_pixels: int = 15
) -> list[tuple[int, int, int, int]]:
    """Connected ink components with NO single-line filtering applied.

    segment_glyphs layers on filters that assume one text line: merging
    vertically stacked pieces, and dropping components short relative to the
    line median. Both are wrong across a whole label, where several lines differ
    in size - on a counterfeit label those filters cut 221 components to 24 and
    line detection found one line out of several. Layout analysis needs the
    unfiltered set.
    """
    grey = image.convert("L")
    width, height = grey.size
    pixels = grey.load()
    visited = bytearray(width * height)
    found: list[tuple[int, int, int, int]] = []

    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if visited[index] or pixels[start_x, start_y] >= threshold:
                continue
            stack = [(start_x, start_y)]
            visited[index] = 1
            min_x = max_x = start_x
            min_y = max_y = start_y
            count = 0
            while stack:
                x, y = stack.pop()
                count += 1
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbour = ny * width + nx
                        if not visited[neighbour] and pixels[nx, ny] < threshold:
                            visited[neighbour] = 1
                            stack.append((nx, ny))
            if count >= min_ink_pixels:
                found.append((min_x, min_y, max_x + 1, max_y + 1))
    return found


def _drop_outliers(
    boxes: list[tuple[int, int, int, int]], min_height_fraction: float = 0.45
) -> list[tuple[int, int, int, int]]:
    """Discard components too short to be glyphs on this line.

    Text on one line shares a baseline and a cap height, so real glyphs cluster
    tightly in height. Print speckle, barcode fragments and JPEG artefacts do
    not. Without this, a 6x7 noise blob counts as a character, every later glyph
    pairs with the wrong reference, and the whole line reads as a font mismatch -
    which is exactly what happened measuring a counterfeit label.

    Punctuation is genuinely short, so this also drops full stops and commas.
    That is acceptable: they carry no font signal and are never in USABLE_GLYPHS.
    """
    if len(boxes) < 3:
        return boxes
    heights = sorted(box[3] - box[1] for box in boxes)
    median_height = heights[len(heights) // 2]
    floor = median_height * min_height_fraction
    return [box for box in boxes if (box[3] - box[1]) >= floor]


def _merge_stacked(
    boxes: list[tuple[int, int, int, int]], overlap: float = 0.6
) -> list[tuple[int, int, int, int]]:
    """Merge components stacked vertically over the same x-range.

    A dotted glyph ("i", "j") or an accented one arrives as two ink-disconnected
    pieces occupying the same column. Without this, component count never
    matches character count and the whole line is rejected.
    """
    merged: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if merged:
            previous = merged[-1]
            span = min(box[2], previous[2]) - max(box[0], previous[0])
            narrower = min(box[2] - box[0], previous[2] - previous[0])
            if narrower > 0 and span / narrower >= overlap:
                merged[-1] = (
                    min(previous[0], box[0]), min(previous[1], box[1]),
                    max(previous[2], box[2]), max(previous[3], box[3]),
                )
                continue
        merged.append(box)
    return merged


def otsu_threshold(image: Image.Image) -> int:
    """Threshold that best separates ink from paper in THIS image.

    A fixed threshold works on synthetic renders and fails on photographs, where
    paper is off-white and unevenly lit.
    """
    histogram = image.convert("L").histogram()
    total = sum(histogram)
    total_intensity = sum(index * count for index, count in enumerate(histogram))
    best_threshold, best_variance = 128, -1.0
    background_weight = background_intensity = 0
    for threshold in range(1, 255):
        background_weight += histogram[threshold - 1]
        background_intensity += (threshold - 1) * histogram[threshold - 1]
        foreground_weight = total - background_weight
        if background_weight == 0 or foreground_weight == 0:
            continue
        background_mean = background_intensity / background_weight
        foreground_mean = (total_intensity - background_intensity) / foreground_weight
        variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance, best_threshold = variance, threshold
    return best_threshold


@dataclass(frozen=True)
class FontScore:
    """How far a photographed line departs from San Francisco.

    ``offset_percent`` is the median per-glyph deviation and is DISCARDED as
    evidence: it moves with resolution, ink spread and camera distance. Two
    genuine labels measured +2.4% and -6.0% purely from imaging differences, and
    -6.0% is indistinguishable from Arial's synthetic -5.9%.

    ``scatter_percent`` is the signal. Typefaces differ from one another
    NON-UNIFORMLY - SF against Helvetica is 22% on R but 8% on S - so a wrong
    font produces per-glyph deviations that disagree with each other, while
    genuine print produces deviations that agree whatever the imaging
    conditions. Median absolute deviation about the median, so one clipped edge
    glyph shifts nothing.
    """

    glyphs: int
    offset_percent: float
    scatter_percent: float
    letters_measured: int
    median_glyph_height_px: float
    #: Median per-glyph shape distance from San Francisco over the full
    #: descriptor, not just the bounding box. None when no glyph could be
    #: described. See ``shape_distance``.
    shape_distance: float | None = None

    @property
    def is_measurable(self) -> bool:
        """Whether the photograph resolves letterform at all.

        Separate from ``is_scoreable``, which asks whether enough glyphs were
        paired. This asks whether the pixels carry the information in the first
        place - and below 100px they do not, whatever the glyph count.
        """
        return self.median_glyph_height_px >= MEASURABLE_GLYPH_HEIGHT_PX

    @property
    def resolution_shortfall_px(self) -> float:
        """How much closer the photograph needs to be. 0 when already enough."""
        return max(0.0, MEASURABLE_GLYPH_HEIGHT_PX - self.median_glyph_height_px)

    @property
    def is_scoreable(self) -> bool:
        """Digits alone cannot be scored.

        Most typefaces set tabular figures - every digit the same advance width -
        so digits deviate uniformly and carry no per-glyph signal. Measured: on a
        digits-only line SF scatters 0.71% and Helvetica 0.71%, indistinguishable.
        Add letters and SF scatters 0.41% against 3.3-5.3% for substitutes.
        """
        return (
            self.letters_measured >= MINIMUM_LETTERS_TO_SCORE
            and self.glyphs >= MINIMUM_GLYPHS_TO_SCORE
            and self.median_glyph_height_px >= MINIMUM_GLYPH_HEIGHT_PX
        )

    @property
    def too_small_to_score(self) -> bool:
        return self.median_glyph_height_px < MINIMUM_GLYPH_HEIGHT_PX

    @property
    def consistent_with_san_francisco(self) -> bool:
        """True only when the line was actually measurable AND agrees with SF.

        A failed measurement carries scatter 0.0, which without the scoreable
        guard reads as a perfect match - so a segmentation failure would pass as
        genuine. Absence of a measurement must never look like evidence of one.
        """
        return self.is_scoreable and self.scatter_percent < FONT_SCATTER_THRESHOLD

    @property
    def differs_from_san_francisco(self) -> bool:
        """True only when measurable AND disagreeing. Not the negation of the
        property above: an unmeasurable line is neither."""
        return self.is_scoreable and self.scatter_percent >= FONT_SCATTER_THRESHOLD


#: The measured glyph height at which a typeface substitution survives a phone
#: camera. Derived by rendering one label line in San Francisco, Helvetica,
#: Arial and Verdana, degrading each the way a photograph does - optical blur,
#: resampling, sensor noise, JPEG - and scoring all four against San Francisco
#: through this module's own path. Heights are what `median_glyph_height_px`
#: reports, so the gate is expressed in the units the code measures:
#:
#:   glyph px   SF shape   nearest substitute   separation
#:         23     0.1890               0.1382        0.73x   INVERTED
#:         30     0.0869               0.1101        1.27x   noise
#:         41     0.0684               0.1284        1.88x   usable
#:         54     0.0399               0.1514        3.79x   usable
#:         75     0.0650               0.1163        1.79x   usable
#:        106     0.0385               0.1043        2.71x   usable
#:        137     0.0335               0.1123        3.35x   usable
#:
#: At 23px a substitute scores BETTER than San Francisco. That inversion is the
#: mechanism behind this rule's false positives, and no descriptor fixes it:
#: the pixels do not carry the letterform. Photographs of a whole box underside
#: put the fine print at 28-35px - measured on the owner's genuine Pro 2 label
#: (28px) and a counterfeit label (35px) - which is inside the dead band. The
#: cut sits at 45px, above the first usable row with margin.
MEASURABLE_GLYPH_HEIGHT_PX = 45

#: Below this, per-glyph deviations agree and the line is consistent with San
#: Francisco. Genuine photographed labels measured 0.90%; SF rendered
#: synthetically 0.41-0.71%; substitute typefaces 3.3-7.9%. The cut sits between
#: those populations rather than at either edge.
FONT_SCATTER_THRESHOLD = 2.0

#: Digits are tabular and carry no signal, so a line needs real letters.
MINIMUM_LETTERS_TO_SCORE = 3

#: Minimum glyphs before a score may be trusted, measured rather than assumed.
#: On a SINGLE line of six usable glyphs, Arial scored 1.36% - identical to San
#: Francisco - and passed as genuine. Whether a substitute is caught depends on
#: which letters that line happens to contain, and six is too few for that to
#: average out. Pooling a second line takes it to twelve glyphs and every
#: substitute is caught: SF 0.51% against 5.1%-6.9%. Twelve is where it starts
#: working, so the floor sits above it.
MINIMUM_GLYPHS_TO_SCORE = 15

#: A component taller than this multiple of the line's median height has merged
#: with a neighbouring mark. Ascenders and capitals legitimately reach about
#: 1.35x the median on a mixed-case line, so the cut sits above that; the
#: barcode-merged glyphs that motivated it measured 1.48x and higher.
MERGED_GLYPH_HEIGHT_RATIO = 1.4

#: Minimum glyph height, in pixels, for the score to mean anything.
#:
#: Measured by rendering the same string at decreasing sizes: at a cap height of
#: 29px San Francisco scatters 0.94% against Helvetica's 6.32%, clean
#: separation. At 22px SF rises to 1.70% - still under the 2.0% threshold but
#: with almost no margin. Below 18px segmentation fails outright: strokes merge
#: and neighbouring glyphs join, so there is nothing to measure.
#:
#: What can be MEASURED, not what can be TRUSTED. All four of the owner's
#: genuine labels sit at 30-40px glyph height - that is simply what a phone
#: photograph of a retail box produces - so a floor above that would exclude
#: every real sample.
#:
#: A previous value of 60px came from my own error: I recorded the AirPods Pro 2
#: line as having ~130px glyphs, but that figure came from a FAILED pairing
#: whose height was measured on whatever the crop happened to contain rather
#: than on text. Measured properly the line is 32px. The floor built on that
#: number excluded all four genuine labels.
#:
#: METHODOLOGICAL WARNING: downscaling a sharp image is NOT equivalent to a
#: natively low-resolution capture, so the synthetic noise curve recorded in the
#: knowledge base flatters low resolutions and must not be used to set this.
MINIMUM_GLYPH_HEIGHT_PX = 25


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def score_font_match(
    comparisons: list[GlyphComparison], glyph_heights: list[float] | None = None
) -> FontScore | None:
    """Reduce per-glyph deviations to a scale-invariant font score."""
    if not comparisons:
        return None
    deviations = [c.deviation_percent for c in comparisons]
    offset = _median(deviations)
    scatter = _median([abs(d - offset) for d in deviations])
    return FontScore(
        glyphs=len(comparisons),
        offset_percent=round(offset, 2),
        scatter_percent=round(scatter, 2),
        letters_measured=sum(1 for c in comparisons if c.character.isalpha()),
        median_glyph_height_px=_median([float(h) for h in glyph_heights])
        if glyph_heights
        else float(MINIMUM_GLYPH_HEIGHT_PX),
    )


@dataclass(frozen=True)
class TextLine:
    """A detected row of similarly-sized glyphs, tightly bounded."""

    box: tuple[int, int, int, int]
    glyph_count: int
    median_glyph_height: float

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]


def find_text_lines(
    image: Image.Image,
    *,
    min_glyphs: int = 4,
    height_tolerance: float = 0.45,
    pad: int = 2,
) -> list[TextLine]:
    """Locate text lines and return their TIGHT bounds.

    Bounds come from the components themselves rather than from a sliding band,
    which removes clipping by construction. Sliding bands were the dominant
    error source: moving one 36px down a genuine label changed the font score
    from 0.69% to 4.15% and the apparent glyph height from 30px to 66px, purely
    from cutting glyphs at the edge.

    Rows are grouped by vertical overlap and kept only when several components
    share a consistent height, which is what set text does and what print
    speckle, barcodes and background clutter do not.
    """
    mask = adaptive_ink_mask(image)
    components = raw_components(mask, threshold=128, min_ink_pixels=15)
    if not components:
        return []

    width, height = image.size
    # Anything spanning a large fraction of the frame is background, not a glyph.
    components = [
        box for box in components
        if (box[2] - box[0]) < width * 0.5 and (box[3] - box[1]) < height * 0.5
    ]
    if not components:
        return []

    rows: list[list[tuple[int, int, int, int]]] = []
    for box in sorted(components, key=lambda b: (b[1], b[0])):
        for row in rows:
            top = min(b[1] for b in row)
            bottom = max(b[3] for b in row)
            overlap = min(box[3], bottom) - max(box[1], top)
            shorter = min(box[3] - box[1], bottom - top)
            if shorter > 0 and overlap / shorter >= 0.5:
                row.append(box)
                break
        else:
            rows.append([box])

    # Split each row at column gaps. Apple's label sets a multilingual paragraph
    # beside a part-number block, and grouping purely by vertical overlap puts
    # unrelated words from both columns on one detected line - 55 components
    # where the intended text has 34. A column gap is far wider than a word gap,
    # so the same median-multiple test separates them.
    rows = [segment for row in rows for segment in _split_at_column_gaps(row)]

    lines: list[TextLine] = []
    for row in rows:
        if len(row) < min_glyphs:
            continue
        heights = sorted(b[3] - b[1] for b in row)
        median = heights[len(heights) // 2]
        consistent = [h for h in heights if abs(h - median) <= median * height_tolerance]
        if len(consistent) < min_glyphs:
            continue
        lines.append(
            TextLine(
                box=(
                    max(0, min(b[0] for b in row) - pad),
                    max(0, min(b[1] for b in row) - pad),
                    min(width, max(b[2] for b in row) + pad),
                    min(height, max(b[3] for b in row) + pad),
                ),
                glyph_count=len(row),
                median_glyph_height=float(median),
            )
        )
    lines.sort(key=lambda line: line.box[1])
    return lines


def _split_at_column_gaps(
    row: list[tuple[int, int, int, int]], gap_multiple: float = 6.0
) -> list[list[tuple[int, int, int, int]]]:
    """Split one detected row wherever a gap is far wider than a word space.

    Word gaps sit around twice the median inter-glyph gap; a column gutter is an
    order of magnitude wider. The multiple is deliberately high so ordinary word
    spacing never splits a line.
    """
    if len(row) < 2:
        return [row]

    ordered = sorted(row, key=lambda box: box[0])
    gaps = [ordered[i + 1][0] - ordered[i][2] for i in range(len(ordered) - 1)]
    positive = [gap for gap in gaps if gap > 0]
    if not positive:
        return [ordered]
    typical = _median([float(gap) for gap in positive])
    threshold = max(typical * gap_multiple, 1.0)

    segments: list[list[tuple[int, int, int, int]]] = [[ordered[0]]]
    for gap, box in zip(gaps, ordered[1:]):
        if gap > threshold:
            segments.append([box])
        else:
            segments[-1].append(box)
    return segments


def adaptive_ink_mask(image: Image.Image, radius: int = 12, offset: int = 10) -> Image.Image:
    """Mark pixels meaningfully darker than their own neighbourhood.

    A single global threshold cannot separate print from a photograph: it splits
    box from background, so on a patterned surface the pattern becomes ink and
    the grey-on-white text becomes background. Comparing each pixel to a local
    mean removes that dependence on what else is in frame and handles uneven
    lighting across a curved box face.
    """
    grey = image.convert("L")
    local_mean = grey.filter(ImageFilter.BoxBlur(radius))
    ink = Image.new("L", grey.size, 255)
    ink.putdata([
        0 if pixel < mean - offset else 255
        for pixel, mean in zip(grey.get_flattened_data(), local_mean.get_flattened_data())
    ])
    return ink


def isolate_dominant_line(
    segments: list[tuple[tuple[int, int, int, int], Image.Image]],
    expected_count: int | None = None,
) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
    """Keep only the largest horizontal band of components.

    A crop rarely contains exactly one line. On the owner's AirPods Pro 1 label
    the barcode sat a few pixels under the serial text, and its bars were counted
    as glyphs - which shifted every pairing and swung the font score from 2.25%
    to 5.52% depending on nothing but where the crop edge fell. That is a false
    accusation waiting to happen on a genuine box.

    Components are grouped into rows by vertical overlap. When the expected
    character count is known the closest-matching row wins; otherwise the most
    populous does.

    That distinction matters: a barcode has far more bars than the text above it
    has letters, so "most populous" picks the barcode every time on a label crop
    that includes both. Measuring barcode bars as glyphs is how a counterfeit
    label produced a component count that matched the text exactly while
    describing something else entirely.
    """
    if len(segments) < 3:
        return segments

    rows: list[list[tuple[tuple[int, int, int, int], Image.Image]]] = []
    for item in sorted(segments, key=lambda s: s[0][1]):
        box = item[0]
        for row in rows:
            top = min(b[0][1] for b in row)
            bottom = max(b[0][3] for b in row)
            overlap = min(box[3], bottom) - max(box[1], top)
            shorter = min(box[3] - box[1], bottom - top)
            if shorter > 0 and overlap / shorter >= 0.5:
                row.append(item)
                break
        else:
            rows.append([item])

    if expected_count is not None:
        dominant = min(rows, key=lambda row: (abs(len(row) - expected_count), -len(row)))
    else:
        dominant = max(rows, key=len)
    return sorted(dominant, key=lambda s: s[0][0])


def compare_text_line(image: Image.Image, expected_text: str) -> list[GlyphComparison]:
    """Measure a photographed text line against San Francisco.

    ``expected_text`` is what the line says - supplied by the extractor, since
    reading text is what an LLM is good at. Only characters in USABLE_GLYPHS are
    measured; the rest carry too little signal to survive blur.
    """
    letters_expected = len(
        [c for c in expected_text if not c.isspace() and c not in _SHORT_MARKS]
    )
    # Same ink detection as find_text_lines. Using Otsu here while layout
    # analysis used a local adaptive mask made the two disagree about what
    # counts as ink, so a line located with exactly the right glyph count was
    # then refused by the measurement step.
    mask = adaptive_ink_mask(image)
    boxes = raw_components(mask, threshold=128, min_ink_pixels=15)
    boxes.sort(key=lambda box: box[0])
    boxes = _merge_stacked(boxes)
    boxes = _drop_outliers(boxes)
    segments = [(box, image.crop(box)) for box in boxes]
    segments = isolate_dominant_line(segments, letters_expected)
    # Punctuation is dropped by the height filter, so compare against the
    # characters that survive it too.
    # Only marks the height filter actually removes. Brackets and quotes are
    # full-height, survive it, and must stay in the expected sequence or the
    # counts will never line up.
    letters = [c for c in expected_text if not c.isspace() and c not in _SHORT_MARKS]
    if len(segments) != len(letters):
        # Component count and character count still disagree, so a glyph split
        # or merged. Refuse rather than silently pairing the wrong shapes -
        # mispaired glyphs read as enormous deviations and would manufacture a
        # counterfeit verdict out of a segmentation bug.
        return []

    # A glyph markedly taller than the rest of its line has merged with
    # something - on the owner's AirPods Pro 1 label the final three characters
    # fused with barcode bars beneath them and measured 138-144px against a line
    # median of 93px. Their aspect ratios are meaningless, and including them
    # swung the font score from 2.25% to 5.52% on a genuine box. They are
    # skipped for MEASUREMENT but kept in the pairing, so alignment survives.
    # A glyph cut by the crop boundary has a truncated bounding box, so its
    # aspect ratio is meaningless. Sliding a crop band down a genuine AirPods 4
    # label by 36 pixels moved the score from 0.69% to 4.15% and the apparent
    # glyph height from 30px to 66px - all of it clipping, none of it typeface.
    # Reject the whole line rather than score a truncated one: a crop that
    # clips is a crop we cannot trust anywhere along it.
    # A glyph cut by the crop boundary has a truncated bounding box, so its
    # aspect ratio is meaningless: sliding a crop 36px down a genuine AirPods 4
    # label moved the score from 0.69% to 4.15% and the apparent glyph height
    # from 30px to 66px, all of it clipping. Clipped glyphs are skipped for
    # measurement and kept in the pairing, so one bad glyph does not discard
    # nineteen good ones - but if the whole line is clipped, nothing survives
    # and no score is produced, which is the correct outcome.
    _, height = image.size

    heights = [box[3] - box[1] for box, _ in segments]
    line_height = _median([float(h) for h in heights]) if heights else 0.0
    ceiling = line_height * MERGED_GLYPH_HEIGHT_RATIO if line_height else float("inf")

    results: list[GlyphComparison] = []
    for character, (box, glyph) in zip(letters, segments):
        if (box[3] - box[1]) > ceiling:
            continue
        if box[1] <= 0 or box[3] >= height:
            continue
        # Case must NOT be normalised. USABLE_GLYPHS was derived for uppercase
        # and digits; comparing a lowercase "e" against an uppercase "E"
        # reference reads as a 34% deviation on a genuine label.
        if character not in USABLE_GLYPHS:
            continue
        comparison = compare_glyph(character, glyph)
        if comparison is not None:
            results.append(comparison)
    return results


def analyse_label(
    image_path: "Path | str", box: tuple[int, int, int, int], expected_text: str
) -> "FontScore | None":
    """Measure the typography of a label crop against San Francisco.

    Upscales small crops before segmentation - interpolation does not create
    detail, but it does stop thin strokes vanishing into single pixels, which is
    what makes neighbouring glyphs merge. It cannot rescue a line below the
    resolution floor; that is reported rather than papered over.
    """
    with Image.open(image_path) as source:
        crop = source.convert("L").crop(box)

    if crop.width < 4 or crop.height < 4:
        return None

    # Upscaling helps segmentation - thin strokes stop vanishing into single
    # pixels, which is what makes neighbouring glyphs merge - but it adds NO
    # information. Glyph heights must therefore be reported in original pixels,
    # or a 7px line upscaled 8x would claim 56px and sail past the resolution
    # floor the floor exists to enforce.
    upscale = 1
    if crop.height < 120:
        upscale = max(2, int(120 / max(1, crop.height)))
        crop = crop.resize((crop.width * upscale, crop.height * upscale), Image.LANCZOS)

    threshold = otsu_threshold(crop)
    segments = segment_glyphs(crop, threshold=threshold, min_ink_pixels=25)
    heights = [(box_[3] - box_[1]) / upscale for box_, _ in segments]
    comparisons = compare_text_line(crop, expected_text)
    if not comparisons:
        # Segmentation disagreed with the text. Report the size we could see, so
        # the caller can tell "too small to read" from "read it and it differs".
        if heights:
            return FontScore(
                glyphs=0, offset_percent=0.0, scatter_percent=0.0,
                letters_measured=0, median_glyph_height_px=_median(heights),
            )
        return None
    return score_font_match(comparisons, heights)


def _split_into_words(
    boxes: list[tuple[int, int, int, int]], gap_multiple: float = 1.8
) -> list[list[tuple[int, int, int, int]]]:
    """Group left-to-right components into words by horizontal gap.

    Inter-word gaps are markedly wider than inter-letter gaps, so the median gap
    on a line separates the two without needing to know the text.
    """
    if len(boxes) < 2:
        return [boxes] if boxes else []

    gaps = [boxes[i + 1][0] - boxes[i][2] for i in range(len(boxes) - 1)]
    positive = [gap for gap in gaps if gap > 0]
    typical = _median([float(g) for g in positive]) if positive else 0.0
    threshold = max(typical * gap_multiple, 1.0)

    groups: list[list[tuple[int, int, int, int]]] = [[boxes[0]]]
    for gap, box in zip(gaps, boxes[1:]):
        if gap > threshold:
            groups.append([box])
        else:
            groups[-1].append(box)
    return groups


def _pair_components(
    boxes: list[tuple[int, int, int, int]],
    expected_text: str,
    image: "Image.Image",
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Align components with the characters they should be, word by word.

    Requiring the whole line to match exactly made real print unusable: glyphs
    touch, glyphs break, punctuation segments unpredictably, and any one of
    those discarded the entire line. Word-level alignment loses only the word
    that went wrong.
    """
    ordered = _merge_stacked(sorted(boxes, key=lambda box: box[0]))
    if not ordered:
        return []

    groups = _split_into_words(ordered)
    expected_words = [
        "".join(c for c in word if c not in _SHORT_MARKS) for word in expected_text.split()
    ]
    expected_words = [word for word in expected_words if word]

    pairs: list[tuple[str, tuple[int, int, int, int]]] = []
    if len(groups) == len(expected_words):
        for group, word in zip(groups, expected_words):
            if len(group) == len(word):
                pairs.extend(zip(word, group))
    else:
        remaining = list(groups)
        for word in expected_words:
            for index, group in enumerate(remaining):
                if len(group) == len(word):
                    pairs.extend(zip(word, group))
                    remaining = remaining[index + 1:]
                    break
    if not pairs:
        return []

    heights = [float(box[3] - box[1]) for box in ordered]
    ceiling = _median(heights) * MERGED_GLYPH_HEIGHT_RATIO
    _, image_height = image.size
    return [
        (character, box)
        for character, box in pairs
        if character in USABLE_GLYPHS
        and (box[3] - box[1]) <= ceiling          # merged with a neighbouring mark
        and box[1] > 0 and box[3] < image_height  # clipped by the crop edge
    ]


def measure_components(
    image: Image.Image, boxes: list[tuple[int, int, int, int]], expected_text: str
) -> list[GlyphComparison]:
    """Pair ALREADY-SEGMENTED components against text and measure them.

    Segmenting twice is what kept breaking this. Line detection and measurement
    each ran their own segmentation with slightly different filters, so a line
    located with exactly the right glyph count - 40 found, 40 expected - would
    then be refused, and no amount of aligning the thresholds fixed it because
    the filters themselves differed. One segmentation, one component list, one
    count.
    """
    results: list[GlyphComparison] = []
    for character, box in _pair_components(boxes, expected_text, image):
        comparison = compare_glyph(character, image.crop(box))
        if comparison is not None:
            results.append(comparison)
    return results


# --------------------------------------------------------------------------- #
# Shape descriptor
# --------------------------------------------------------------------------- #
#
# The aspect ratio above reduces a glyph to its bounding box, which is exactly
# the thing a crop edge, a blur radius and a resampling step all change: moving
# one crop edge on a genuine label moved its score from 2.25% to 5.52%. This
# describes where the ink actually sits instead, which survives those and
# separates San Francisco from Helvetica, Arial, Verdana and Geneva by 1.7-2.0x
# on clean renders.

#: Bands used for the ink profiles. Six is enough to place a crossbar and
#: coarse enough to survive blur at the resolutions that matter.
SHAPE_BINS = 6

SHAPE_FEATURES = (
    ["aspect", "density", "stroke", "centroid_x", "centroid_y"]
    + [f"row{i}" for i in range(SHAPE_BINS)]
    + [f"col{i}" for i in range(SHAPE_BINS)]
)

#: Features live on different scales; these bring them to comparable size so no
#: single one dominates the distance.
_SHAPE_WEIGHTS = [4.0, 6.0, 8.0, 6.0, 6.0] + [10.0] * (2 * SHAPE_BINS)


@dataclass(frozen=True)
class GlyphShape:
    """Where a glyph's ink sits, independent of how big it was photographed."""

    character: str
    height: int
    aspect: float
    density: float
    stroke: float
    row_profile: tuple[float, ...]
    col_profile: tuple[float, ...]
    centroid_x: float
    centroid_y: float

    def vector(self) -> list[float]:
        return [
            self.aspect, self.density, self.stroke, self.centroid_x, self.centroid_y,
            *self.row_profile, *self.col_profile,
        ]


def describe_glyph(image: "Image.Image", character: str, threshold: int) -> "GlyphShape | None":
    """Measure one glyph's ink distribution. None when there is too little ink."""
    grey = image.convert("L")
    width, height = grey.size
    if width < 3 or height < 6:
        return None
    pixels = list(grey.getdata())
    rows = [
        [1 if pixels[y * width + x] < threshold else 0 for x in range(width)]
        for y in range(height)
    ]
    ink = sum(sum(row) for row in rows)
    if ink < 8:
        return None

    runs: list[int] = []
    for row in rows:
        run = 0
        for value in row:
            if value:
                run += 1
            elif run:
                runs.append(run)
                run = 0
        if run:
            runs.append(run)

    row_profile = []
    for band in range(SHAPE_BINS):
        low, high = int(band * height / SHAPE_BINS), int((band + 1) * height / SHAPE_BINS)
        row_profile.append(sum(sum(rows[y]) for y in range(low, max(high, low + 1))) / ink)
    col_profile = []
    for band in range(SHAPE_BINS):
        low, high = int(band * width / SHAPE_BINS), int((band + 1) * width / SHAPE_BINS)
        col_profile.append(
            sum(rows[y][x] for y in range(height) for x in range(low, max(high, low + 1))) / ink
        )

    sum_x = sum(x for y in range(height) for x in range(width) if rows[y][x])
    sum_y = sum(y for y in range(height) for x in range(width) if rows[y][x])

    return GlyphShape(
        character=character,
        height=height,
        aspect=width / height,
        density=ink / (width * height),
        stroke=(_median([float(r) for r in runs]) / height) if runs else 0.0,
        row_profile=tuple(row_profile),
        col_profile=tuple(col_profile),
        centroid_x=(sum_x / ink) / width,
        centroid_y=(sum_y / ink) / height,
    )


def reference_shape(character: str, height: int, blur: float = 0.6) -> "GlyphShape | None":
    """San Francisco's own shape for this character, rendered at this height."""
    for size in range(max(height, 8), height * 4):
        glyph = render_glyph(character, size=size, blur=0.0)
        box = glyph.point(lambda pixel: 255 if pixel < 128 else 0).getbbox()
        if box and (box[3] - box[1]) >= height:
            crop = glyph.crop(box)
            scale = height / (box[3] - box[1])
            crop = crop.resize((max(1, round(crop.width * scale)), height), Image.LANCZOS)
            if blur:
                crop = crop.filter(ImageFilter.GaussianBlur(blur))
            return describe_glyph(crop, character, 128)
    return None


def shape_distance(pairs: list[tuple["GlyphShape", "GlyphShape"]]) -> float | None:
    """Median per-glyph distance from San Francisco, with the label's own bias out.

    Blur, exposure and resolution push every glyph on one label the same way.
    Subtracting the label's median shift per feature leaves the part that is
    actually about letterform rather than about the photograph.
    """
    if len(pairs) < MINIMUM_GLYPHS_TO_SCORE:
        return None
    count = len(SHAPE_FEATURES)
    rows = [
        [measured.vector()[i] - reference.vector()[i] for i in range(count)]
        for measured, reference in pairs
    ]
    bias = [_median([row[i] for row in rows]) for i in range(count)]
    distances = [
        sum(abs(row[i] - bias[i]) * _SHAPE_WEIGHTS[i] for i in range(count)) / count
        for row in rows
    ]
    return _median(distances)


def measure_shapes(
    image: "Image.Image", boxes: list[tuple[int, int, int, int]], expected_text: str
) -> list[tuple["GlyphShape", "GlyphShape"]]:
    """Pair already-segmented components with text and describe both sides."""
    pairs: list[tuple[GlyphShape, GlyphShape]] = []
    for character, box in _pair_components(boxes, expected_text, image):
        crop = image.crop(box)
        measured = describe_glyph(crop, character, otsu_threshold(crop))
        if measured is None:
            continue
        reference = reference_shape(character, measured.height)
        if reference is None:
            continue
        pairs.append((measured, reference))
    return pairs


def analyse_label_lines(
    image: "Image.Image", expected_texts: list[str]
) -> "FontScore | None":
    """Score a whole label by pooling glyphs from every line it can read.

    Measuring one line is not enough. On a single six-glyph line Arial scored
    1.36%, identical to San Francisco, and passed as genuine - whether a
    substitute is caught depends on which letters that line happens to contain.
    Pooling a second line catches every substitute tested, and pooling four
    gives San Francisco 0.62% against 5.1%-7.1%.

    ``expected_texts`` is what the lines say, supplied by the extractor. Lines
    whose components disagree with their text are skipped rather than guessed
    at, so a partially readable label still scores on the lines that worked.
    """
    lines = find_text_lines(image)
    if not lines:
        return None

    mask = adaptive_ink_mask(image)
    components = raw_components(mask, threshold=128, min_ink_pixels=15)

    pooled: list[GlyphComparison] = []
    shapes: list[tuple[GlyphShape, GlyphShape]] = []
    heights: list[float] = []
    # Each expected text claims a DISTINCT detected line. Choosing independently
    # let two texts of similar length pick the same line while others went
    # unused, so a three-line label contributed only one line's glyphs.
    available = list(lines)
    for text in expected_texts:
        if not available:
            break
        wanted = len([c for c in text if not c.isspace() and c not in _SHORT_MARKS])
        candidate = min(available, key=lambda line: abs(line.glyph_count - wanted))
        available.remove(candidate)
        left, top, right, bottom = candidate.box
        # Reuse the components that DEFINED this line - never re-segment.
        inside = [
            box for box in components
            if box[0] >= left and box[2] <= right and box[1] >= top and box[3] <= bottom
        ]
        comparisons = measure_components(image, inside, text)
        if not comparisons:
            continue
        pooled.extend(comparisons)
        shapes.extend(measure_shapes(image, inside, text))
        # Height of the glyphs actually MEASURED, not of every component in the
        # line. Commas, full stops and the dots off an "i" are a third the
        # height of a letter, and including them dragged the reported glyph
        # height down by a third - which is the number the resolution gate
        # depends on.
        heights.extend(
            float(box[3] - box[1]) for _, box in _pair_components(inside, text, image)
        )

    if not pooled:
        return None
    score = score_font_match(pooled, heights)
    if score is None:
        return None
    return replace(score, shape_distance=shape_distance(shapes))


def sf_available() -> bool:
    return Path(SF_FONT_PATH).is_file()
