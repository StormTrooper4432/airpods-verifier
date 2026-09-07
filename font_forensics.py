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
from dataclasses import dataclass
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
#: the drift blur induces. Measured, not chosen: P, N, X, Y, H and W all fall
#: below that bar and are excluded - their blur drift rivals the font signal.
USABLE_GLYPHS = frozenset("5ETCR3S4GOZF8K1092")

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
    for character in string.ascii_uppercase + string.digits:
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
            and self.glyphs >= 5
            and self.median_glyph_height_px >= MINIMUM_GLYPH_HEIGHT_PX
        )

    @property
    def too_small_to_score(self) -> bool:
        return self.median_glyph_height_px < MINIMUM_GLYPH_HEIGHT_PX

    @property
    def consistent_with_san_francisco(self) -> bool:
        return self.scatter_percent < FONT_SCATTER_THRESHOLD


#: Below this, per-glyph deviations agree and the line is consistent with San
#: Francisco. Genuine photographed labels measured 0.90%; SF rendered
#: synthetically 0.41-0.71%; substitute typefaces 3.3-7.9%. The cut sits between
#: those populations rather than at either edge.
FONT_SCATTER_THRESHOLD = 2.0

#: Digits are tabular and carry no signal, so a line needs real letters.
MINIMUM_LETTERS_TO_SCORE = 3

#: Minimum glyph height, in pixels, for the score to mean anything.
#:
#: Measured by rendering the same string at decreasing sizes: at a cap height of
#: 29px San Francisco scatters 0.94% against Helvetica's 6.32%, clean
#: separation. At 22px SF rises to 1.70% - still under the 2.0% threshold but
#: with almost no margin. Below 18px segmentation fails outright: strokes merge
#: and neighbouring glyphs join, so there is nothing to measure.
#:
#: This is the constraint that decides what the feature is FOR. Across 20 real
#: listing screenshots, exactly one contained a detectable text line, and its
#: glyphs were 7px tall - four times below the floor. Font forensics works for
#: someone photographing a box in front of them, and does not work for screening
#: posted listing photos.
MINIMUM_GLYPH_HEIGHT_PX = 25


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def score_font_match(
    comparisons: list[GlyphComparison], glyph_heights: list[int] | None = None
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


def compare_text_line(image: Image.Image, expected_text: str) -> list[GlyphComparison]:
    """Measure a photographed text line against San Francisco.

    ``expected_text`` is what the line says - supplied by the extractor, since
    reading text is what an LLM is good at. Only characters in USABLE_GLYPHS are
    measured; the rest carry too little signal to survive blur.
    """
    segments = segment_glyphs(image, threshold=otsu_threshold(image), min_ink_pixels=25)
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

    results: list[GlyphComparison] = []
    for character, (_, glyph) in zip(letters, segments):
        # Case must NOT be normalised. USABLE_GLYPHS was derived for uppercase
        # and digits; comparing a lowercase "e" against an uppercase "E"
        # reference reads as a 34% deviation on a genuine label.
        if character not in USABLE_GLYPHS:
            continue
        comparison = compare_glyph(character, glyph)
        if comparison is not None:
            results.append(comparison)
    return results


def sf_available() -> bool:
    return Path(SF_FONT_PATH).is_file()
