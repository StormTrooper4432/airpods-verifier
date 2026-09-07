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

#: Glyphs whose ink aspect ratio separates SF from substitutes by at least 3x
#: the drift blur induces. Measured, not chosen: P, N, X, Y, H and W all fall
#: below that bar and are excluded - their blur drift rivals the font signal.
USABLE_GLYPHS = frozenset("5ETCR3S4GOZF8KQ1092")

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
    """Re-derive USABLE_GLYPHS from measurement. Used by the test suite."""
    usable = set()
    for character in string.ascii_uppercase + string.digits:
        result = measure_glyph_signal_to_noise(character)
        if result and result[2] >= minimum_ratio:
            usable.add(character)
    return usable


def sf_available() -> bool:
    return Path(SF_FONT_PATH).is_file()
