"""Deterministic image-quality measurement. No LLM, no network, no cost.

Typography judgements are only meaningful on sharp images. Blur, JPEG
compression and viewing angle all reproduce the appearance of bad kerning and
wrong letterforms on genuine packaging - which is exactly how the kerning rule
scored 0-for-2 against a labelled corpus, firing only on real units.

MEASURED RESULT - the obvious hypothesis is wrong. Across 20 labelled posts,
sharpness does NOT separate true kerning flags from false ones. The sharpest
image in the whole flagged set (post 5, score 513) is a genuine unit that drew a
false kerning flag; the blurriest (post 16, score 41) is a real counterfeit.
Mean sharpness where kerning fired on genuine units is 311, against 220 on
fakes - sharper images produce MORE false font flags, not fewer.

So a blur gate cannot rescue an LLM's font judgement. What sharpness does
predict well is extraction failure: of five posts below 150, none yielded a
model number and four were inconclusive. That is what this module is for -
telling a buyer their photographs are unusable, and deciding which tier of
geometric font comparison is worth attempting.

Everything here is pure Pillow arithmetic, so it costs nothing and returns the
same answer every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

#: Discrete Laplacian. Variance of the response is the standard sharpness proxy:
#: a sharp image has strong edges and therefore high variance, a blurred one has
#: its high frequencies smoothed away.
_LAPLACIAN = ImageFilter.Kernel(
    size=(3, 3),
    kernel=[0, 1, 0, 1, -4, 1, 0, 1, 0],
    scale=1,
    offset=128,
)

#: Sharpness is scale-dependent, so measure at a fixed working size or the score
#: says more about resolution than about focus.
_WORKING_EDGE_PX = 1024


@dataclass(frozen=True)
class Sharpness:
    score: float
    width: int
    height: int

    def is_sharp(self, threshold: float) -> bool:
        return self.score >= threshold


def measure_sharpness(image_path: Path | str) -> Sharpness:
    """Laplacian variance of the greyscale image, normalised for size."""
    with Image.open(image_path) as image:
        image = image.convert("L")
        width, height = image.size
        longest = max(image.size)
        if longest > _WORKING_EDGE_PX:
            scale = _WORKING_EDGE_PX / longest
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )
        variance = ImageStat.Stat(image.filter(_LAPLACIAN)).var[0]
    return Sharpness(score=round(variance, 2), width=width, height=height)


def sharpest(image_paths: list[Path | str]) -> Sharpness | None:
    """Best-focused image in a set.

    Typography is judged from whichever photograph shows the print most clearly,
    so a set is only unusable when *every* image in it is soft.
    """
    scores = [measure_sharpness(path) for path in image_paths]
    return max(scores, key=lambda s: s.score) if scores else None
