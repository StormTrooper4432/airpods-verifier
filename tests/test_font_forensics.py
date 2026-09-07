"""Tests for geometric font comparison.

The end-to-end claim - "this photographed label is not set in San Francisco" -
cannot be tested without labelled photographs of known-genuine labels. What IS
testable is the measurement engine underneath it, and the empirical claims the
glyph selection rests on. Those claims are what would silently rot.
"""

from __future__ import annotations

import pathlib

from pathlib import Path

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

    def test_every_usable_glyph_is_alphanumeric(self):
        # Lowercase is now included. Label lines are digit-heavy and digits are
        # tabular, so an uppercase-only set left "Designed by Apple in
        # California" - present on every Apple box - with one usable glyph.
        assert all(c.isalnum() for c in ff.USABLE_GLYPHS)

    def test_lowercase_is_characterised(self):
        assert set("acfkmrstz") <= set(ff.USABLE_GLYPHS)

    def test_thin_lowercase_is_excluded(self):
        # i and l score S/N 0.3 and 0.1 - thin enough that blur destroys the
        # aspect ratio entirely.
        assert not ({"i", "l", "h", "w"} & set(ff.USABLE_GLYPHS))


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


class TestNoiseFiltering:
    """Print speckle and JPEG artefacts counted as glyphs, shifting every later
    pairing by one. That turned a segmentation bug into a +157% "font deviation"
    on a real label - a counterfeit verdict manufactured out of nothing."""

    def test_speckle_is_not_counted_as_a_glyph(self):
        image = Image.new("L", (300, 120), 255)
        for x in (20, 90, 160):                    # three real glyphs
            image.paste(0, (x, 20, x + 40, 100))
        image.paste(0, (250, 55, 258, 63))         # a speckle
        assert len(ff.segment_glyphs(image, min_ink_pixels=10)) == 3

    def test_short_marks_are_dropped_from_both_sides(self):
        # A full stop is filtered out of the image, so it must also be dropped
        # from the expected text or the counts can never agree.
        assert "." in ff._SHORT_MARKS
        assert "(" not in ff._SHORT_MARKS and ")" not in ff._SHORT_MARKS

    def test_a_line_too_degraded_to_segment_is_refused(self):
        # Refusing is the correct output. Reporting a deviation derived from
        # mispaired glyphs would be worse than reporting nothing.
        image = Image.new("L", (200, 100), 255)
        image.paste(0, (20, 20, 170, 80))          # one merged blob
        assert ff.compare_text_line(image, "ABCDE") == []

    def test_outlier_filter_needs_enough_components_to_have_a_median(self):
        image = Image.new("L", (120, 100), 255)
        image.paste(0, (10, 40, 30, 60))
        assert len(ff.segment_glyphs(image, min_ink_pixels=10)) == 1


def _render_line(font_path: str, text: str, size: int = 120) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, size)
    width = int(ImageDraw.Draw(Image.new("L", (10, 10))).textlength(text, font=font)) + size
    canvas = Image.new("L", (width, size * 2), 255)
    ImageDraw.Draw(canvas).text((size // 2, size // 4), text, font=font, fill=0)
    return canvas


class TestFontScore:
    """Scatter, not offset, is the font signal.

    Offset moves with imaging conditions: two genuine labels measured +2.4% and
    -6.0%, and -6.0% is indistinguishable from Arial's -5.9%. Typefaces differ
    from each other non-uniformly, so a wrong font makes per-glyph deviations
    disagree - that disagreement is what survives a camera.
    """

    # Long enough to clear MINIMUM_GLYPHS_TO_SCORE. A six-glyph line let Arial
    # pass as San Francisco, which is why the floor exists.
    LETTERS = "KC2WVC6R36 GTZE5 castrom ZKFRESTG artmask"

    def _score(self, font_path, text):
        image = _render_line(font_path, text)
        heights = [b[3] - b[1] for b, _ in ff.segment_glyphs(
            image, threshold=ff.otsu_threshold(image), min_ink_pixels=25)]
        return ff.score_font_match(ff.compare_text_line(image, text), heights)

    def test_san_francisco_matches_itself(self):
        score = self._score(ff.SF_FONT_PATH, self.LETTERS)
        assert score is not None and score.is_scoreable
        assert score.consistent_with_san_francisco
        assert score.scatter_percent < 1.0

    @pytest.mark.parametrize("name", list(ff.SUBSTITUTE_FONT_PATHS))
    def test_every_substitute_is_rejected(self, name):
        path = ff.SUBSTITUTE_FONT_PATHS[name]
        if not ff.Path(path).is_file():
            pytest.skip(f"{name} not installed")
        score = self._score(path, self.LETTERS)
        assert score is not None
        assert not score.consistent_with_san_francisco, f"{name} passed as SF"

    def test_the_threshold_sits_between_the_populations(self):
        # Not at either edge: genuine photos 0.90%, SF synthetic 0.42%,
        # substitutes 3.3-7.9%.
        assert 1.0 < ff.FONT_SCATTER_THRESHOLD < 3.0

    def test_offset_is_not_used_as_evidence(self):
        # A large offset with tight scatter must still pass - that is exactly a
        # genuine label photographed at a different scale.
        comparisons = [
            ff.GlyphComparison(c, ref * 0.94, ref)
            for c, ref in ((ch, ff.reference_aspect(ch)) for ch in "CERTSKGZOF35actm")
            if ref
        ]
        score = ff.score_font_match(comparisons)
        assert score.offset_percent < -5.0
        assert score.consistent_with_san_francisco

    def test_digit_only_lines_are_not_scoreable(self):
        # Tabular figures deviate uniformly, so digits carry no per-glyph signal:
        # SF and Helvetica both scatter 0.71% on a digits-only line.
        digits = "3056 3059 3055 3057 3058 3050"
        score = self._score(ff.SF_FONT_PATH, digits)
        assert score is not None
        assert score.letters_measured < ff.MINIMUM_LETTERS_TO_SCORE
        assert not score.is_scoreable

    def test_a_clipped_edge_glyph_does_not_drag_the_score(self):
        # Median absolute deviation, so one wild outlier is ignored. A crop that
        # clips its final character produced a -30% glyph on a genuine label.
        good = [ff.GlyphComparison(c, r * 1.01, r)
                for c, r in ((ch, ff.reference_aspect(ch)) for ch in "CERTSKGZOF35actm") if r]
        clipped = good + [ff.GlyphComparison("O", ff.reference_aspect("O") * 0.6,
                                             ff.reference_aspect("O"))]
        assert ff.score_font_match(clipped).consistent_with_san_francisco

    def test_empty_input_scores_nothing(self):
        assert ff.score_font_match([]) is None


class TestResolutionFloor:
    """The constraint that decides what this feature is for.

    Across 20 real listing screenshots exactly one contained a detectable text
    line, and its glyphs were 7px tall. Font forensics works for someone
    photographing a box in front of them; it does not work for screening posted
    listing photos, and pretending otherwise would produce confident nonsense.
    """

    def _score_at(self, size: int):
        text = "KC2WVC6R36 GTZE5 castrom ZKFRESTG"
        image = _render_line(ff.SF_FONT_PATH, text, size)
        heights = [b[3] - b[1] for b, _ in ff.segment_glyphs(
            image, threshold=ff.otsu_threshold(image), min_ink_pixels=25)]
        return ff.score_font_match(ff.compare_text_line(image, text), heights)

    def test_reference_quality_is_scoreable(self):
        score = self._score_at(120)
        assert score is not None and score.is_scoreable
        assert score.median_glyph_height_px >= ff.MINIMUM_GLYPH_HEIGHT_PX

    def test_a_close_up_is_scoreable(self):
        # 40px font renders ~29px caps, just under the raised 30px floor, so use
        # a size that clears it. The floor moved up after the noise curve showed
        # results below ~26px are erratic rather than merely imprecise.
        score = self._score_at(50)
        assert score is not None and score.is_scoreable

    def test_listing_screenshot_resolution_is_refused(self):
        # Either segmentation fails outright, or the score declares itself
        # unusable. Both are acceptable; silently returning a number is not.
        score = self._score_at(14)
        assert score is None or not score.is_scoreable

    def test_a_small_but_segmentable_line_is_flagged_too_small(self):
        score = ff.score_font_match(
            [ff.GlyphComparison(c, r, r) for c, r in
             ((ch, ff.reference_aspect(ch)) for ch in "CERTSKGZOF35actm") if r],
            glyph_heights=[9] * 16,
        )
        assert score.too_small_to_score
        assert not score.is_scoreable

    def test_the_floor_admits_real_photographs(self):
        # All four of the owner's genuine labels sit at 30-40px glyph height -
        # that is what a phone photograph of a retail box produces. A floor
        # above that excludes every real sample, which is what a previous value
        # of 60px did after I misread one line as having 130px glyphs.
        assert ff.MINIMUM_GLYPH_HEIGHT_PX <= 30


class TestUpscaleDoesNotFakeResolution:
    """Upscaling aids segmentation but adds no information. If glyph heights
    were reported post-upscale, a 7px listing screenshot enlarged 8x would claim
    56px and sail straight past the resolution floor."""

    def test_heights_are_reported_in_original_pixels(self, tmp_path):
        from PIL import ImageDraw, ImageFont

        # A genuinely tiny line, well under the floor.
        font = ImageFont.truetype(ff.SF_FONT_PATH, 14)
        image = Image.new("L", (300, 30), 255)
        ImageDraw.Draw(image).text((5, 5), "CERTS KGZ", font=font, fill=0)
        path = tmp_path / "tiny.png"
        image.save(path)

        score = ff.analyse_label(path, (0, 0, 300, 30), "CERTS KGZ")
        if score is not None:
            assert score.median_glyph_height_px < ff.MINIMUM_GLYPH_HEIGHT_PX
            assert not score.is_scoreable


class TestMergedGlyphExclusion:
    """A glyph fused with a neighbouring mark has a meaningless aspect ratio.
    On the owner's AirPods Pro 1 label the final three characters merged with
    barcode bars beneath them, measuring 138-144px against a line median of
    93px, and swung the score from 2.25% to 5.52% on a genuine box."""

    def test_a_merged_glyph_is_skipped_not_measured(self):
        image = Image.new("L", (400, 160), 255)
        for x in (20, 90, 160, 230):
            image.paste(0, (x, 40, x + 45, 110))     # four normal glyphs
        image.paste(0, (300, 40, 345, 155))          # one fused with a bar below
        segments = ff.segment_glyphs(image, min_ink_pixels=20)
        assert len(segments) == 5
        heights = [b[3] - b[1] for b, _ in segments]
        assert max(heights) > ff.MERGED_GLYPH_HEIGHT_RATIO * min(heights)

    def test_the_ratio_leaves_room_for_real_ascenders(self):
        # A lowercase "l" legitimately reaches ~1.35x a mixed-case line median,
        # so the cut must sit above that or genuine text loses glyphs.
        assert 1.3 < ff.MERGED_GLYPH_HEIGHT_RATIO < 1.6

    def test_pairing_survives_an_excluded_glyph(self):
        # Skipping for measurement must not shift the alignment of what remains.
        from PIL import ImageDraw, ImageFont

        font = ImageFont.truetype(ff.SF_FONT_PATH, 120)
        image = Image.new("L", (900, 260), 255)
        ImageDraw.Draw(image).text((20, 40), "CERTS KG", font=font, fill=0)
        results = ff.compare_text_line(image, "CERTS KG")
        assert [r.character for r in results] == [c for c in "CERTSKG" if c in ff.USABLE_GLYPHS]


class TestTextLineDetection:
    """Locating lines automatically, with bounds taken from the components
    themselves. Sliding a hand-picked band was the dominant error source:
    moving one 36px down a genuine label changed the score from 0.69% to 4.15%
    and the apparent glyph height from 30px to 66px, purely from clipping."""

    def _label(self, lines: list[str], size: int = 40) -> Image.Image:
        from PIL import ImageDraw, ImageFont

        font = ImageFont.truetype(ff.SF_FONT_PATH, size)
        image = Image.new("L", (900, 120 + len(lines) * size * 2), 245)
        draw = ImageDraw.Draw(image)
        for index, text in enumerate(lines):
            draw.text((40, 40 + index * size * 2), text, font=font, fill=30)
        return image

    def test_each_printed_line_is_found(self):
        found = ff.find_text_lines(self._label(["CERTS KGZ", "OFTE ZKRG", "SGT ECRO"]))
        assert len(found) == 3

    def test_bounds_are_tight_enough_not_to_clip(self):
        image = self._label(["CERTS KGZ"])
        line = ff.find_text_lines(image)[0]
        crop = image.crop(line.box)
        # No component may touch the top or bottom of its own tight crop.
        boxes = ff.raw_components(ff.adaptive_ink_mask(crop), threshold=128, min_ink_pixels=15)
        assert boxes
        assert not any(b[1] <= 0 or b[3] >= crop.height for b in boxes)

    def test_lines_come_back_in_reading_order(self):
        found = ff.find_text_lines(self._label(["FIRST GZ", "SECOND KR", "THIRD OE"]))
        assert [line.box[1] for line in found] == sorted(line.box[1] for line in found)

    def test_a_blank_image_yields_nothing(self):
        assert ff.find_text_lines(Image.new("L", (400, 200), 255)) == []


class TestUnfilteredComponents:
    """Layout analysis needs raw components; the single-line filters assume one
    line and destroy a multi-line label. On a counterfeit label they cut 221
    components to 24 and line detection found one line out of seven."""

    def test_raw_keeps_what_the_line_filters_discard(self):
        image = Image.new("L", (400, 300), 255)
        image.paste(0, (20, 20, 60, 80))      # tall
        image.paste(0, (100, 40, 130, 55))    # short - dropped relative to a line median
        image.paste(0, (200, 200, 260, 260))  # another line entirely
        raw = ff.raw_components(image, min_ink_pixels=10)
        filtered = ff.segment_glyphs(image, min_ink_pixels=10)
        assert len(raw) >= len(filtered)

    def test_ink_detection_is_shared_by_layout_and_measurement(self):
        # Layout used a local adaptive mask while measurement used Otsu, so a
        # line located with exactly the right glyph count was then refused.
        source = pathlib.Path("font_forensics.py").read_text()
        measurement = source[source.index("def compare_text_line"):]
        assert "adaptive_ink_mask" in measurement.split("def ")[1]


class TestMultiLinePooling:
    """One line is not enough. On a six-glyph line Arial scored 1.36% -
    identical to San Francisco - and passed as genuine, because whether a
    substitute is caught depends on which letters that line happens to hold."""

    LINES = [
        "Designed by Apple in California",
        "Assembled in Vietnam",
        "MTJV3AM/A AirPods Pro 2nd generation",
        "Compatible with iOS device and Mac",
    ]

    def _pool(self, font_path, lines):
        pooled, heights = [], []
        for text in lines:
            image = _render_line(font_path, text, 90)
            pooled += ff.compare_text_line(image, text)
            heights += [b[3] - b[1] for b, _ in ff.segment_glyphs(
                image, threshold=ff.otsu_threshold(image), min_ink_pixels=25)]
        return ff.score_font_match(pooled, heights)

    def test_pooling_catches_a_substitute_a_single_line_missed(self):
        arial = ff.SUBSTITUTE_FONT_PATHS["Arial"]
        if not ff.Path(arial).is_file():
            pytest.skip("Arial not installed")
        single = self._pool(arial, self.LINES[:1])
        pooled = self._pool(arial, self.LINES)
        assert not single.is_scoreable            # too few glyphs to trust
        assert pooled.differs_from_san_francisco  # caught once pooled

    @pytest.mark.parametrize("name", list(ff.SUBSTITUTE_FONT_PATHS))
    def test_every_substitute_is_caught_when_pooled(self, name):
        path = ff.SUBSTITUTE_FONT_PATHS[name]
        if not ff.Path(path).is_file():
            pytest.skip(f"{name} not installed")
        assert self._pool(path, self.LINES).differs_from_san_francisco

    def test_san_francisco_still_passes_when_pooled(self):
        score = self._pool(ff.SF_FONT_PATH, self.LINES)
        assert score.consistent_with_san_francisco
        assert score.glyphs >= ff.MINIMUM_GLYPHS_TO_SCORE

    def test_the_glyph_floor_sits_above_where_it_starts_working(self):
        # Six glyphs let Arial through; twelve caught every substitute.
        assert 12 < ff.MINIMUM_GLYPHS_TO_SCORE <= 25


class TestShapeDescriptorAndResolutionGate:
    """The gate is a measurement, not a preference. These re-derive it.

    The rule this replaced was calibrated on synthetic renders and produced a
    false positive on first contact with a photograph. So the number that
    matters - the glyph height at which a typeface substitution is still
    visible - is derived here rather than asserted, and the test fails if the
    constant drifts away from what the pixels support.
    """

    TEXT = "Model A3047 A3048 Designed by Apple in California Product of Vietnam"
    SUBSTITUTES = {
        "Helvetica": "/System/Library/Fonts/Helvetica.ttc",
        "Arial": "/System/Library/Fonts/Supplemental/Arial.ttf",
        "Verdana": "/System/Library/Fonts/Supplemental/Verdana.ttf",
    }

    @staticmethod
    def _photographed(path: str, render_px: int, text: str):
        """Degrade a clean render the way a phone camera degrades print."""
        import io
        import random

        from PIL import Image, ImageDraw, ImageFilter, ImageFont

        big = render_px * 4
        font = ImageFont.truetype(path, big)
        width = int(font.getlength(text)) + big
        canvas = Image.new("L", (width, big * 3), 246)
        ImageDraw.Draw(canvas).text((big // 2, big // 2), text, font=font, fill=28)
        canvas = canvas.filter(ImageFilter.GaussianBlur(big * 0.012))
        canvas = canvas.resize((width // 4, big * 3 // 4), Image.LANCZOS)
        random.seed(7)
        canvas.putdata(
            [max(0, min(255, v + random.gauss(0, 3.5))) for v in list(canvas.getdata())]
        )
        buffer = io.BytesIO()
        canvas.save(buffer, "JPEG", quality=82)
        buffer.seek(0)
        return Image.open(buffer).convert("L")

    def _score(self, path: str, render_px: int):
        image = self._photographed(path, render_px, self.TEXT)
        return ff.analyse_label_lines(image, [self.TEXT])

    @pytest.mark.skipif(not ff.sf_available(), reason="San Francisco not installed")
    def test_above_the_gate_a_substitute_typeface_separates(self):
        genuine = self._score(ff.SF_FONT_PATH, 200)
        assert genuine is not None and genuine.shape_distance is not None
        assert genuine.median_glyph_height_px >= ff.MEASURABLE_GLYPH_HEIGHT_PX

        for name, path in self.SUBSTITUTES.items():
            if not Path(path).exists():
                continue
            other = self._score(path, 200)
            assert other is not None and other.shape_distance is not None
            assert other.shape_distance > genuine.shape_distance * 1.5, (
                f"{name} is within 1.5x of San Francisco above the resolution "
                f"gate: {other.shape_distance:.4f} vs {genuine.shape_distance:.4f}"
            )

    @pytest.mark.skipif(not ff.sf_available(), reason="San Francisco not installed")
    def test_below_the_gate_the_measurement_can_invert(self):
        """Why the gate exists, stated as a test rather than as a comment.

        At the resolution a whole-box photograph produces, at least one
        substitute scores BETTER than San Francisco. A reading taken there is
        not weak evidence, it is misleading evidence, which is why the rule
        refuses to report a figure below the gate at all.
        """
        genuine = self._score(ff.SF_FONT_PATH, 40)
        assert genuine is not None
        assert genuine.median_glyph_height_px < ff.MEASURABLE_GLYPH_HEIGHT_PX

        scores = []
        for path in self.SUBSTITUTES.values():
            if not Path(path).exists():
                continue
            other = self._score(path, 40)
            if other is not None and other.shape_distance is not None:
                scores.append(other.shape_distance)
        if genuine.shape_distance is not None and scores:
            assert min(scores) < genuine.shape_distance, (
                "below the gate a substitute no longer scores better than San "
                "Francisco - re-derive MEASURABLE_GLYPH_HEIGHT_PX, the gate may "
                "now be set higher than the evidence requires"
            )

    @pytest.mark.skipif(not ff.sf_available(), reason="San Francisco not installed")
    def test_the_gate_sits_below_the_first_usable_separation(self):
        """The constant must not drift above the resolution it was derived from."""
        assert 30 < ff.MEASURABLE_GLYPH_HEIGHT_PX <= 60

    def test_a_shape_needs_enough_glyphs_before_it_reports(self):
        assert ff.shape_distance([]) is None
