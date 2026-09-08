"""A corpus built from press shots would be worse than no corpus.

Sellers pad listings with Apple's own product renders. Those are photographs of
a genuine unit attached to a listing that may not be one, so letting them into
the corpus means measuring this tool against the single thing it must never
learn from. These tests pin the separation rather than trusting it.
"""

from __future__ import annotations

import io
import itertools
from pathlib import Path

import pytest

from PIL import Image, ImageDraw, ImageEnhance

from tools import stock_photos as sp

REFERENCE = Path(__file__).resolve().parent.parent / "knowledge"
#: Real hand-held photographs, one folder per unit. They are gitignored, so
#: every test that needs them skips cleanly on a fresh clone and in CI.
FOLDERS = ("real_app2", "real_ap1", "real_ap4", "real_app1", "real_ap4w_anc")


def _reference_photos() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for folder in FOLDERS:
        directory = REFERENCE / folder
        if directory.is_dir():
            found += [(folder, path) for path in sorted(directory.glob("*.JPG"))]
    return found


def _press_shot() -> Image.Image:
    """A stand-in for a manufacturer render: subject cut out on pure white.

    Deliberately not flat. A perfectly uniform render is a degenerate test
    subject — brightening it clips every pixel to 255 and destroys the hash,
    which says nothing about real press photography. Real renders carry a
    contact shadow and a soft product gradient, so this one does too, while the
    outer border stays pure white as a cut-out's does.
    """
    canvas = Image.new("RGB", (600, 600), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    # Contact shadow beneath the product, well inside the border.
    for step in range(14):
        grey = 232 + step
        draw.ellipse(
            (176 - step * 2, 452 + step, 424 + step * 2, 486 + step // 2),
            fill=(grey, grey, grey + 1),
        )
    # The product, with a vertical gradient down its face.
    draw.rounded_rectangle(
        (180, 150, 420, 470), radius=40, fill=(240, 240, 242), outline=(206, 206, 212)
    )
    for row in range(150, 470):
        shade = 246 - int((row - 150) / 320 * 26)
        draw.line((184, row, 416, row), fill=(shade, shade, shade + 2))
    draw.rounded_rectangle(
        (180, 150, 420, 470), radius=40, outline=(204, 204, 210), width=2
    )
    return canvas


def _reencode(image: Image.Image, width: int, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    resized = image.resize((width, round(image.height * width / image.width)))
    resized.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer)


class TestHashStability:
    def test_re_encoding_barely_moves_the_hash(self):
        """A marketplace resizes and recompresses everything it is given."""
        source = _press_shot()
        base = sp.difference_hash(source)
        for width, quality in ((520, 80), (400, 65), (300, 50)):
            moved = sp.hamming(base, sp.difference_hash(_reencode(source, width, quality)))
            assert moved <= sp.DUPLICATE_DISTANCE, (
                f"re-encoding at {width}px/q{quality} moved the hash {moved} bits, "
                f"past the {sp.DUPLICATE_DISTANCE}-bit cut — press shots would slip through"
            )

    def test_exposure_shifts_barely_move_the_hash(self):
        """Measured on a real photograph, which is the only place it means much.

        Brightening a cut-out-on-white render by 15% clips 98.7% of its pixels
        to pure white: the image stops carrying information, and the hash moves
        because there is nothing left to hash. That is a fact about flat white
        subjects, not about the hash, so the check runs on a photograph with
        real tonal range.
        """
        photos = _reference_photos()
        if not photos:
            pytest.skip("reference photographs not present")
        with Image.open(photos[0][1]) as source:
            source.load()
            base = sp.difference_hash(source)
            for factor in (0.85, 1.15):
                shifted = ImageEnhance.Brightness(source).enhance(factor)
                moved = sp.hamming(base, sp.difference_hash(shifted))
                assert moved <= sp.DUPLICATE_DISTANCE, (
                    f"a {factor:.0%} exposure shift moved the hash {moved} bits, past the "
                    f"{sp.DUPLICATE_DISTANCE}-bit cut"
                )

    def test_the_grid_is_fine_enough_to_tell_two_white_boxes_apart(self):
        """Why the hash is 16x16 and not the usual 8x8.

        At 8x8 an AirPods 1 box and an AirPods Pro 1 box photographed the same
        way collided exactly: the hash saw "white square, hand, patterned
        surface" for both, and no threshold can recover from a collision.
        """
        photos = _reference_photos()
        if len(photos) < 6:
            pytest.skip("reference photographs not present")
        distances = []
        for (folder_a, a), (folder_b, b) in itertools.combinations(photos, 2):
            if folder_a == folder_b:
                continue
            with Image.open(a) as first, Image.open(b) as second:
                distances.append(
                    sp.hamming(sp.difference_hash(first), sp.difference_hash(second))
                )
        assert min(distances) > sp.DUPLICATE_DISTANCE, (
            f"two different units hash within {min(distances)} bits, at or under the "
            f"{sp.DUPLICATE_DISTANCE}-bit cut — real photographs would be discarded as "
            f"press shots"
        )


class TestJudgement:
    def test_an_image_reused_across_listings_is_dropped(self):
        press = _press_shot()
        candidates = []
        for index in range(4):
            path = Path(f"/tmp/press-{index}.jpg")
            _reencode(press, 520 + index * 20, 78 + index).save(path)
            candidate = sp.inspect(f"seller-{index}", path)
            assert candidate is not None
            candidates.append(candidate)
        verdicts = sp.judge(candidates)
        assert all(not judgement.keep for judgement in verdicts.values())
        assert all("listings" in judgement.reason for judgement in verdicts.values())

    def test_one_seller_may_photograph_their_box_many_times(self):
        """Reuse only means anything BETWEEN listings."""
        photos = _reference_photos()
        if len(photos) < 4:
            pytest.skip("reference photographs not present")
        candidates = [
            candidate
            for _, path in photos
            if (candidate := sp.inspect("one-listing", path)) is not None
        ]
        verdicts = sp.judge(candidates)
        dropped = [path for path, judgement in verdicts.items() if not judgement.keep]
        assert not dropped, f"a single seller's own photographs were discarded: {dropped}"

    def test_real_photographs_survive_a_batch_containing_press_shots(self):
        photos = _reference_photos()
        if len(photos) < 6:
            pytest.skip("reference photographs not present")
        candidates = [
            candidate
            for folder, path in photos
            if (candidate := sp.inspect(folder, path)) is not None
        ]
        press = _press_shot()
        for index in range(4):
            path = Path(f"/tmp/press-batch-{index}.jpg")
            _reencode(press, 520 + index * 20, 78 + index).save(path)
            candidate = sp.inspect(f"seller-{index}", path)
            if candidate:
                candidates.append(candidate)

        verdicts = sp.judge(candidates)
        wrongly_dropped = [
            path
            for path, judgement in verdicts.items()
            if not judgement.keep and "knowledge" in str(path)
        ]
        wrongly_kept = [
            path
            for path, judgement in verdicts.items()
            if judgement.keep and "press-batch" in path.name
        ]
        assert not wrongly_dropped, f"real photographs discarded: {wrongly_dropped}"
        assert not wrongly_kept, f"press shots admitted to the corpus: {wrongly_kept}"

    def test_a_cut_out_on_white_is_dropped_even_from_one_listing(self):
        """The weaker signal, for a render that appears only once."""
        path = Path("/tmp/lone-press.jpg")
        _press_shot().save(path)
        candidate = sp.inspect("only-listing", path)
        assert candidate is not None
        assert candidate.border >= sp.STOCK_BORDER_RATIO
        verdict = sp.judge([candidate])[path]
        assert not verdict.keep and "white" in verdict.reason

    def test_a_hand_held_photograph_is_not_a_cut_out(self):
        photos = _reference_photos()
        if not photos:
            pytest.skip("reference photographs not present")
        _, path = photos[0]
        candidate = sp.inspect("listing", path)
        assert candidate is not None
        assert candidate.border < sp.STOCK_BORDER_RATIO
