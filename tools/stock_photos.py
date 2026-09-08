"""Tell a seller's own photograph from the manufacturer's press shot.

A marketplace listing is mostly not evidence. Sellers pad listings with Apple's
own product renders, and those are worse than useless here: they are pictures
of a genuine unit attached to a listing that may not be, so a corpus built
without filtering them would train and evaluate this tool on the one thing it
must never learn from.

Two signals, in order of how much they can be trusted:

1. **The same image in more than one seller's listing.** A photograph of a
   specific box on a specific table cannot honestly appear in two unrelated
   listings. A press shot appears in hundreds. This needs no reference images
   and no judgement about what Apple's photography looks like, which is why it
   is the primary test.

2. **A clean white surround.** Press shots are cut out on pure white with no
   shadow, no hand and no room behind them. This is a weaker signal - a careful
   seller with a lightbox produces the same thing - so it only ever advises.

No dependency beyond Pillow, which the pipeline already requires.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

#: Side length of the grid the difference hash is computed on. The usual 8 is
#: too coarse for this subject: a white box held over a patterned surface has
#: almost no low-frequency structure to distinguish it, and an AirPods 1 box and
#: an AirPods Pro 1 box photographed the same way collided EXACTLY at 8x8 — the
#: hash saw "white square, hand, blue pattern" for both. Resolution fixes what
#: no threshold could:
#:
#:     hash grid   re-encode max   different photos, min
#:       8 ( 64)               4                       0   collides
#:      12 (144)               9                      22
#:      16 (256)              11                      29
#:      24 (576)              23                     118
#:
#: 16 leaves a clear gap on both sides of the cut and stays cheap to compute.
_HASH_SIDE = 16

#: Two hashes within this many bits are the same picture, re-encoded. Measured
#: at the 256-bit hash above, not guessed:
#:
#:     same image resized and recompressed        0 - 11
#:     same image, brightness or contrast shifted 0 - 11
#:     different photographs of the same box     29 and up
#:     two different boxes, shot alike           50
#:
#: 16 sits inside that gap. The two errors do not cost the same either: keeping
#: a press shot corrupts the corpus, while dropping a real photograph only makes
#: it smaller, and there is no shortage of listings.
DUPLICATE_DISTANCE = 16

#: A pixel this bright counts as backdrop rather than subject.
_WHITE = 244

#: Fraction of the border that must be near-white before a photograph is called
#: a cut-out. Set high because the cost of dropping a real seller photo is a
#: smaller corpus, while the cost of keeping a press shot is a corrupt one.
STOCK_BORDER_RATIO = 0.97


def difference_hash(image: Image.Image, side: int = _HASH_SIDE) -> int:
    """A perceptual hash: which way the brightness steps, left to right.

    Robust to the resizing and re-compression a marketplace applies, and to
    small exposure differences, because only the SIGN of each step is kept.
    """
    small = image.convert("L").resize((side + 1, side), Image.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(side):
        for column in range(side):
            left = pixels[row * (side + 1) + column]
            right = pixels[row * (side + 1) + column + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def border_whiteness(image: Image.Image, band: int = 6) -> float:
    """Fraction of the outer band that is near-white."""
    grey = image.convert("L")
    width, height = grey.size
    if width < band * 3 or height < band * 3:
        return 0.0
    pixels = grey.load()
    white = total = 0
    for x in range(width):
        for y in list(range(band)) + list(range(height - band, height)):
            total += 1
            white += pixels[x, y] >= _WHITE
    for y in range(band, height - band):
        for x in list(range(band)) + list(range(width - band, width)):
            total += 1
            white += pixels[x, y] >= _WHITE
    return white / total if total else 0.0


@dataclass(frozen=True)
class Candidate:
    """One image from one listing."""

    listing_id: str
    path: Path
    hash_bits: int
    border: float


@dataclass(frozen=True)
class Judgement:
    keep: bool
    reason: str


def inspect(listing_id: str, path: Path) -> Candidate | None:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image) or image
            image.load()
            return Candidate(
                listing_id=listing_id,
                path=path,
                hash_bits=difference_hash(image),
                border=border_whiteness(image),
            )
    except Exception:
        return None


def judge(candidates: list[Candidate]) -> dict[Path, Judgement]:
    """Decide which images are the sellers' own, across the whole batch.

    Cross-listing reuse is decided over every candidate at once, because the
    signal only exists between listings - no single image can be judged that
    way on its own.
    """
    # Cluster near-identical images, then count how many DISTINCT listings each
    # cluster spans. Sequential clustering is enough here: the batches are
    # thousands of images, not millions, and a press shot matches its own
    # cluster long before it drifts.
    clusters: list[list[Candidate]] = []
    for candidate in candidates:
        for cluster in clusters:
            if hamming(cluster[0].hash_bits, candidate.hash_bits) <= DUPLICATE_DISTANCE:
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])

    verdicts: dict[Path, Judgement] = {}
    for cluster in clusters:
        listings = {member.listing_id for member in cluster}
        if len(listings) > 1:
            reason = f"same image in {len(listings)} listings — a press shot, not evidence"
            for member in cluster:
                verdicts[member.path] = Judgement(False, reason)
            continue
        for member in cluster:
            if member.border >= STOCK_BORDER_RATIO:
                verdicts[member.path] = Judgement(
                    False,
                    f"cut out on white ({member.border:.0%} of the border) — probably a render",
                )
            else:
                verdicts[member.path] = Judgement(True, "the seller's own photograph")
    return verdicts


def summarise(verdicts: dict[Path, Judgement]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for judgement in verdicts.values():
        counts["kept" if judgement.keep else "dropped"] += 1
    return f"{counts['kept']} kept, {counts['dropped']} dropped"
