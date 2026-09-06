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
    "copyright_year": 2023,
    "has_plastic_shrinkwrap": False,
    "has_paper_pull_tabs": True,
    "shrinkwrap_has_pull_tab": None,
    "shrinkwrap_looks_shoddy": None,
    "visible_panels": ["box_bottom_label"],
    "font_kerning_irregularities": False,
    "visible_spelling_errors": [],
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
            ("A3063", "AirPods Pro 3"),
            ("A3064", "AirPods Pro 3"),
            ("A3065", "AirPods Pro 3"),
            ("A3122", "AirPods Pro 3"),
            ("A2084", "AirPods Pro (1st generation)"),
            ("A2083", "AirPods Pro (1st generation)"),
        ],
    )
    def test_other_generations_are_named_not_accused(self, number, generation):
        # A3063 is a real Pro 3 number. It appeared in the corpus on one genuine
        # unit and one counterfeit; naming the product is the only honest answer.
        report = evaluate(make_extraction(extracted_model_number=number))
        detail = detail_of(report, "Model Number Compatibility")
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.UNKNOWN
        assert generation in detail
        assert report.summary_verdict is not Verdict.HIGH_RISK_SUSPECTED

    def test_out_of_scope_items_do_not_get_an_authenticity_finding(self):
        # A counterfeit Pro 3 and a genuine one look identical to a Pro 2 tool.
        report = evaluate(make_extraction(extracted_model_number="A3063"))
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
        report = evaluate(
            make_extraction(has_plastic_shrinkwrap=True, shrinkwrap_has_pull_tab=False)
        )
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
        report = evaluate(
            make_extraction(
                has_plastic_shrinkwrap=True,
                shrinkwrap_has_pull_tab=False,
                shrinkwrap_looks_shoddy=True,
            )
        )
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

    def test_year_beyond_the_observed_ceiling_fails(self):
        report = evaluate(make_extraction(copyright_year=MAXIMUM_COPYRIGHT_YEAR + 1))
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
            make_extraction(font_kerning_irregularities=True, visible_spelling_errors=["Appie"])
        )
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL
        assert "kerning" in detail_of(report, "Typography & Print Quality")


# --------------------------------------------------------------------------- #
# Verdict aggregation
# --------------------------------------------------------------------------- #


class TestVerdictAggregation:
    def test_clean_unit_is_genuine_but_never_fully_certain(self):
        report = evaluate(make_extraction())
        assert report.summary_verdict is Verdict.GENUINE
        # Capped below 1.0: photo evidence cannot rule out a cloned serial.
        assert 0.9 <= report.confidence_score <= 0.95

    def test_stacked_disqualifiers_raise_confidence(self):
        one = evaluate(make_extraction(has_plastic_shrinkwrap=True))
        two = evaluate(make_extraction(has_plastic_shrinkwrap=True, copyright_year=2019))
        three = evaluate(
            make_extraction(
                has_plastic_shrinkwrap=True, copyright_year=2019, extracted_model_number="B1234"
            )
        )
        assert one.confidence_score < two.confidence_score < three.confidence_score
        assert three.confidence_score <= 0.99

    def test_soft_signals_alone_flag_risk_with_lower_confidence(self):
        soft = evaluate(make_extraction(extracted_serial_number="TOO-SHORT"))
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

    def test_unsourced_rules_are_flagged_and_cannot_disqualify(self):
        # The serial-length rule is not externally sourced. Until it is, it must
        # never be able to drive a HIGH_RISK verdict on its own.
        for rule in RULES:
            if rule.source.startswith("UNSOURCED"):
                assert not rule.disqualifying, f"{rule.parameter} is unsourced but disqualifying"

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
    def test_letter_substitutions_still_fail(self, word):
        # Post 4's real counterfeit errors must survive the filter untouched.
        report = evaluate(make_extraction(visible_spelling_errors=[word]))
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL

    def test_a_real_error_alongside_accent_loss_still_fails(self):
        report = evaluate(make_extraction(visible_spelling_errors=["Audifonos", "MagSate"]))
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
        assert any("re-shoot" in step for step in steps)

    def test_no_open_checks_means_no_busywork(self):
        report = evaluate(make_extraction(visible_panels=["box_bottom_label", "box_back"]))
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
            ("A3063", "AirPods Pro 3"),                             # corpus posts 4 and 8
            ("A3053", "AirPods 4"),
            ("A2565", "AirPods (3rd generation)"),
            ("A2032", "AirPods (2nd generation)"),
            ("A1523", "AirPods (1st generation)"),
            ("A2096", "AirPods Max"),
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
