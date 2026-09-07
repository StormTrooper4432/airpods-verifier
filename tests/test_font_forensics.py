"""Tests for geometric font comparison.

The end-to-end claim - "this photographed label is not set in San Francisco" -
cannot be tested without labelled photographs of known-genuine labels. What IS
testable is the measurement engine underneath it, and the empirical claims the
glyph selection rests on. Those claims are what would silently rot.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageFilter

import font_forensics as ff

pytestmark = pytest.mark.skipif(
    not ff.sf_available(), reason="San Francisco font not present (non-macOS)"
)


class TestInkMeasurement:
    def test_a_blank_image_has_no_ink(self):
        assert ff.ink_aspect_ratio(Image.new("L", (100, 100), 255)) is None

    def test_a_wide_mark_reads_wider_than_tall(self):
        image = Image.new("L", (200, 200), 255)
        image.paste(0, (20, 90, 180, 110))
        ratio = ff.ink_aspect_ratio(image)
        assert ratio is not None and ratio > 4

    def test_aspect_ratio_is_scale_invariant(self):
        small = ff.ink_aspect_ratio(ff.render_glyph("R", size=60))
        large = ff.ink_aspect_ratio(ff.render_glyph("R", size=300))
        assert small == pytest.approx(large, rel=0.06)


class TestGlyphSelection:
    def test_declared_glyphs_match_what_measurement_derives(self):
        # USABLE_GLYPHS is a cached measurement, not a judgement call. If a font
        # update moves these, the constant is wrong and must be re-derived.
        assert ff.derive_usable_glyphs() == set(ff.USABLE_GLYPHS)

    @pytest.mark.parametrize("character", "PNXYHW")
    def test_excluded_glyphs_really_are_too_noisy(self, character):
        result = ff.measure_glyph_signal_to_noise(character)
        assert result is not None
        assert result[2] < 3.0, f"{character} was excluded but scores {result[2]:.1f}"

    @pytest.mark.parametrize("character", "5ETCR")
    def test_the_strongest_glyphs_clear_the_bar_comfortably(self, character):
        signal, noise, ratio = ff.measure_glyph_signal_to_noise(character)
        assert ratio >= 4.0
        assert signal > noise

    def test_every_usable_glyph_is_alphanumeric_uppercase(self):
        # Serial and model numbers are uppercase alphanumerics; that is where
        # this measurement gets applied.
        assert all(c.isalnum() and c.isupper() or c.isdigit() for c in ff.USABLE_GLYPHS)


class TestBlurRobustness:
    """The property the whole approach rests on: blur expands ink in both axes
    at similar rates, so the RATIO survives even when the glyph does not."""

    @pytest.mark.parametrize("character", "5ETCS")
    def test_ratio_survives_heavy_blur(self, character):
        sharp = ff.ink_aspect_ratio(ff.render_glyph(character))
        blurred = ff.ink_aspect_ratio(ff.render_glyph(character, blur=6.0))
        assert abs(100 * (blurred - sharp) / sharp) < 8.0

    def test_blur_destroys_shape_but_not_ratio(self):
        # Sanity check that the blur is actually severe - otherwise the test
        # above proves nothing.
        sharp = ff.render_glyph("R")
        blurred = sharp.filter(ImageFilter.GaussianBlur(6.0))
        difference = sum(abs(a - b) for a, b in zip(sharp.getdata(), blurred.getdata()))
        assert difference > 0


class TestSubstituteDiscrimination:
    @pytest.mark.parametrize("name", list(ff.SUBSTITUTE_FONT_PATHS))
    def test_each_substitute_is_measurably_different_somewhere(self, name):
        path = ff.SUBSTITUTE_FONT_PATHS[name]
        if not ff.Path(path).is_file():
            pytest.skip(f"{name} not installed")
        gaps = []
        for character in ff.USABLE_GLYPHS:
            sf = ff.reference_aspect(character)
            other = ff.reference_aspect(character, path)
            if sf and other:
                gaps.append(abs(100 * (other - sf) / sf))
        assert max(gaps) > 8.0, f"{name} is indistinguishable from SF on every usable glyph"

    def test_sf_compared_to_itself_is_a_perfect_match(self):
        for character in "RES50":
            comparison = ff.compare_glyph(character, ff.render_glyph(character))
            assert comparison is not None
            assert abs(comparison.deviation_percent) < 0.01

    def test_a_substitute_font_trips_the_provisional_threshold(self):
        helvetica = ff.SUBSTITUTE_FONT_PATHS["Helvetica"]
        if not ff.Path(helvetica).is_file():
            pytest.skip("Helvetica not installed")
        tripped = [
            c for c in "RESTC"
            if (cmp := ff.compare_glyph(c, ff.render_glyph(c, font_path=helvetica)))
            and abs(cmp.deviation_percent) >= ff.PROVISIONAL_DEVIATION_THRESHOLD
        ]
        assert tripped, "Helvetica should exceed the threshold on at least one strong glyph"


class TestRuledOutApproaches:
    """Two plausible approaches were measured and rejected. These pin the
    reasons, so nobody re-derives them from scratch."""

    def test_cross_ratio_does_not_discriminate_fonts(self):
        from PIL import ImageDraw, ImageFont

        def advances(path, text, size=400):
            font = ImageFont.truetype(path, size)
            draw = ImageDraw.Draw(Image.new("L", (10, 10)))
            return [draw.textlength(text[:i], font=font) for i in range(len(text) + 1)]

        def cross_ratio(p, i, j, k, m):
            a, b, c, d = p[i], p[j], p[k], p[m]
            return ((c - a) * (d - b)) / ((c - b) * (d - a))

        text = "Designed by Apple in California"
        sf = cross_ratio(advances(ff.SF_FONT_PATH, text), 0, 8, 17, 31)
        helvetica_path = ff.SUBSTITUTE_FONT_PATHS["Helvetica"]
        if not ff.Path(helvetica_path).is_file():
            pytest.skip("Helvetica not installed")
        other = cross_ratio(advances(helvetica_path, text), 0, 8, 17, 31)
        # Perspective-invariant, but font-blind: under 1% separation.
        assert abs(100 * (other - sf) / sf) < 1.0

    def test_font_metrics_are_not_ink(self):
        from PIL import ImageDraw, ImageFont

        def metric_aspect(path, ch, size=400):
            font = ImageFont.truetype(path, size)
            box = ImageDraw.Draw(Image.new("L", (10, 10))).textbbox((0, 0), ch, font=font)
            return (box[2] - box[0]) / (box[3] - box[1])

        # Metrics say "1" is a huge discriminator; measured ink says otherwise.
        helvetica = ff.SUBSTITUTE_FONT_PATHS["Helvetica"]
        if not ff.Path(helvetica).is_file():
            pytest.skip("Helvetica not installed")
        metric_gap = abs(
            100 * (metric_aspect(helvetica, "1") - metric_aspect(ff.SF_FONT_PATH, "1"))
            / metric_aspect(ff.SF_FONT_PATH, "1")
        )
        ink_gap = abs(
            100 * (ff.reference_aspect("1", helvetica) - ff.reference_aspect("1"))
            / ff.reference_aspect("1")
        )
        assert metric_gap > 20.0
        assert ink_gap < 6.0


class TestGenuineBaseline:
    """Measured against a confirmed-genuine North American label. These pin the
    empirical findings that synthetic rendering could not have predicted."""

    def test_q_is_excluded_by_evidence_not_by_the_synthetic_screen(self):
        # Q clears the signal-to-noise bar on rendered glyphs...
        result = ff.measure_glyph_signal_to_noise("Q")
        assert result is not None and result[2] >= 3.0
        # ...and still must not be used, because genuine print disagrees.
        assert "Q" in ff.EMPIRICALLY_EXCLUDED_GLYPHS
        assert "Q" not in ff.USABLE_GLYPHS

    def test_derived_set_subtracts_the_empirical_exclusions(self):
        assert ff.derive_usable_glyphs() == set(ff.USABLE_GLYPHS)
        assert not (ff.EMPIRICALLY_EXCLUDED_GLYPHS & ff.USABLE_GLYPHS)

    def test_the_genuine_baseline_sits_well_inside_the_threshold(self):
        # Recorded in knowledge/airpods_pro_2.yaml: 1.68% mean, 3.34% worst on a
        # genuine label. If a change pushes genuine print near the threshold,
        # the threshold is wrong or the measurement drifted.
        import yaml

        knowledge = yaml.safe_load(open("knowledge/airpods_pro_2.yaml", encoding="utf-8"))
        baseline = knowledge["font_baseline"]
        assert baseline["max_absolute_deviation_percent"] < ff.PROVISIONAL_DEVIATION_THRESHOLD
        assert baseline["excluded_glyph"] == "Q"


class TestSegmentation:
    def test_stacked_components_merge_into_one_glyph(self):
        # A dotted "i" is two ink-disconnected pieces in the same column.
        image = Image.new("L", (60, 100), 255)
        image.paste(0, (20, 10, 32, 22))    # the dot
        image.paste(0, (20, 35, 32, 90))    # the stem
        assert len(ff.segment_glyphs(image, min_ink_pixels=10)) == 1

    def test_separate_glyphs_stay_separate(self):
        image = Image.new("L", (120, 100), 255)
        image.paste(0, (10, 20, 40, 80))
        image.paste(0, (70, 20, 100, 80))
        assert len(ff.segment_glyphs(image, min_ink_pixels=10)) == 2

    def test_otsu_beats_a_fixed_threshold_on_off_white_paper(self):
        paper = Image.new("L", (100, 60), 205)   # off-white, as photographed
        paper.paste(60, (20, 15, 50, 45))        # grey ink, not black
        assert 60 < ff.otsu_threshold(paper) < 205

    def test_a_mismatched_line_is_refused_rather_than_mispaired(self):
        image = Image.new("L", (120, 100), 255)
        image.paste(0, (10, 20, 40, 80))
        assert ff.compare_text_line(image, "AB") == []
