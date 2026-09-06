"""Unit tests for the deterministic rules engine.

Everything here runs on synthetic extractions - no images, no API calls, no
network. That is the payoff of splitting extraction from judgement: the half of
the pipeline that decides whether someone loses $200 is exhaustively testable.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import main as cli
from rules_engine import (
    HARDWARE_MATRIX,
    MINIMUM_COPYRIGHT_YEAR,
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
    @pytest.mark.parametrize(
        "variant, component, number",
        [
            (variant, component, number)
            for variant, components in HARDWARE_MATRIX.items()
            for component, numbers in components.items()
            for number in numbers
        ],
    )
    def test_every_matrix_entry_passes_against_its_own_variant(self, variant, component, number):
        report = evaluate(
            make_extraction(claimed_model_variant=variant, extracted_model_number=number)
        )
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.PASS
        assert component in detail_of(report, "Model Number Compatibility")

    def test_lookup_returns_variant_and_component(self):
        assert lookup_model_number("A2968") == [(VARIANT_USB_C, "Charging Case")]
        assert lookup_model_number("a2700") == [(VARIANT_LIGHTNING, "Charging Case")]
        assert lookup_model_number("A9999") == []

    def test_cross_variant_model_number_fails(self):
        # A Lightning case number on a USB-C box: a real counterfeit tell.
        report = evaluate(
            make_extraction(claimed_model_variant=VARIANT_USB_C, extracted_model_number="A2700")
        )
        assert status_of(report, "Model Number Compatibility") is EvidenceStatus.FAIL
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_number_outside_the_matrix_fails(self):
        report = evaluate(make_extraction(extracted_model_number="A9999"))
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
    def test_shrinkwrap_alone_condemns_an_otherwise_perfect_unit(self):
        report = evaluate(make_extraction(has_plastic_shrinkwrap=True, has_paper_pull_tabs=False))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL
        # Every other rule passes, so this proves the single disqualifier drives
        # the verdict on its own.
        assert report.summary_verdict is Verdict.HIGH_RISK_SUSPECTED

    def test_shrinkwrap_wins_over_a_contradictory_pull_tab_report(self):
        report = evaluate(make_extraction(has_plastic_shrinkwrap=True, has_paper_pull_tabs=True))
        assert status_of(report, "Packaging Seal Type") is EvidenceStatus.FAIL
        assert "contradictory" in detail_of(report, "Packaging Seal Type")

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

    def test_forward_dated_copyright_fails(self):
        future = dt.date.today().year + 5
        report = evaluate(make_extraction(copyright_year=future))
        assert status_of(report, "Copyright Year Constraint") is EvidenceStatus.FAIL
        assert "future" in detail_of(report, "Copyright Year Constraint")

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

    def test_kerning_alone_fails(self):
        report = evaluate(make_extraction(font_kerning_irregularities=True))
        assert status_of(report, "Typography & Print Quality") is EvidenceStatus.FAIL


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
                has_plastic_shrinkwrap=True, copyright_year=2019, extracted_model_number="A9999"
            )
        )
        assert one.confidence_score < two.confidence_score < three.confidence_score
        assert three.confidence_score <= 0.99

    def test_soft_signals_alone_flag_risk_with_lower_confidence(self):
        soft = evaluate(make_extraction(font_kerning_irregularities=True))
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
        assert set(report) == {"summary_verdict", "confidence_score", "evidence_ledger"}
        assert report["summary_verdict"] in {v.value for v in Verdict}
        assert 0.0 <= report["confidence_score"] <= 1.0
        for item in report["evidence_ledger"]:
            assert set(item) == {"tier", "parameter", "status", "detail"}
            assert item["tier"] in (1, 2)
            assert item["status"] in {s.value for s in EvidenceStatus}
            assert item["detail"].strip()

    def test_every_rule_produces_exactly_one_ledger_line(self):
        from rules_engine import RULES

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
