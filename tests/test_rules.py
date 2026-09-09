"""Unit tests for the deterministic rules engine.

Everything here runs on synthetic extractions - no images, no API calls, no
network. That is the payoff of splitting extraction from judgement: the half of
the pipeline that decides whether someone loses $200 is exhaustively testable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import main as cli
from font_forensics import MEASURABLE_GLYPH_HEIGHT_PX
from rules_engine import (
    MAXIMUM_COPYRIGHT_YEAR,
    MIN_COVERAGE_FOR_VERDICT,
    MINIMUM_COPYRIGHT_YEAR,
    MODEL_NUMBERS,
    RULES,
    evaluate,
    evaluate_dict,
    lookup_model_number,
    resolve_variant,
)
from schema import (
    VARIANT_LIGHTNING,
    LocalAnalysis,
    VARIANT_UNKNOWN,
    VARIANT_USB_C,
    EvidenceStatus,
    Verdict,
    VisionExtraction,
)

SAMPLE_DATA = Path(__file__).parent / "sample_data"

#: A clean, fully-legible genuine USB-C unit. Tests override single fields so
#: each assertion isolates exactly one rule.
GENUINE_USB_C = {
    "claimed_model_variant": VARIANT_USB_C,
    "extracted_model_number": "A3047",
    "extracted_serial_number": "H4XQ7K2LMN9P",
    "serial_label_literal": "(S) Serial No. H4XQ7K2LMN9P",
    # Box and case agree; each earbud carries its own distinct serial.
    "part_number": "MTJV3AM/A",
    "origin_line": "Assembled in Vietnam",
    "serial_case_lid": "H4XQ7K2LMN9P",
    "serial_left_earbud": "GX7RT4PQWZ81",
    "serial_right_earbud": "KM3PD9TLVN52",
    "label_image_index": None,
    "label_left": None,
    "label_top": None,
    "label_right": None,
    "label_bottom": None,
    "label_line_texts": [],
    "copyright_year": 2023,
    "has_plastic_shrinkwrap": False,
    "has_paper_pull_tabs": True,
    "shrinkwrap_has_pull_tab": None,
    "shrinkwrap_looks_shoddy": None,
    "visible_panels": ["box_bottom_label"],
    "font_kerning_irregularities": False,
    "visible_spelling_errors": [],
}


#: A label photographed well enough to measure, matching San Francisco. Genuine
#: labels measured 0.90% scatter; the threshold is 2.0%.
GOOD_FONT = LocalAnalysis(
    font_scatter_percent=0.9, font_glyphs_measured=9, font_letters_measured=4,
    median_glyph_height_px=80.0, font_letterforms_resolved=True,
)

#: The same label set in something other than San Francisco.
WRONG_FONT = LocalAnalysis(
    font_scatter_percent=4.4, font_glyphs_measured=9, font_letters_measured=4,
    median_glyph_height_px=80.0, font_letterforms_resolved=True,
)

#: Photographed close enough that letterform is actually resolved, so the shape
#: descriptor - not the bounding-box ratio - is the statistic that applies.
RESOLVED_FONT = LocalAnalysis(
    font_scatter_percent=1.1, font_glyphs_measured=22, font_letters_measured=14,
    median_glyph_height_px=96.0, font_letterforms_resolved=True,
    font_shape_distance=0.041,
)

#: A label located but photographed too small to measure.
TOO_SMALL_FONT = LocalAnalysis(
    median_glyph_height_px=7.0, font_below_resolution_floor=True,
)


#: Wrapped with no tear-strip, AND photographed from an edge - which is what
#: makes the absence a finding rather than a limit of the viewpoint. Tests about
#: the tab rule use this; tests about the coverage guard override panels
#: themselves.
WRAPPED_NO_TAB = {
    "has_plastic_shrinkwrap": True,
    "has_paper_pull_tabs": False,
    "shrinkwrap_has_pull_tab": False,
    "visible_panels": ["box_bottom_label", "box_side"],
}


def make_extraction(**overrides) -> VisionExtraction:
    return VisionExtraction.model_validate({**GENUINE_USB_C, **overrides})


def status_of(report, parameter: str) -> EvidenceStatus:
    for item in report.evidence_ledger:
        if item.parameter == parameter:
            return item.status
    raise AssertionError(f"No ledger entry named {parameter!r}")


def detail_of(report, parameter: str) -> str:
    for item in report.evidence_ledger:
        if item.parameter == parameter:
            return item.detail
    raise AssertionError(f"No ledger entry named {parameter!r}")


# --------------------------------------------------------------------------- #
# Schema normalisation
# --------------------------------------------------------------------------- #


class TestSchemaNormalisation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("A3047", "A3047"),
            ("a3047", "A3047"),
            ("  A3047  ", "A3047"),
            ("Model A3047", "A3047"),
            ("Model No. A3047 / EMC 1234", "A3047"),
            ("", None),
            ("N/A", None),
            ("not visible", None),
            (None, None),
        ],
    )
    def test_model_number_is_normalised(self, raw, expected):
        assert make_extraction(extracted_model_number=raw).extracted_model_number == expected

    def test_unparseable_model_number_is_kept_for_the_engine_to_reject(self):
        # Silently dropping junk would turn a FAIL into an UNKNOWN and hide a
        # counterfeit behind "we could not read it".
        assert make_extraction(extracted_model_number="B1234").extracted_model_number == "B1234"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("h4xq7k2lmn9p", "H4XQ7K2LMN9P"),
            ("H4XQ-7K2L-MN9P", "H4XQ7K2LMN9P"),
            (" H4XQ 7K2L MN9P ", "H4XQ7K2LMN9P"),
            (None, None),
        ],
    )
    def test_serial_is_normalised(self, raw, expected):
        assert make_extraction(extracted_serial_number=raw).extracted_serial_number == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (2023, 2023),
            ("2023", 2023),
            ("© 2023", 2023),
            ("Copyright 2023 Apple Inc.", 2023),
            ("no year printed", None),
            (None, None),
        ],
    )
    def test_copyright_year_is_normalised(self, raw, expected):
        assert make_extraction(copyright_year=raw).copyright_year == expected

    def test_spelling_errors_accepts_a_bare_string(self):
        assert make_extraction(visible_spelling_errors="Designd").visible_spelling_errors == [
            "Designd"
        ]

    def test_extra_fields_are_rejected(self):
        # The LLM must not smuggle a verdict past the extraction boundary.
        with pytest.raises(Exception):
            VisionExtraction.model_validate({**GENUINE_USB_C, "is_genuine": True})


# --------------------------------------------------------------------------- #
# Tier 1 - hardware matrix
# --------------------------------------------------------------------------- #


class TestModelNumberRule:
    @pytest.mark.parametrize("number", sorted(MODEL_NUMBERS))
    def test_every_known_number_passes_against_its_own_variant(self, number):
        variant = MODEL_NUMBERS[number]["variant"]
        report = evaluate(
            make_extraction(claimed_model_variant=variant, extracted_model_number=number)
        )
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.PASS

    def test_a2698_is_recognised(self):
        # Present on genuine Lightning boxes; absent from the original spec doc.
        assert MODEL_NUMBERS["A2698"]["variant"] == VARIANT_LIGHTNING

    def test_lookup_returns_variant_and_component(self):
        assert lookup_model_number("A2968") == [(VARIANT_USB_C, "MagSafe Charging Case (USB-C)")]
        assert lookup_model_number("a2700") == [
            (VARIANT_LIGHTNING, "MagSafe Charging Case (Lightning)")
        ]
        assert lookup_model_number("A9999") == []

    def test_unspecified_components_are_not_invented(self):
        # Apple lists the three set numbers without saying which is left/right.
        # Guessing would put unsourced detail in the ledger.
        assert lookup_model_number("A3047") == [(VARIANT_USB_C, "part")]

    def test_cross_variant_model_number_fails(self):
        # A Lightning case number on a USB-C box: a real counterfeit tell.
        report = evaluate(
            make_extraction(claimed_model_variant=VARIANT_USB_C, extracted_model_number="A2700")
        )
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_unknown_apple_number_is_out_of_scope_not_a_failure(self):
        # Corpus evidence: A3063 appeared on a confirmed counterfeit AND a
        # confirmed genuine unit. An A-number this matrix does not cover means
        # "different generation", not "fake" - accusing on it produced 2 of the
        # 4 false positives in the first labelled run.
        report = evaluate(make_extraction(extracted_model_number="A9999"))
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN
        assert "no AirPods Pro generation" in detail_of(report, "Model Number Compatibility")
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    @pytest.mark.parametrize(
        "number, generation",
        [
            # Pro 3 numbers are NOT here any more: it was promoted to a
            # verified variant once its seal and copyright were sourced, so it
            # is named AND graded rather than named and declined.
            ("A2084", "AirPods Pro 1"),
            ("A2083", "AirPods Pro 1"),
        ],
    )
    def test_other_generations_are_named_not_accused(self, number, generation):
        # A3063 is a real Pro 3 number. It appeared in the corpus on one genuine
        # unit and one counterfeit; naming the product is the only honest answer.
        # The year is set inside Pro 3's sourced range so this tests the
        # model-number rule rather than tripping the copyright one.
        report = evaluate(make_extraction(extracted_model_number=number, copyright_year=2025))
        detail = detail_of(report, "Model Number Compatibility")
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN
        assert generation in detail
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_out_of_scope_items_do_not_get_an_authenticity_finding(self):
        # A counterfeit AirPods 4 and a genuine one look identical to a Pro 2
        # tool. Pro 3 is no longer the example here: its seal and copyright are
        # owner-sourced now, so it can be condemned on facts confirmed FOR IT.
        # The invariant is about products with no sourced facts at all.
        report = evaluate(make_extraction(extracted_model_number="A3053"))
        assert report.summary_verdict is Verdict.INCONCLUSIVE

    def test_malformed_identifier_still_fails(self):
        # Not an A-number at all: no Apple product carries this.
        report = evaluate(make_extraction(extracted_model_number="B1234"))
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.FAIL

    def test_missing_number_is_unknown_not_a_failure(self):
        report = evaluate(make_extraction(extracted_model_number=None))
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is Verdict.INCONCLUSIVE

    def test_unstated_variant_is_inferred_from_the_model_number(self):
        extraction = make_extraction(
            claimed_model_variant=VARIANT_UNKNOWN, extracted_model_number="A2931"
        )
        assert resolve_variant(extraction) == VARIANT_LIGHTNING
        report = evaluate(extraction)
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.PASS
        assert "inferred" in detail_of(report, "Model Number Compatibility")

    def test_unstated_variant_with_unreadable_number_stays_unknown(self):
        extraction = make_extraction(
            claimed_model_variant=VARIANT_UNKNOWN, extracted_model_number=None
        )
        assert resolve_variant(extraction) == VARIANT_UNKNOWN


# --------------------------------------------------------------------------- #
# Tier 2 - packaging
# --------------------------------------------------------------------------- #


class TestPackagingSealRule:
    def test_wrapping_alone_is_not_evidence(self):
        # The common "wrapped means fake" claim is wrong: genuine sealed units
        # do ship wrapped. Coding the blanket rule would accuse real stock.
        report = evaluate(
            make_extraction(has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=True)
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.PASS
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_wrap_without_a_side_pull_tab_fails(self):
        report = evaluate(make_extraction(**WRAPPED_NO_TAB))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_shoddy_wrap_alone_is_not_evidence(self):
        # Corpus post 6: the model reported wrap AND shoddy wrap on an opened
        # box with no plastic on it, turning a correct GENUINE into a false
        # accusation. Subjective quality judgements are recorded, not trusted.
        report = evaluate(
            make_extraction(
                has_plastic_shrinkwrap=True,
                shrinkwrap_has_pull_tab=True,
                shrinkwrap_looks_shoddy=True,
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.PASS
        assert "observation only" in detail_of(report, "Packaging Seal Type")

    def test_shoddy_wrap_without_a_tab_still_fails_on_the_tab(self):
        report = evaluate(make_extraction(**WRAPPED_NO_TAB, shrinkwrap_looks_shoddy=True))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL

    def test_unverifiable_wrap_is_unknown_even_if_called_shoddy(self):
        report = evaluate(
            make_extraction(
                has_plastic_shrinkwrap=True,
                shrinkwrap_has_pull_tab=None,
                shrinkwrap_looks_shoddy=True,
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN

    def test_wrapped_but_sides_not_visible_is_unknown(self):
        report = evaluate(
            make_extraction(has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=None)
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN

    def test_paper_pull_tabs_pass(self):
        report = evaluate(make_extraction())
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.PASS

    def test_opened_box_is_unknown_not_a_failure(self):
        report = evaluate(make_extraction(has_plastic_shrinkwrap=False, has_paper_pull_tabs=False))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN


class TestCopyrightYearRule:
    @pytest.mark.parametrize(
        "variant, model_number, year, expected",
        [
            # 2022 is the boundary that separates the two variants.
            (VARIANT_LIGHTNING, "A2700", 2022, EvidenceStatus.PASS),
            (VARIANT_USB_C, "A3047", 2022, EvidenceStatus.FAIL),
            (VARIANT_USB_C, "A3047", 2023, EvidenceStatus.PASS),
            (VARIANT_LIGHTNING, "A2700", 2021, EvidenceStatus.FAIL),
            (VARIANT_USB_C, "A3047", 2025, EvidenceStatus.PASS),
        ],
    )
    def test_release_year_boundaries(self, variant, model_number, year, expected):
        report = evaluate(
            make_extraction(
                claimed_model_variant=variant,
                extracted_model_number=model_number,
                copyright_year=year,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is expected

    def test_minimums_match_the_ground_truth_document(self):
        assert MINIMUM_COPYRIGHT_YEAR[VARIANT_USB_C] == 2023
        assert MINIMUM_COPYRIGHT_YEAR[VARIANT_LIGHTNING] == 2022

    def test_inferred_variant_tightens_the_year_constraint(self):
        # No stated variant, but a USB-C model number: 2022 must still fail.
        report = evaluate(
            make_extraction(
                claimed_model_variant=VARIANT_UNKNOWN,
                extracted_model_number="A3047",
                copyright_year=2022,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_unresolvable_variant_uses_the_permissive_minimum(self):
        report = evaluate(
            make_extraction(
                claimed_model_variant=VARIANT_UNKNOWN,
                extracted_model_number=None,
                copyright_year=2022,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.PASS

    def test_year_predating_the_whole_line_fails_even_without_a_variant(self):
        report = evaluate(
            make_extraction(
                claimed_model_variant=VARIANT_UNKNOWN,
                extracted_model_number=None,
                copyright_year=2019,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_the_ceiling_convicts_and_is_therefore_dated(self):
        """This replaced "the current year never fails".

        That test existed because the ceiling had aged: it sat at 2025 while the
        date was 2026-09-08, so a box reprinted that year was called HIGH RISK
        and nothing noticed. Asserting the current year can never fail was the
        fix, and it was the wrong shape — the owner then stated categorically
        that NO genuine Pro 2 box carries a 2026 copyright, which makes the
        current year exactly the year that should fail.

        Both cannot hold. The safety property is not "the calendar wins", it is
        "a convicting ceiling must have been confirmed recently by a human", and
        TestTheCopyrightCeilingCannotSilentlyAge enforces that instead. This
        keeps the two tied together so the conviction cannot outlive its source.
        """
        from rules_engine import KNOWLEDGE

        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 2 USB-C",
                extracted_model_number="A3047",
                copyright_year=MAXIMUM_COPYRIGHT_YEAR + 1,
            )
        )
        if status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL:
            assert KNOWLEDGE.get("observed_on"), (
                "the ceiling convicts, so it must carry the date it was last confirmed"
            )

    def test_a_year_above_the_ceiling_convicts_on_the_owners_statement(self):
        """Owner, asked directly and reaffirmed: no genuine Pro 2 box shows 2026.

        An absence claim, which rule 4 warns about. The objection was raised and
        reaffirmed, so it stands on the source hierarchy where the owner
        outranks everything but Apple's published pages — and Apple publishes
        nothing about packaging copyright years. Corroborated but not proved by
        corpus post 9, a confirmed counterfeit carrying 2026 on a Pro 2, which
        already fails typography so no verdict moves.
        """
        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 2 USB-C",
                extracted_model_number="A3047",
                copyright_year=MAXIMUM_COPYRIGHT_YEAR + 1,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_a_future_year_fails(self):
        """The one ceiling that cannot age: no box is printed in the future."""
        import datetime

        report = evaluate(make_extraction(copyright_year=datetime.date.today().year + 1))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_the_ceiling_year_itself_passes(self):
        # 2025 appears on genuine Pro 2 packaging; the bound must not be tighter.
        report = evaluate(make_extraction(copyright_year=MAXIMUM_COPYRIGHT_YEAR))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.PASS

    def test_ceiling_matches_the_expert_sourced_range(self):
        assert MAXIMUM_COPYRIGHT_YEAR == 2025

    def test_missing_year_is_unknown(self):
        report = evaluate(make_extraction(copyright_year=None))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.UNKNOWN


class TestSerialFormatRule:
    @pytest.mark.parametrize("serial", ["H4XQ7K2LMN", "H4XQ7K2LMN9P"])
    def test_valid_lengths_pass(self, serial):
        report = evaluate(make_extraction(extracted_serial_number=serial))
        assert status_of(report, "Serial Number Format") is EvidenceStatus.PASS

    @pytest.mark.parametrize("serial", ["H4XQ7K2", "H4XQ7K2LMN9", "H4XQ7K2LMN9PQRS"])
    def test_wrong_lengths_fail(self, serial):
        report = evaluate(make_extraction(extracted_serial_number=serial))
        assert status_of(report, "Serial Number Format") is EvidenceStatus.FAIL

    def test_illegal_characters_fail(self):
        report = evaluate(make_extraction(extracted_serial_number="H4XQ7K2LMN9!"))
        assert status_of(report, "Serial Number Format") is EvidenceStatus.FAIL

    def test_missing_serial_is_unknown(self):
        report = evaluate(make_extraction(extracted_serial_number=None))
        assert status_of(report, "Serial Number Format") is EvidenceStatus.UNKNOWN

    def test_pass_detail_warns_that_format_is_not_authenticity(self):
        # A cloned-but-valid serial is the exact scam this tool exists to catch,
        # so the ledger must never imply a format check settles it.
        report = evaluate(make_extraction())
        assert "cloned" in detail_of(report, "Serial Number Format")


class TestTypographyRule:
    def test_clean_print_passes(self):
        report = evaluate(make_extraction())
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.PASS

    def test_spelling_errors_are_listed_verbatim(self):
        report = evaluate(make_extraction(visible_spelling_errors=["Designd", "Califorina"]))
        detail = detail_of(report, "Typography & Print Quality")
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert "Designd" in detail and "Califorina" in detail

    def test_kerning_alone_is_not_evidence(self):
        # Measured 0-for-2 on the labelled corpus: fired standalone only on
        # genuine units. Blur, compression and angle mimic it.
        report = evaluate(make_extraction(font_kerning_irregularities=True))
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_kerning_is_still_noted_alongside_a_real_misspelling(self):
        report = evaluate(
            make_extraction(
                font_kerning_irregularities=True,
                visible_spelling_errors=["Appie", "swalliwed"],
            )
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert "kerning" in detail_of(report, "Typography & Print Quality")


# --------------------------------------------------------------------------- #
# Verdict aggregation
# --------------------------------------------------------------------------- #


class TestVerdictAggregation:
    def test_clean_unit_is_genuine_but_never_fully_certain(self):
        # A clean unit now also needs its letterforms measured. Without a close
        # enough photograph of the label the tool declines to clear the item,
        # because "wrong font" is the most-cited counterfeit tell and going
        # unchecked is missing evidence rather than absent risk.
        report = evaluate(make_extraction(), GOOD_FONT)
        assert report.summary_verdict is Verdict.GENUINE
        # Capped below 1.0: photo evidence cannot rule out a cloned serial.
        assert 0.9 <= report.confidence_score <= 0.95

    def test_stacked_disqualifiers_raise_confidence(self):
        # Wrap alone is no longer a finding - it needs the missing pull tab,
        # which is the objective half of that rule.
        wrapped = dict(WRAPPED_NO_TAB)
        one = evaluate(make_extraction(**wrapped))
        two = evaluate(make_extraction(**wrapped, copyright_year=2019))
        three = evaluate(
            make_extraction(**wrapped, copyright_year=2019, extracted_model_number="B1234")
        )
        assert one.confidence_score < two.confidence_score < three.confidence_score
        assert three.confidence_score <= 0.99

    def test_soft_signals_alone_flag_risk_with_lower_confidence(self):
        # Serial format is soft; keep the cross-match consistent so only the
        # format rule fires.
        soft = evaluate(make_extraction(
            extracted_serial_number="TOOSHORT", serial_case_lid="TOOSHORT"))
        hard = evaluate(make_extraction(has_plastic_shrinkwrap=True))
        assert soft.summary_verdict is Verdict.HIGH_RISK_SUSPECTED
        assert soft.confidence_score < hard.confidence_score

    def test_unreadable_tier_one_blocks_a_genuine_verdict(self):
        # Everything visible passes, but with no model number there is no
        # identity check, so the tool must not clear the item.
        report = evaluate(make_extraction(extracted_model_number=None))
        assert report.summary_verdict is Verdict.INCONCLUSIVE

    def test_thin_evidence_is_inconclusive(self):
        report = evaluate(
            make_extraction(
                extracted_model_number=None,
                extracted_serial_number=None,
                copyright_year=None,
                has_paper_pull_tabs=False,
            )
        )
        assert report.summary_verdict is Verdict.INCONCLUSIVE
        assert report.confidence_score < 0.6

    def test_a_failure_outranks_missing_evidence(self):
        # Partial evidence that contains a hard failure is a verdict, not a shrug.
        report = evaluate(
            make_extraction(
                extracted_model_number="A2700",
                extracted_serial_number=None,
                copyright_year=None,
                has_paper_pull_tabs=False,
            )
        )
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_evaluation_is_deterministic(self):
        payload = dict(GENUINE_USB_C)
        assert evaluate_dict(payload) == evaluate_dict(payload)


# --------------------------------------------------------------------------- #
# Output contract
# --------------------------------------------------------------------------- #


class TestOutputContract:
    def test_report_matches_the_documented_risk_output_schema(self):
        report = evaluate(make_extraction()).to_dict()
        assert set(report) == {
            "summary_verdict", "confidence_score", "evidence_ledger", "next_steps"
        }
        assert report["summary_verdict"] in {v.value for v in Verdict}
        assert 0.0 <= report["confidence_score"] <= 1.0
        for item in report["evidence_ledger"]:
            assert set(item) == {"tier", "parameter", "status", "detail", "source"}
            assert item["source"].strip(), "every rule must declare provenance"
            assert item["tier"] in (1, 2)
            assert item["status"] in {s.value for s in EvidenceStatus}
            assert item["detail"].strip()

    def test_every_rule_declares_a_source(self):
        # A rule with no provenance is an invented specification.
        for rule in RULES:
            assert rule.source.strip(), f"{rule.parameter} has no source"

    def test_no_rule_is_left_unsourced(self):
        # The serial-length constraint was the last one. It is now grounded in
        # observed lengths across genuine units, corpus listings and confirmed
        # counterfeits.
        unsourced = [r.parameter for r in RULES if r.source.startswith("UNSOURCED")]
        assert not unsourced, f"unsourced rules: {unsourced}"

    def test_a_rule_that_does_not_discriminate_cannot_disqualify(self):
        # Serial length is well-formedness, not a tell: both confirmed
        # counterfeits used 10 characters, exactly like genuine units.
        rule = next(r for r in RULES if r.parameter == "Serial Number Format")
        assert not rule.disqualifying
        assert "does not discriminate" in rule.source

    def test_informational_rules_carry_no_weight(self):
        for rule in RULES:
            if rule.informational:
                assert rule.weight == 0.0

    def test_every_rule_produces_exactly_one_ledger_line(self):
        report = evaluate(make_extraction())
        parameters = [item.parameter for item in report.evidence_ledger]
        assert parameters == [rule.parameter for rule in RULES]

    def test_ledger_is_json_serialisable(self):
        json.dumps(evaluate(make_extraction()).to_dict())


# --------------------------------------------------------------------------- #
# Sample data fixtures + CLI
# --------------------------------------------------------------------------- #


class TestSampleData:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("genuine_usbc.json", Verdict.GENUINE),
            ("genuine_lightning.json", Verdict.GENUINE),
            ("counterfeit_shrinkwrap.json", Verdict.HIGH_RISK_SUSPECTED),
            ("inconclusive_blurry.json", Verdict.INCONCLUSIVE),
        ],
    )
    def test_fixture_files_score_as_expected(self, filename, expected):
        payload = json.loads((SAMPLE_DATA / filename).read_text())
        report = evaluate(VisionExtraction.model_validate(payload))
        assert report.summary_verdict is expected

    @pytest.mark.parametrize(
        "filename, expected_exit",
        [
            ("genuine_usbc.json", 0),
            ("counterfeit_shrinkwrap.json", 1),
            ("inconclusive_blurry.json", 2),
        ],
    )
    def test_cli_exit_codes(self, filename, expected_exit, capsys):
        exit_code = cli.main(["--extraction", str(SAMPLE_DATA / filename), "--json"])
        assert exit_code == expected_exit
        json.loads(capsys.readouterr().out)  # stdout must stay machine-readable

    def test_cli_variant_override_changes_the_verdict(self, capsys):
        # The Lightning fixture re-scored as USB-C: same photos, different claim,
        # and the model number no longer matches.
        exit_code = cli.main(
            [
                "--extraction",
                str(SAMPLE_DATA / "genuine_lightning.json"),
                "--variant",
                VARIANT_USB_C,
                "--json",
            ]
        )
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        assert report["summary_verdict"] == Verdict.HIGH_RISK_SUSPECTED.value

    def test_cli_rejects_images_and_extraction_together(self):
        with pytest.raises(SystemExit):
            cli.main(["photo.jpg", "--extraction", str(SAMPLE_DATA / "genuine_usbc.json")])

    def test_cli_reports_a_missing_file_without_a_traceback(self, capsys):
        assert cli.main(["--extraction", "does_not_exist.json"]) == 3
        assert "error:" in capsys.readouterr().err


class TestCorpusRetry:
    """A dropped post silently biases the accuracy number, so transient
    failures must retry rather than being recorded as a result."""

    @pytest.mark.parametrize(
        "message",
        [
            "Gemini had a server error (500); retry shortly",
            "Gemini rate limit reached. Free tiers cap requests",
            "gemini-3.8-flash is currently experiencing high demand",
            "The model is overloaded, please try again",
            "Service temporarily unavailable",
            "Request timed out",
        ],
    )
    def test_transient_failures_are_retried(self, message):
        from tools.run_corpus import is_retryable

        assert is_retryable(message)

    @pytest.mark.parametrize(
        "message",
        [
            "No Gemini credentials found. Create a free key at aistudio.google.com",
            "Image not found: corpus/post_1/photo.jpg",
            "Gemini returned output that did not match the extraction schema",
            "Model 'gemini-9-flash' is not available on Gemini",
        ],
    )
    def test_permanent_failures_are_not_retried(self, message):
        from tools.run_corpus import is_retryable

        assert not is_retryable(message)


class TestAccentLossFilter:
    """OCR drops diacritics; the extractor then reports its own reading loss as
    a printing defect. Corpus post 3 was accused this way on a genuine box."""

    @pytest.mark.parametrize("word", ["Audifonos", "audifonos", "inalambricos", "tamanos"])
    def test_accent_only_differences_are_not_evidence(self, word):
        report = evaluate(make_extraction(visible_spelling_errors=[word]))
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    @pytest.mark.parametrize(
        "word", ["MagSate", "Appie", "Bums", "swalliwed", "inalimbricos", "Designd"]
    )
    def test_letter_substitutions_are_counted_as_real_errors(self, word):
        """Post 4's real counterfeit errors must survive the accent filter.

        Paired with a second error because one alone is now an observation -
        reflective print manufactures single substitutions. What is under test
        here is the accent filter, not the count, so the pairing keeps the
        question the same.
        """
        report = evaluate(make_extraction(visible_spelling_errors=[word, "cargaoores"]))
        detail = detail_of(report, "Typography & Print Quality")
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert word in detail

    @pytest.mark.parametrize("word", ["MagSate", "Appie", "Califomia"])
    def test_one_letter_substitution_alone_is_not_charged(self, word):
        """The reflective-print lesson: a lone substitution is a reading risk."""
        report = evaluate(make_extraction(visible_spelling_errors=[word]))
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN

    def test_real_errors_alongside_accent_loss_still_fail(self):
        report = evaluate(
            make_extraction(visible_spelling_errors=["Audifonos", "MagSate", "swalliwed"])
        )
        detail = detail_of(report, "Typography & Print Quality")
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert "MagSate" in detail
        # The accent-loss word must not be cited as evidence.
        assert "Audifonos" not in detail


class TestPanelAwareNextSteps:
    """Panel data turns an unhelpful UNKNOWN into a specific photograph to take."""

    def _report(self, panels, **overrides):
        return evaluate(
            make_extraction(
                visible_panels=panels,
                extracted_model_number=None,
                extracted_serial_number=None,
                copyright_year=None,
                has_paper_pull_tabs=False,
                **overrides,
            )
        )

    def test_missing_panels_produce_a_specific_request(self):
        steps = self._report(["case_exterior", "earbud"]).next_steps
        assert steps
        assert any("back of the box" in step for step in steps)

    def test_requests_name_the_checks_they_unblock(self):
        steps = self._report(["case_exterior"]).next_steps
        assert any("resolves:" in step for step in steps)

    def test_the_most_informative_panel_wins_a_tie(self):
        # Underside label and earbud both answer model number + serial; the
        # label carries them in one printed block, so it is asked for first.
        steps = self._report(["box_front", "box_back"]).next_steps
        assert "underside of the box" in steps[0]

    def test_a_photographed_but_unreadable_panel_asks_for_a_reshoot(self):
        steps = self._report(["box_bottom_label"]).next_steps
        assert any("Re-shoot" in step for step in steps)

    def test_unreadable_checks_are_named_in_one_instruction(self):
        # The advice is identical for every unreadable check, so repeating it
        # per rule buries the requests that actually differ.
        steps = self._report(["box_bottom_label"]).next_steps
        reshoots = [s for s in steps if "Re-shoot" in s]
        assert len(reshoots) == 1
        assert reshoots[0].count("could not") <= 1

    def test_no_open_checks_means_no_busywork(self):
        report = evaluate(
            make_extraction(visible_panels=["box_bottom_label", "box_back"]), GOOD_FONT
        )
        assert report.summary_verdict is Verdict.GENUINE
        assert report.next_steps == []

    def test_coverage_is_not_rescoped_to_the_panels_provided(self):
        # Photographing only the case must never reach a GENUINE verdict just
        # because the box checks became "not applicable".
        report = self._report(["case_exterior"])
        assert report.summary_verdict is Verdict.INCONCLUSIVE
        assert report.confidence_score < MIN_COVERAGE_FOR_VERDICT


class TestGenerationIdentification:
    """The corpus keeps surfacing non-Pro-2 products. Naming them is the only
    honest answer a Pro 2 verifier can give, and it is what stops an unknown
    A-number being mistaken for a fabricated one."""

    @pytest.mark.parametrize(
        "number, generation",
        [
            ("A3055", "AirPods 4 with Active Noise Cancellation"),  # corpus post 5
            ("A3053", "AirPods 4"),
            ("A2565", "AirPods 3"),
            ("A2032", "AirPods 2"),
            ("A1523", "AirPods 1"),
            ("A2096", "AirPods Max 1 (Lightning)"),
        ],
    )
    def test_the_product_is_named(self, number, generation):
        report = evaluate(make_extraction(extracted_model_number=number))
        assert generation in detail_of(report, "Model Number Compatibility")
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN

    def test_no_number_belongs_to_two_products(self):
        from rules_engine import MODEL_NUMBERS, OTHER_GENERATIONS

        assert not set(MODEL_NUMBERS) & set(OTHER_GENERATIONS)

    def test_every_generation_entry_is_sourced(self):
        from rules_engine import KNOWLEDGE

        for generation in KNOWLEDGE["other_generations"]:
            assert generation.get("source"), f"{generation['name']} has no source"


class TestSerialLabelSpacing:
    """The (S) Serial marker. Binary and printed, unlike the subjective
    judgements that produced false positives - and it caught a counterfeit the
    pipeline had passed as GENUINE."""

    def test_missing_space_fails(self):
        report = evaluate(make_extraction(serial_label_literal="(S)Serial No. PCWXHW027R"))
        assert status_of(report, "Serial Label Format") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_present_space_passes(self):
        report = evaluate(make_extraction(serial_label_literal="(S) Serial No. WXLP2LT40J"))
        assert status_of(report, "Serial Label Format") is EvidenceStatus.PASS

    def test_unreadable_label_is_unknown_not_a_failure(self):
        report = evaluate(make_extraction(serial_label_literal=None))
        assert status_of(report, "Serial Label Format") is EvidenceStatus.UNKNOWN

    def test_a_line_without_the_marker_is_unknown(self):
        report = evaluate(make_extraction(serial_label_literal="Serial WXLP2LT40J"))
        assert status_of(report, "Serial Label Format") is EvidenceStatus.UNKNOWN

    @pytest.mark.parametrize(
        "literal", ["(s)serial no. abc123", "( S )Serial No. ABC", "(S)SERIAL NO. ABC"]
    )
    def test_case_and_bracket_padding_do_not_defeat_it(self, literal):
        report = evaluate(make_extraction(serial_label_literal=literal))
        assert status_of(report, "Serial Label Format") is EvidenceStatus.FAIL

    def test_it_is_answerable_only_from_the_box_label(self):
        rule = next(r for r in RULES if r.parameter == "Serial Label Format")
        assert rule.answerable_from == ("box_bottom_label",)
        assert rule.source and not rule.source.startswith("UNSOURCED")


class TestIndicatorCatalogue:
    def test_catalogue_is_sourced_and_status_tracked(self):
        from rules_engine import KNOWLEDGE

        catalogue = KNOWLEDGE["owner_indicator_catalogue"]
        assert catalogue["source"]
        valid = {"implemented", "partially_implemented", "pending"}
        for indicator in catalogue["indicators"]:
            assert indicator["status"] in valid, indicator["id"]
            assert indicator["verdict_when_present"] in {"real", "fake"}


class TestImageQuality:
    def test_a_blurred_image_scores_lower_than_a_sharp_one(self, tmp_path):
        from PIL import Image, ImageDraw, ImageFilter

        from image_quality import measure_sharpness

        sharp = Image.new("RGB", (600, 400), "white")
        draw = ImageDraw.Draw(sharp)
        for y in range(20, 380, 24):
            draw.line([(20, y), (580, y)], fill="black", width=2)
        sharp_path = tmp_path / "sharp.png"
        sharp.save(sharp_path)

        blurred_path = tmp_path / "blurred.png"
        sharp.filter(ImageFilter.GaussianBlur(4)).save(blurred_path)

        assert measure_sharpness(sharp_path).score > measure_sharpness(blurred_path).score

    def test_sharpest_picks_the_best_image_in_a_set(self, tmp_path):
        from PIL import Image, ImageDraw, ImageFilter

        from image_quality import measure_sharpness, sharpest

        base = Image.new("RGB", (400, 300), "white")
        ImageDraw.Draw(base).rectangle([50, 50, 350, 250], outline="black", width=3)
        paths = []
        for name, image in [("a.png", base.filter(ImageFilter.GaussianBlur(5))), ("b.png", base)]:
            p = tmp_path / name
            image.save(p)
            paths.append(p)
        assert sharpest(paths).score == measure_sharpness(paths[1]).score

    def test_empty_set_returns_none(self):
        from image_quality import sharpest

        assert sharpest([]) is None


class TestLabelTypographyRule:
    """Wrong font is the most-cited tell in the owner's catalogue and the one an
    LLM cannot judge. The measurement happens locally in Python; this rule only
    decides what the number means."""

    def test_matching_letterforms_pass(self):
        report = evaluate(make_extraction(), GOOD_FONT)
        assert status_of(report, "Label Typography") is EvidenceStatus.PASS

    def test_mismatched_letterforms_are_reported_not_charged(self):
        # UNCALIBRATED. Four genuine labels span 0.69%-3.0% scatter at the
        # resolution phone photos produce, and the only substitute figures
        # available come from synthetic renders at a different scale. Until a
        # counterfeit label is measured at comparable resolution, a high reading
        # cannot separate a wrong typeface from a genuine label shot badly.
        report = evaluate(make_extraction(), WRONG_FONT)
        assert status_of(report, "Label Typography") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED
        assert "information only" in detail_of(report, "Label Typography")

    def test_print_below_the_resolution_floor_is_unknown_with_advice(self):
        # Below the gate the rule must not merely decline: it must say how far
        # short the photograph is and what would fix it, because that is the
        # only thing the buyer can act on.
        report = evaluate(make_extraction(), TOO_SMALL_FONT)
        detail = detail_of(report, "Label Typography")
        assert status_of(report, "Label Typography") is EvidenceStatus.UNKNOWN
        assert "7 pixels tall" in detail
        assert str(MEASURABLE_GLYPH_HEIGHT_PX) in detail
        assert "Fill the frame" in detail
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_below_the_gate_the_warning_names_the_inversion(self):
        # The reason for the gate is not that the measurement is weak. It is
        # that at 23px a substitute typeface scores BETTER than San Francisco,
        # so a reading taken there is worse than no reading. Anyone tempted to
        # lower the gate should meet that sentence first.
        detail = detail_of(evaluate(make_extraction(), TOO_SMALL_FONT), "Label Typography")
        assert "better than the real one" in detail

    def test_a_resolved_label_reports_shape_not_bounding_box(self):
        report = evaluate(make_extraction(), RESOLVED_FONT)
        detail = detail_of(report, "Label Typography")
        assert status_of(report, "Label Typography") is EvidenceStatus.UNKNOWN
        assert "0.041" in detail
        assert "96 pixels" in detail
        # Still not scored: no counterfeit has been photographed above the gate,
        # so there is no measured figure to compare against.
        assert "Reported, not scored" in detail
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_a_resolved_label_still_carries_no_weight(self):
        without = evaluate(make_extraction())
        resolved = evaluate(make_extraction(), RESOLVED_FONT)
        assert without.confidence_score == resolved.confidence_score

    def test_no_measurement_at_all_is_unknown(self):
        report = evaluate(make_extraction())
        assert status_of(report, "Label Typography") is EvidenceStatus.UNKNOWN

    def test_it_carries_no_weight_while_uncalibrated(self):
        # An observation-only rule must not move the verdict in either
        # direction, or an uncalibrated measurement is silently voting.
        without = evaluate(make_extraction())
        with_font = evaluate(make_extraction(), GOOD_FONT)
        wrong = evaluate(make_extraction(), WRONG_FONT)
        assert without.summary_verdict is with_font.summary_verdict is Verdict.GENUINE
        assert without.confidence_score == with_font.confidence_score == wrong.confidence_score

    def test_a_thinly_evidenced_item_is_still_not_cleared(self):
        # The safety trade this rule exists to make. On the labelled corpus it
        # turned a counterfeit that had passed as GENUINE @ 0.62 into
        # INCONCLUSIVE, because that item was already near the coverage floor.
        # A missed fake costs a buyer $200; a needless re-photograph costs
        # nothing.
        thin = make_extraction(
            extracted_serial_number=None, serial_label_literal=None,
            copyright_year=None, has_paper_pull_tabs=False,
        )
        assert evaluate(thin).summary_verdict is not Verdict.GENUINE

    def test_the_rule_only_answers_from_the_label_panel(self):
        rule = next(r for r in RULES if r.parameter == "Label Typography")
        assert rule.answerable_from == ("box_bottom_label",)
        assert not rule.source.startswith("UNSOURCED")

    def test_it_is_marked_uncalibrated_and_weightless(self):
        rule = next(r for r in RULES if r.parameter == "Label Typography")
        assert rule.informational and rule.weight == 0.0 and not rule.disqualifying
        assert "NOT CALIBRATED" in rule.source


class TestChargingCaseIdentification:
    """Apple lists charging cases separately from earbuds, with their own model
    numbers and years. An earlier version of the knowledge base merged them,
    which put a 2024 case inside an earbud entry and dated every AirPods Max to
    2020."""

    @pytest.mark.parametrize(
        "number, fragment, year",
        [
            ("A3059", "AirPods 4", 2024),
            ("A3058", "AirPods 4", 2024),
            ("A2897", "AirPods 3", 2022),
            ("A2566", "AirPods 3", 2021),
            ("A1938", "AirPods 1 and 2", 2019),
            ("A1602", "AirPods 1 and 2", 2016),
        ],
    )
    def test_cases_are_identified_with_their_own_year(self, number, fragment, year):
        from rules_engine import OTHER_GENERATIONS

        entry = OTHER_GENERATIONS[number]
        assert fragment in entry["name"]
        assert entry["introduced"] == year

    def test_airpods_max_generations_are_separate(self):
        from rules_engine import OTHER_GENERATIONS

        assert OTHER_GENERATIONS["A3454"]["introduced"] == 2026   # Max 2
        assert OTHER_GENERATIONS["A3184"]["introduced"] == 2024   # Max 1 USB-C
        assert OTHER_GENERATIONS["A2096"]["introduced"] == 2020   # Max 1 Lightning

    def test_a2190_ambiguity_is_preserved_not_silently_resolved(self):
        # One model number, two different cases, distinguished only by the last
        # four characters of the serial. Dropping either would make the ledger
        # state something Apple does not.
        from rules_engine import OTHER_GENERATIONS

        entry = OTHER_GENERATIONS["A2190"]
        assert entry.get("ambiguous")
        assert "or" in entry["name"]

    def test_a_case_number_is_named_not_accused(self):
        # A3059 is the AirPods 4 ANC case. A3122 used to sit here; it is a Pro 3
        # case and Pro 3 is a verified variant now, so it is graded rather than
        # declined.
        report = evaluate(make_extraction(extracted_model_number="A3059"))
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN
        assert "AirPods 4" in detail_of(report, "Model Number Compatibility")
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED


class TestCaseSerialSuffixes:
    """Apple states the last four characters of an AirPods Pro 1 case serial
    outright. Recorded now, usable when Pro 1 verification is added."""

    def test_suffixes_are_recorded_with_their_case(self):
        from rules_engine import KNOWLEDGE

        suffixes = KNOWLEDGE["case_serial_suffixes"]["A2190"]
        flat = {s for entry in suffixes for s in entry["suffixes"]}
        assert flat == {1059, "1NRC", "0C6L", "LKKT"}

    def test_the_owner_genuine_label_matches_a_documented_suffix(self):
        # The owner's genuine Pro 1 serial ends 1059: the MagSafe Charging Case
        # for AirPods Pro 1. Apple's own constraint, satisfied by a real unit.
        from rules_engine import KNOWLEDGE

        entry = next(
            e for e in KNOWLEDGE["case_serial_suffixes"]["A2190"] if 1059 in e["suffixes"]
        )
        assert entry["case"] == "MagSafe Charging Case for AirPods Pro 1"


class TestSerialCrossMatch:
    """The strongest check available that needs no reference data: it asks only
    whether one item's identifiers agree with each other. That makes it immune
    to the attack this project exists to detect - a serial cloned from a genuine
    unit, which passes Apple's own coverage lookup."""

    def test_a_consistent_set_passes(self):
        report = evaluate(make_extraction())
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.PASS

    def test_box_and_case_must_agree(self):
        report = evaluate(make_extraction(serial_case_lid="DIFFERENT99X"))
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL
        assert "not the case it was sold with" in detail_of(report, "Serial Cross-Match")
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_an_earbud_repeating_the_case_serial_fails(self):
        # Apple never repeats a serial across components.
        report = evaluate(make_extraction(serial_left_earbud="H4XQ7K2LMN9P"))
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_two_earbuds_sharing_a_serial_fails(self):
        report = evaluate(
            make_extraction(serial_left_earbud="AAAA1111BBBB", serial_right_earbud="AAAA1111BBBB")
        )
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL

    def test_one_serial_alone_cannot_be_cross_checked(self):
        report = evaluate(
            make_extraction(serial_case_lid=None, serial_left_earbud=None, serial_right_earbud=None)
        )
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.UNKNOWN
        assert "case lid" in detail_of(report, "Serial Cross-Match")

    def test_distinct_earbuds_without_the_box_pair_stays_unknown(self):
        # The pair that must MATCH is unverified, so this cannot pass yet.
        report = evaluate(
            make_extraction(extracted_serial_number=None, serial_case_lid=None)
        )
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.UNKNOWN
        assert "unverified" in detail_of(report, "Serial Cross-Match")

    def test_it_disqualifies_but_does_not_block(self):
        # Tier 2 on purpose. A Tier 1 rule left unresolved blocks GENUINE
        # outright, and on a sealed box the case-lid and earbud serials cannot
        # be photographed at all - so at Tier 1 this would make every sealed
        # unit permanently unverifiable. A FAIL still disqualifies.
        rule = next(r for r in RULES if r.parameter == "Serial Cross-Match")
        assert rule.tier == 2 and rule.disqualifying
        assert not rule.source.startswith("UNSOURCED")

    def test_a_sealed_box_can_still_be_cleared(self):
        # Only the box label serial is visible on a sealed unit.
        sealed = make_extraction(
            serial_case_lid=None, serial_left_earbud=None, serial_right_earbud=None,
            has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=True,
        )
        report = evaluate(sealed, GOOD_FONT)
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is Verdict.GENUINE

    def test_it_survives_a_cloned_serial(self):
        # A counterfeit carrying a serial cloned from a genuine unit passes
        # Apple's coverage lookup. It cannot pass this, because the cloner has
        # to reproduce four consistent serials, not one.
        cloned = make_extraction(
            serial_case_lid="H4XQ7K2LMN9P",
            serial_left_earbud="H4XQ7K2LMN9P",
            serial_right_earbud="H4XQ7K2LMN9P",
        )
        assert evaluate(cloned).summary_verdict is Verdict.HIGH_RISK_SUSPECTED


class TestEvidenceCollectedNotYetRuled:
    """Part numbers and origin wording are extracted and recorded, deliberately
    without a rule attached. Both look like tells and neither is supported yet."""

    def test_origin_wording_does_not_discriminate_on_current_evidence(self):
        from rules_engine import KNOWLEDGE

        evidence = KNOWLEDGE["origin_line_evidence"]
        # "Made in Vietnam" appears on a genuine AirPods Pro 1 AND on corpus
        # post 18, a confirmed counterfeit. It separates nothing.
        assert evidence["made_in_vietnam"]["genuine"] >= 1
        assert evidence["made_in_vietnam"]["counterfeit"] >= 1
        assert evidence["status"] == "insufficient_evidence"

    def test_product_of_vietnam_is_refuted_not_merely_unproven(self):
        """It sat at genuine 0 / counterfeit 1 and looked like the best tell here.

        The owner's own genuine AirPods Pro 3 reads "Product of Vietnam"
        verbatim, so it is now 1 and 1. This is not "not enough evidence yet" —
        it is evidence that the phrase separates nothing, and a rule built on it
        would have accused a genuine unit. Kept as a test so the promising-looking
        version cannot be reintroduced by someone reading only the counterfeit.
        """
        from rules_engine import KNOWLEDGE

        counts = KNOWLEDGE["origin_line_evidence"]["product_of_vietnam"]
        assert counts["genuine"] >= 1 and counts["counterfeit"] >= 1, (
            "Product of Vietnam appears on a genuine AirPods Pro 3 and on a confirmed "
            "counterfeit; it cannot be a tell in either direction"
        )

        observation = next(
            o for o in KNOWLEDGE["counterfeit_observations"]["observations"]
            if o["id"] == "product_of_vietnam"
        )
        assert observation["status"] == "refuted"

    def test_no_rule_reads_the_origin_line(self):
        # If this starts failing, a rule was added on evidence that does not
        # support it - four genuine labels already show two legitimate wordings,
        # so the full set Apple uses is unknown.
        import inspect

        import rules_engine

        assert "origin_line" not in inspect.getsource(rules_engine)

    def test_part_numbers_are_recorded_with_their_regions(self):
        from rules_engine import KNOWLEDGE

        genuine = KNOWLEDGE["observed_part_numbers"]["genuine"]
        assert {entry["region_suffix"] for entry in genuine} == {"AM/A", "LL/A"}
        assert all(entry.get("origin_line") for entry in genuine)

    def test_a_counterfeit_shares_a_region_suffix_with_genuine_units(self):
        # AM/A appears on genuine boxes AND on corpus post 18, so the suffix
        # alone proves nothing - a region rule needs the printed marks too.
        from rules_engine import KNOWLEDGE

        observed = KNOWLEDGE["observed_part_numbers"]
        genuine = {e["region_suffix"] for e in observed["genuine"]}
        fake = {e["region_suffix"] for e in observed["counterfeit"]}
        assert genuine & fake


class TestProvenanceIsVisible:
    """Provenance that only exists in the data is provenance the buyer cannot
    use. Being told an item is fake without being told where the claim comes
    from is not something anyone can act on or argue with."""

    def _render(self, extraction, **kwargs):
        return cli.render_report(evaluate(extraction), color=False, **kwargs)

    def test_a_failing_check_shows_its_source(self):
        text = self._render(make_extraction(extracted_model_number="A2700"))
        assert "source: Apple" in text

    def test_passing_checks_stay_quiet_by_default(self):
        # Every line carrying a citation would drown the findings.
        text = self._render(make_extraction())
        assert "source:" not in text

    def test_sources_flag_shows_them_all(self):
        text = self._render(make_extraction(), show_sources=True)
        assert text.count("source:") >= 5

    def test_json_output_always_carries_provenance(self):
        report = evaluate(make_extraction(extracted_model_number="A2700")).to_dict()
        assert all(item["source"].strip() for item in report["evidence_ledger"])

    def test_a_weak_rule_states_its_limits_where_a_buyer_sees_it(self):
        # Serial length catches a malformed identifier but does not distinguish
        # a counterfeit, and the ledger must say so rather than implying more.
        text = self._render(
            make_extraction(extracted_serial_number="TOOSHORT", serial_case_lid="TOOSHORT"),
        )
        assert "does not discriminate" in text


class TestVerdictScenarioMatrix:
    """End-to-end behaviour across the situations a buyer actually hits. The
    aggregation grew to nine rules through many small changes; this pins what
    each combination should produce so a weight change cannot quietly shift a
    verdict."""

    def _verdict(self, **overrides):
        report = evaluate(make_extraction(**overrides), GOOD_FONT)
        return report.summary_verdict, report.confidence_score

    def test_a_clean_unit_clears_at_the_cap(self):
        verdict, score = self._verdict()
        assert verdict is Verdict.GENUINE
        assert score == 0.95

    def test_a_sealed_box_clears_at_lower_confidence(self):
        # Case lid and earbud serials are physically unreachable while sealed,
        # so it clears - but visibly less certainly than an opened one.
        verdict, score = self._verdict(
            serial_case_lid=None, serial_left_earbud=None, serial_right_earbud=None,
            has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=True,
        )
        assert verdict is Verdict.GENUINE
        assert 0.6 < score < 0.95

    def test_hardware_only_photos_cannot_clear_anything(self):
        verdict, score = self._verdict(
            extracted_model_number=None, extracted_serial_number=None,
            serial_label_literal=None, copyright_year=None, has_paper_pull_tabs=False,
            visible_panels=["case_exterior", "earbud"],
        )
        assert verdict is Verdict.INCONCLUSIVE
        assert score < MIN_COVERAGE_FOR_VERDICT

    @pytest.mark.parametrize(
        "name, overrides",
        [
            ("cross-variant model", dict(extracted_model_number="A2700")),
            ("repeated serial", dict(serial_left_earbud="H4XQ7K2LMN9P")),
            ("(S)Serial no space", dict(serial_label_literal="(S)Serial No. H4XQ7K2LMN9P")),
            ("wrap without pull tab", dict(WRAPPED_NO_TAB)),
        ],
    )
    def test_each_disqualifier_condemns_alone(self, name, overrides):
        verdict, score = self._verdict(**overrides)
        assert verdict is Verdict.HIGH_RISK_SUSPECTED, name
        assert score >= 0.85, name

    def test_a_soft_signal_flags_at_lower_confidence(self):
        # Two misspellings: one alone is a reading artefact on reflective print.
        verdict, score = self._verdict(visible_spelling_errors=["MagSate", "swalliwed"])
        assert verdict is Verdict.HIGH_RISK_SUSPECTED
        assert score < 0.85

    def test_an_out_of_scope_product_is_never_accused(self):
        # AirPods 4, not Pro 3: Pro 3 now has a sourced seal and copyright.
        verdict, _ = self._verdict(extracted_model_number="A3053")
        assert verdict is Verdict.INCONCLUSIVE


class TestInconclusiveIsNotConfidence:
    def test_an_inconclusive_verdict_reports_coverage_not_confidence(self):
        # 0.74 next to "Confidence" reads as "probably fine", which is the
        # opposite of what an inconclusive result means.
        report = evaluate(make_extraction(extracted_model_number="A3053"), GOOD_FONT)
        text = cli.render_report(report, color=False)
        assert report.summary_verdict is Verdict.INCONCLUSIVE
        assert "Evidence coverage:" in text
        assert "Confidence:" not in text

    def test_a_decided_verdict_still_reports_confidence(self):
        text = cli.render_report(evaluate(make_extraction(), GOOD_FONT), color=False)
        assert "Confidence:" in text


class TestRulesNeedingSeveralPanels:
    """A cross-check between two surfaces is not answerable from either alone.
    Without that distinction the tool told a buyer who had photographed the box
    thoroughly to "re-shoot in even light", when the real problem was that the
    case had never been opened."""

    def test_it_asks_for_the_case_lid_not_a_reshoot(self):
        report = evaluate(make_extraction(
            serial_case_lid=None, serial_left_earbud=None, serial_right_earbud=None,
            visible_panels=["box_front", "box_back", "box_bottom_label"],
        ))
        steps = " ".join(report.next_steps)
        assert "underside of the lid" in steps
        assert "re-shoot" not in steps

    def test_the_cross_match_rule_declares_both_surfaces(self):
        rule = next(r for r in RULES if r.parameter == "Serial Cross-Match")
        assert set(rule.requires_panels) == {"box_bottom_label", "case_lid_underside"}

    def test_a_single_panel_rule_is_unaffected(self):
        # Serial Label Format needs only the box label, and should still be
        # treated as unreadable rather than unphotographed when it is present.
        report = evaluate(make_extraction(
            serial_label_literal=None, visible_panels=["box_bottom_label"],
        ))
        steps = " ".join(report.next_steps)
        assert "Serial Label Format" in steps and "Re-shoot" in steps

    def test_having_every_required_panel_removes_the_request(self):
        report = evaluate(make_extraction(
            visible_panels=["box_bottom_label", "case_lid_underside", "box_back"],
        ))
        assert not any("underside of the lid" in step for step in report.next_steps)


class TestNextStepRanking:
    """Photo requests are ranked by the evidence weight they unlock, not by how
    many checks they happen to touch."""

    def _steps(self, panels):
        return evaluate(make_extraction(
            extracted_model_number=None, extracted_serial_number=None,
            serial_label_literal=None, copyright_year=None, serial_case_lid=None,
            serial_left_earbud=None, serial_right_earbud=None,
            has_paper_pull_tabs=False, visible_panels=panels,
        )).next_steps

    def test_the_case_lid_is_requested_when_nothing_was_photographed(self):
        # It unblocks only one rule, so counting checks buried it - but that
        # rule is the serial cross-match, the only check a cloned serial cannot
        # survive.
        steps = self._steps([])
        assert any("underside of the lid" in step for step in steps)

    def test_the_box_label_still_comes_first(self):
        # Highest total weight: model number, copyright, cross-match, label
        # format and serial format all depend on it.
        assert "underside of the box" in self._steps([])[0]

    def test_the_case_lid_leads_once_the_box_is_covered(self):
        assert "underside of the lid" in self._steps(
            ["box_front", "box_back", "box_bottom_label"])[0]

    def test_it_terminates_and_does_not_repeat_a_request(self):
        # A rule needing several panels stays queued until all are covered, so
        # the loop must exclude panels already asked for or it never ends.
        steps = self._steps([])
        requests = [s for s in steps if "resolves:" in s]
        assert len(requests) == len(set(requests))
        assert len(steps) < 12

    def test_a_fully_photographed_item_asks_for_nothing(self):
        report = evaluate(make_extraction(
            visible_panels=["box_bottom_label", "case_lid_underside", "box_back", "earbud"],
        ), GOOD_FONT)
        assert report.next_steps == []


class TestProductScopedRules:
    """Packaging ground truth here is AirPods Pro 2 specific. The pull-tab seal
    comes from a Pro 2 guide and the copyright range from Pro 2 release dates,
    so applying either to another generation is an unsourced extrapolation - and
    it produced a false accusation on a genuine AirPods 4 ANC whose packaging
    simply differs."""

    OTHER_PRODUCT = "A3055"   # AirPods 4 with Active Noise Cancellation

    def test_seal_rule_declines_on_another_generation(self):
        report = evaluate(make_extraction(
            extracted_model_number=self.OTHER_PRODUCT,
            has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=False,
        ))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN
        assert "AirPods 4" in detail_of(report, "Packaging Seal Type")
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_copyright_rule_declines_on_another_generation(self):
        report = evaluate(make_extraction(
            extracted_model_number=self.OTHER_PRODUCT, copyright_year=2019,
        ))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.UNKNOWN

    def test_universal_rules_still_apply_to_other_products(self):
        # Cross-matched serials and printed misspellings are not Pro 2 specific:
        # Apple never repeats a serial, and proofreads every region's packaging.
        report = evaluate(make_extraction(
            extracted_model_number=self.OTHER_PRODUCT,
            serial_left_earbud="H4XQ7K2LMN9P",
            visible_spelling_errors=["MagSate", "swalliwed"],
        ))
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL

    def test_a_pro_2_item_is_unaffected(self):
        report = evaluate(make_extraction(**WRAPPED_NO_TAB))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL

    def test_an_unrecognised_number_does_not_disable_the_rules(self):
        # Only a number that IDENTIFIES another product scopes them out. A
        # malformed one is still a Pro 2 claim and must be judged as one.
        report = evaluate(make_extraction(**WRAPPED_NO_TAB, extracted_model_number="B1234"))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL


class TestTheTabMustBeVisibleToBeMissing:
    """A tear-strip runs along a side edge. A photograph of the front face
    cannot establish that it is absent, and a model that says so is reporting
    the limit of its viewpoint rather than a fact about the box.

    Corpus post 13 was called HIGH RISK on exactly that - three photographs,
    none of them an edge - where the domain expert said inconclusive. It is the
    only item in the corpus where this pipeline out-claimed the expert on a
    disqualifying rule.
    """

    def _wrapped(self, panels, tab=False):
        return make_extraction(
            has_plastic_shrinkwrap=True,
            has_paper_pull_tabs=False,
            shrinkwrap_has_pull_tab=tab,
            visible_panels=panels,
        )

    def test_no_edge_photographed_is_unknown_not_a_finding(self):
        report = evaluate(self._wrapped(["box_front", "case_exterior"]))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN
        detail = detail_of(report, "Packaging Seal Type")
        assert "side edge" in detail
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_it_says_which_photograph_would_settle_it(self):
        detail = detail_of(
            evaluate(self._wrapped(["box_front"])), "Packaging Seal Type"
        )
        assert "Photograph the side of the box" in detail

    def test_a_side_view_still_convicts(self):
        report = evaluate(self._wrapped(["box_front", "box_side"]))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL

    def test_a_top_view_alone_does_not_convict(self):
        """box_top is not trusted as an edge view, and the reason is measured.

        Two extractor failures that have both actually happened combine into a
        false accusation: shrink-wrap reported on an already-opened box (post
        6), and the interior tray of an opened box labelled "box_top" (post
        10). Between them they manufacture an edge view that does not exist.
        box_side cannot be confused with an interior tray, and requiring it
        costs no corpus verdict - every item failed for a missing tab carries
        box_side anyway.
        """
        report = evaluate(self._wrapped(["box_top"]))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN

    def test_a_visible_tab_still_passes_without_an_edge_view(self):
        """Seeing a tab is positive evidence; the guard is only about absence."""
        report = evaluate(self._wrapped(["box_front"], tab=True))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.PASS


class TestCleanCopyNobodyPhotographedIsNotAPass:
    """The dangerous half of the same bug the pull-tab rule had.

    "No misspelled text was found in the printed packaging copy" was returned
    whenever the extractor reported no misspellings — including when no surface
    carrying printed copy had been photographed at all. The case, the lid and
    the earbuds have no prose to misspell, so a clean reading of them says
    nothing, and PASS added 1.5 of evidence weight toward a verdict on the
    strength of absent evidence.

    Measured on the corpus: 3 of 20 items collected that free pass, including
    one the expert labelled HIGH RISK.
    """

    HARDWARE_ONLY = ["case_exterior", "case_lid_underside", "earbud"]

    def test_a_clean_reading_of_hardware_only_is_unknown(self):
        report = evaluate(
            make_extraction(visible_panels=self.HARDWARE_ONLY, visible_spelling_errors=[])
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN

    def test_it_names_the_photograph_that_would_make_it_real(self):
        detail = detail_of(
            evaluate(make_extraction(visible_panels=self.HARDWARE_ONLY)),
            "Typography & Print Quality",
        )
        assert "box back" in detail and "underside label" in detail

    def test_that_pass_no_longer_buys_coverage(self):
        """The point of the fix: absent evidence must not move the score.

        Compared against the same extraction with a printed surface added, so
        the difference measured is the rule's own contribution and nothing
        else. (These fixtures deliberately carry a full set of extracted
        values, so most rules resolve whatever the panel list says — which is
        exactly why this asserts on the delta rather than on the verdict.)
        """
        hardware = evaluate(make_extraction(visible_panels=self.HARDWARE_ONLY))
        with_copy = evaluate(make_extraction(visible_panels=[*self.HARDWARE_ONLY, "box_back"]))
        assert status_of(hardware, "Typography & Print Quality") is EvidenceStatus.UNKNOWN
        assert status_of(with_copy, "Typography & Print Quality") is EvidenceStatus.PASS
        assert hardware.confidence_score < with_copy.confidence_score, (
            "a clean reading of copy nobody photographed still buys coverage"
        )

    def test_printed_copy_photographed_still_passes(self):
        report = evaluate(
            make_extraction(visible_panels=["box_back"], visible_spelling_errors=[])
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.PASS

    def test_a_misspelling_convicts_from_any_panel_set(self):
        """A misspelling that was actually read is positive evidence, and the
        guard is only ever about absence."""
        report = evaluate(
            make_extraction(
                visible_panels=self.HARDWARE_ONLY,
                visible_spelling_errors=["Apnie", "swalliwed"],
            )
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL


class TestOcrFragmentsAreNotMisspellings:
    """Corpus post 15 reported 'b,' beside 'Apnie'. One is a printing defect and
    the other is a reading artefact, and a fragment must never convict alone."""

    def test_a_fragment_alone_does_not_convict(self):
        report = evaluate(
            make_extraction(visible_panels=["box_back"], visible_spelling_errors=["b,", ".", "x"])
        )
        assert status_of(report, "Typography & Print Quality") is not EvidenceStatus.FAIL

    def test_real_misspellings_beside_a_fragment_still_convict(self):
        report = evaluate(
            make_extraction(
                visible_panels=["box_back"],
                visible_spelling_errors=["b,", "Apnie", "swalliwed"],
            )
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        detail = detail_of(report, "Typography & Print Quality")
        assert "Apnie" in detail and "'b,'" not in detail

    def test_the_discarded_fragments_are_still_declared(self):
        """Dropped input is disclosed, never silently swallowed."""
        detail = detail_of(
            evaluate(
                make_extraction(visible_panels=["box_back"], visible_spelling_errors=["b,", "Apnie"])
            ),
            "Typography & Print Quality",
        )
        assert "too short to be a word" in detail

    def test_a_short_but_real_word_is_not_a_fragment(self):
        report = evaluate(
            make_extraction(
                visible_panels=["box_back"], visible_spelling_errors=["Aple", "swalliwed"]
            )
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert "Aple" in detail_of(report, "Typography & Print Quality")


class TestReflectivePrintMustNotConvict:
    """Newer AirPods packaging throws highlights, and a washed-out stroke turns
    "Apple" into "Appie" - one l-to-i substitution.

    Corpus post 21, a genuine retail box with the price sticker still on it, was
    called HIGH RISK on exactly that. The pattern cannot save us: a real
    counterfeit in the corpus reads "Califomia" (rn-to-m), the same shape of
    single substitution. Sharpness cannot either - the reflective genuine box
    scores 220 against 182 and 492 for two fakes. Only the count separates them:
    across the labelled corpus no genuine item reported any misspelling, and the
    counterfeits that reported them reported 4, 2, 1 and 1.
    """

    def test_one_misspelling_is_reported_not_charged(self):
        report = evaluate(
            make_extraction(visible_panels=["box_back"], visible_spelling_errors=["Appie"])
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_it_says_why_and_what_would_settle_it(self):
        detail = detail_of(
            evaluate(make_extraction(visible_panels=["box_back"], visible_spelling_errors=["Appie"])),
            "Typography & Print Quality",
        )
        assert "Appie" in detail
        assert "reflective" in detail.lower()
        assert "without glare" in detail

    def test_two_misspellings_still_convict(self):
        report = evaluate(
            make_extraction(
                visible_panels=["box_back"],
                visible_spelling_errors=["Appie", "cargaoores"],
            )
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL

    def test_a_fragment_does_not_count_toward_the_second(self):
        """'b,' beside one real misspelling is still one misspelling."""
        report = evaluate(
            make_extraction(visible_panels=["box_back"], visible_spelling_errors=["b,", "Appie"])
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.UNKNOWN


class TestSerialGlyphConfusion:
    """A genuine label reading L0YT6B4RKN came back as LOYT6B4RKN off a
    reflective panel - letter O for digit zero. Folding those together for
    COMPARISON keeps a highlight from manufacturing a mismatch; the buyer is
    still shown exactly what was read."""

    def test_a_zero_read_as_an_o_does_not_break_the_match(self):
        report = evaluate(
            make_extraction(
                extracted_serial_number="L0YT6B4RKN",
                serial_case_lid="LOYT6B4RKN",
                serial_label_literal="(S) Serial No. L0YT6B4RKN",
                visible_panels=["box_bottom_label", "case_lid_underside"],
            )
        )
        assert status_of(report, "Serial Cross-Match") is not EvidenceStatus.FAIL

    def test_a_genuinely_different_serial_still_fails(self):
        report = evaluate(
            make_extraction(
                extracted_serial_number="H4XQ7K2LMN",
                serial_case_lid="P9WT3B8RTY",
                serial_label_literal="(S) Serial No. H4XQ7K2LMN",
                visible_panels=["box_bottom_label", "case_lid_underside"],
            )
        )
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL

    def test_one_real_character_apart_still_fails(self):
        """Folding must not swallow a difference a reader would never make."""
        from rules_engine import _same_serial

        assert not _same_serial("MF4TFMQ2VQ", "MF4TFMQ2VR")
        assert not _same_serial("L0YT6B4RKN", "L0YT6B4RK")
        assert _same_serial("L0YT6B4RKN", "LOYT6B4RKN")

    def test_the_buyer_is_shown_what_was_actually_read(self):
        """Folding is for comparison only; it must never rewrite the evidence."""
        detail = detail_of(
            evaluate(
                make_extraction(
                    extracted_serial_number="L0YT6B4RKN",
                    serial_case_lid="P9WT3B8RTY",
                    serial_label_literal="(S) Serial No. L0YT6B4RKN",
                    visible_panels=["box_bottom_label", "case_lid_underside"],
                )
            ),
            "Serial Cross-Match",
        )
        assert "L0YT6B4RKN" in detail and "P9WT3B8RTY" in detail


class TestOutOfScopeSaysWhatItCanStillDo:
    """An out-of-scope product cannot be cleared, but can still be condemned.

    Was written about Pro 3, which is a verified variant now - its seal and
    copyright were sourced and its numbers are in the matrix, so it is graded
    like any Pro 2. AirPods 4 is the example that still fits.

    The Pro-2-sourced checks - seal type, copyright range, model compatibility -
    are withheld. The serial checks are not: Apple never repeats a serial and
    never ships a box whose label disagrees with the case inside it, whatever
    the generation. The message used to say "no authenticity finding can be
    made about this item", which overstated it, and a buyer who read that and
    stopped reading would miss a finding sitting three rows below.
    """

    OUT_OF_SCOPE = "A3053"  # AirPods 4

    def test_it_does_not_claim_nothing_can_be_found(self):
        detail = detail_of(
            evaluate(make_extraction(extracted_model_number=self.OUT_OF_SCOPE)),
            "Model Number Compatibility",
        )
        assert "cannot be cleared" in detail
        assert "can still catch a fake" in detail

    def test_the_serial_label_tell_still_convicts_out_of_scope(self):
        """Generation-independent: Apple never prints "(S)Serial" unspaced."""
        report = evaluate(
            make_extraction(
                extracted_model_number=self.OUT_OF_SCOPE,
                extracted_serial_number="HGHP9X4047",
                serial_label_literal="(S)Serial No. HGHP9X4047",
            )
        )
        assert status_of(report, "Serial Label Format") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_a_serial_mismatch_still_convicts_out_of_scope(self):
        """Apple never ships a box whose label disagrees with the case inside."""
        report = evaluate(
            make_extraction(
                extracted_model_number=self.OUT_OF_SCOPE,
                extracted_serial_number="H4XQ7K2LMN",
                serial_case_lid="P9WT3B8RTY",
                serial_label_literal="(S) Serial No. H4XQ7K2LMN",
                visible_panels=["box_bottom_label", "case_lid_underside"],
            )
        )
        assert status_of(report, "Serial Cross-Match") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_a_clean_out_of_scope_item_is_still_never_cleared(self):
        """The asymmetry is the point: condemn yes, clear no.

        Pro 3 used to be the example here and is now a verified variant, so it
        IS cleared. The asymmetry still holds for anything this tool has no
        packaging ground truth for.
        """
        report = evaluate(make_extraction(extracted_model_number=self.OUT_OF_SCOPE))
        assert report.summary_verdict is not Verdict.GENUINE


class TestNoGenuineSerialIsWrittenDown:
    """A real serial identifies someone's property. None belongs in here.

    This is not hypothetical: a genuine unit's serial was transcribed into the
    knowledge base, the rules and this test file while documenting a real
    misreading, and rode three commits before anyone noticed. The characters
    were never load-bearing - what the observation needed was a serial with a
    zero in it - so they are a stand-in now, and this stops that one coming
    back under any edit.
    """

    #: Serials known to belong to a real unit. Fragments only, never the whole
    #: string, so this guard does not reintroduce what it exists to remove.
    KNOWN_REAL_FRAGMENTS = ("MG74KXN", "QMG74K")

    #: Anything that reads a repository file is in scope. Photographs are
    #: covered separately - .gitignore keeps them out entirely.
    SEARCHED_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".json", ".ts", ".mjs", ".astro", ".txt"}

    def test_no_known_real_serial_appears_anywhere(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in self.SEARCHED_SUFFIXES:
                continue
            if any(part in {".git", "node_modules", ".venv", "dist"} for part in path.parts):
                continue
            text = path.read_text(errors="ignore")
            for fragment in self.KNOWN_REAL_FRAGMENTS:
                if fragment in text and path.name != Path(__file__).name:
                    offenders.append(f"{path.relative_to(root)} contains {fragment}")
        assert not offenders, "a real serial is back in the repository: " + "; ".join(offenders)


class TestCopyrightForOtherGenerations:
    """Pro 3 has a sourced copyright range, so it is checked against its own.

    Floor 2025 from Apple's introduction year. Ceiling 2025 from the owner,
    asked directly: 2025 is the only year on a genuine Pro 3 box. Not
    extrapolated from Pro 2 dates, which is the move that once accused a
    genuine AirPods 4 ANC.

    Condemning is allowed. Clearing is not: Model Number Compatibility is
    Tier 1 and stays UNKNOWN for a non-Pro-2 product, so INCONCLUSIVE holds
    whatever the coverage reaches.
    """

    PRO_3 = "A3063"
    AIRPODS_4 = "A3053"  # no sourced facts; the invariant still applies to it

    def test_a_year_predating_the_product_fails(self):
        report = evaluate(make_extraction(claimed_model_variant="Pro 3", extracted_model_number=self.PRO_3, copyright_year=2024))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_the_only_genuine_year_passes(self):
        report = evaluate(make_extraction(extracted_model_number=self.PRO_3, copyright_year=2025))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.PASS

    def test_a_later_year_fails_on_the_owners_statement(self):
        report = evaluate(make_extraction(extracted_model_number=self.PRO_3, copyright_year=2026))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_a_clean_pro_3_reaches_a_verdict(self):
        """Was "still cannot be cleared". Promoting Pro 3 inverted it."""
        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 3",
                extracted_model_number=self.PRO_3,
                copyright_year=2025,
                has_paper_pull_tabs=True,
                has_plastic_shrinkwrap=False,
            )
        )
        assert report.summary_verdict is Verdict.GENUINE
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.PASS

    def test_a_product_with_no_sourced_range_is_still_withheld(self):
        report = evaluate(make_extraction(extracted_model_number=self.AIRPODS_4, copyright_year=2024))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.UNKNOWN

    def test_the_range_comes_from_the_knowledge_base(self):
        from rules_engine import _OTHER_GENERATION_COPYRIGHT

        bounds = _OTHER_GENERATION_COPYRIGHT["AirPods Pro 3"]
        assert bounds["min"] == 2025 and bounds["max"] == 2025
        assert "owner" in bounds["source"]


class TestTheCopyrightCeilingCannotSilentlyAge:
    """A frozen ceiling accused genuine boxes once. It must not do it twice.

    The ceiling convicts again, on the owner's categorical statement that no
    genuine AirPods Pro 2 box carries a 2026 copyright. That is an absence
    claim; the objection was raised and the owner reaffirmed it, so it stands
    on the source hierarchy — the same footing as the (S) Serial spacing rule.

    What went wrong before was not the claim but the calendar. The ceiling sat
    at 2025 while the date was 2026-09-08, so every box reprinted that year was
    called HIGH RISK, and the tests asserted the stale number instead of the
    property. These fail when the observation goes stale, forcing a re-check
    rather than letting it rot into false accusations.
    """

    MAX_OBSERVATION_AGE_DAYS = 400  # a year, plus room to notice

    def test_the_ceiling_observation_is_dated(self):
        from rules_engine import KNOWLEDGE

        assert KNOWLEDGE.get("observed_on"), (
            "copyright ceilings convict; without a date nobody can tell when they went stale"
        )

    def test_the_observation_is_not_stale(self):
        import datetime

        from rules_engine import KNOWLEDGE

        observed = KNOWLEDGE["observed_on"]
        if isinstance(observed, str):
            observed = datetime.date.fromisoformat(observed)
        age = (datetime.date.today() - observed).days
        assert age <= self.MAX_OBSERVATION_AGE_DAYS, (
            f"the copyright ceiling was last confirmed {age} days ago and it CONVICTS. "
            f"Ask the owner whether a genuine box has appeared carrying a later year, "
            f"then update observed_on. Until then this rule may be accusing genuine units."
        )

    def test_a_year_above_the_ceiling_convicts(self):
        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 2 USB-C",
                extracted_model_number="A3047",
                copyright_year=MAXIMUM_COPYRIGHT_YEAR + 1,
            )
        )
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_the_finding_warns_that_a_misread_digit_looks_identical(self):
        """OCR turning 2025 into 2026 produces this finding and no other signal."""
        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 2 USB-C",
                extracted_model_number="A3047",
                copyright_year=MAXIMUM_COPYRIGHT_YEAR + 1,
            )
        )
        assert "misread" in detail_of(report, "Copyright Year Constraint")


class TestSealForProductsWithASourcedSeal:
    """Pro 3 can be condemned on its seal. It still cannot be cleared.

    Owner, asked directly 2026-09-08: a sealed genuine AirPods Pro 3 box has
    paper pull-tabs with green arrows, the same as Pro 2 USB-C. Apple publishes
    nothing about packaging seals, so the owner is the highest source available.

    Clearing stays impossible because Model Number Compatibility is Tier 1 and
    stays UNKNOWN for a non-Pro-2 product, and an unresolved Tier 1 forces
    INCONCLUSIVE whatever the coverage reaches. That is the designed position
    for a product this tool does not fully characterise, and it is why enabling
    the seal here did not break the three "never accused" invariants.
    """

    PRO_3 = "A3063"
    AIRPODS_4 = "A3053"  # no sourced seal; must stay withheld

    def test_the_list_comes_from_the_knowledge_base(self):
        from rules_engine import KNOWLEDGE, _SEAL_SOURCED_FOR

        assert set(_SEAL_SOURCED_FOR) == set(KNOWLEDGE["packaging"]["seal_sourced_for"])
        assert "AirPods Pro 3" in _SEAL_SOURCED_FOR

    def test_a_pro_3_with_paper_tabs_resolves_the_seal(self):
        report = evaluate(
            make_extraction(
                extracted_model_number=self.PRO_3,
                has_paper_pull_tabs=True,
                has_plastic_shrinkwrap=False,
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.PASS

    def test_a_pro_3_can_be_condemned_on_its_seal(self):
        report = evaluate(
            make_extraction(
                extracted_model_number=self.PRO_3,
                has_plastic_shrinkwrap=True,
                shrinkwrap_has_pull_tab=False,
                has_paper_pull_tabs=False,
                visible_panels=["box_side", "box_bottom_label"],
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_a_pro_3_still_cannot_be_cleared(self):
        """The invariant. Tier 1 is unresolved, so no coverage clears it."""
        report = evaluate(
            make_extraction(
                claimed_model_variant="Pro 3",
                extracted_model_number=self.PRO_3,
                copyright_year=2025,
                has_paper_pull_tabs=True,
                has_plastic_shrinkwrap=False,
            )
        )
        assert report.summary_verdict is Verdict.GENUINE
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.PASS

    def test_a_product_with_no_sourced_seal_is_still_withheld(self):
        report = evaluate(
            make_extraction(
                extracted_model_number=self.AIRPODS_4,
                has_paper_pull_tabs=True,
                has_plastic_shrinkwrap=False,
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.UNKNOWN


class TestProThreeIsAVerifiedVariant:
    """Pro 3 is graded, not declined, since 2026-09-09.

    It was promoted out of other_generations once every voting rule was either
    generation-independent or sourced FOR it: model numbers and the case from
    Apple, seal type and copyright ceiling from the owner asked directly. The
    serial checks and the misspelling check never depended on generation.

    The promotion is the product decision the three "never accused" invariants
    were protecting against being made by accident. It was made on answers to
    two direct questions, and those invariants still hold for AirPods 4.
    """

    PRO_3 = "A3063"

    def _pro3(self, **kw):
        base = dict(
            claimed_model_variant="Pro 3",
            extracted_model_number=self.PRO_3,
            copyright_year=2025,
            has_paper_pull_tabs=True,
            has_plastic_shrinkwrap=False,
        )
        base.update(kw)
        return make_extraction(**base)

    def test_a_clean_pro_3_is_cleared(self):
        report = evaluate(self._pro3())
        assert report.summary_verdict is Verdict.GENUINE

    def test_the_model_number_resolves_instead_of_declining(self):
        assert status_of(evaluate(self._pro3()), "Model Number Compatibility") is EvidenceStatus.PASS

    def test_the_variant_resolves_from_the_number_without_a_claim(self):
        """The schema gained "Pro 3", but a box that does not state it still works."""
        from rules_engine import resolve_variant

        assert resolve_variant(self._pro3(claimed_model_variant="Unknown")) == "Pro 3"

    def test_a_copyright_before_the_product_existed_convicts(self):
        report = evaluate(self._pro3(copyright_year=2024))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL

    def test_a_missing_pull_tab_convicts_on_an_edge_view(self):
        report = evaluate(
            self._pro3(
                has_paper_pull_tabs=False,
                has_plastic_shrinkwrap=True,
                shrinkwrap_has_pull_tab=False,
                visible_panels=["box_side", "box_bottom_label"],
            )
        )
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL

    def test_pro_3_numbers_are_not_in_two_tables(self):
        """A3122 sat in model_numbers AND charging_cases when Pro 3 moved."""
        from rules_engine import MODEL_NUMBERS, OTHER_GENERATIONS

        assert not set(MODEL_NUMBERS) & set(OTHER_GENERATIONS)

    def test_pro_3_has_exactly_one_variant(self):
        """Apple ships Pro 3 with USB-C only, so there is no connector split."""
        from rules_engine import KNOWLEDGE

        pro3 = [v for v in KNOWLEDGE["variants"] if v["id"] == "Pro 3"]
        assert len(pro3) == 1 and pro3[0]["introduced"] == 2025
