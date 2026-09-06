"""Deterministic rules engine for AirPods Pro 2 authentication.

No LLM reaches this module. It takes a validated ``VisionExtraction`` of pure
observations and evaluates it against the ground-truth hardware matrix in
REQUIREMENTS.md, producing an explainable Evidence Ledger.

Every rule is a pure function of (extraction, resolved variant) -> RuleOutcome,
so the same input always yields the same ledger. That is the whole point: an
LLM guessing "looks fake, 0.83" is unauditable; "A2700 is the Lightning
charging case but this box claims USB-C" is a fact a buyer can check.

Tiering follows REQUIREMENTS.md:
  * Tier 1 - the hardware model-number matrix (§1). Identity-level ground truth.
  * Tier 2 - packaging and visual signatures (§2), plus the derived print- and
    serial-format forensics that the extraction schema supports.

Tier is *not* the same axis as severity. Some Tier-2 findings are physically
impossible on a genuine unit (plastic shrink wrap; a copyright year predating
the product's release), so rules carry an explicit ``disqualifying`` flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from schema import (
    VARIANT_LIGHTNING,
    VARIANT_UNKNOWN,
    VARIANT_USB_C,
    EvidenceItem,
    EvidenceStatus,
    Verdict,
    VerificationReport,
    VisionExtraction,
)

# --------------------------------------------------------------------------- #
# Ground truth (REQUIREMENTS.md §1 and §2)
# --------------------------------------------------------------------------- #

#: variant -> component -> set of valid Apple A-numbers
HARDWARE_MATRIX: dict[str, dict[str, frozenset[str]]] = {
    VARIANT_USB_C: {
        "Left Earbud": frozenset({"A3047"}),
        "Right Earbud": frozenset({"A3048"}),
        "Charging Case": frozenset({"A3049", "A2968"}),
    },
    VARIANT_LIGHTNING: {
        "Left Earbud": frozenset({"A2931"}),
        "Right Earbud": frozenset({"A2699"}),
        "Charging Case": frozenset({"A2700"}),
    },
}

#: Earliest copyright year that can legitimately appear on each variant's box.
MINIMUM_COPYRIGHT_YEAR: dict[str, int] = {
    VARIANT_USB_C: 2023,       # released late 2023
    VARIANT_LIGHTNING: 2022,   # released 2022
}

#: Latest copyright year observed on genuine AirPods Pro 2 retail packaging.
#: Apple reprints boxes over a product's life, so this is an observed ceiling,
#: not a release date. A 2026 line on a Pro 2 box is out of range.
MAXIMUM_COPYRIGHT_YEAR = 2025

#: Used when the variant is still unknown at copyright-check time: the most
#: permissive bound, so an unidentified box is never failed on a technicality.
_PERMISSIVE_MINIMUM_YEAR = min(MINIMUM_COPYRIGHT_YEAR.values())

#: Apple serial numbers are uppercase alphanumeric. Legacy units use 12
#: characters; devices from the randomised-serial era use 10.
_VALID_SERIAL_LENGTHS = (10, 12)

#: Shape of an Apple model number, independent of which product it belongs to.
_MODEL_NUMBER_SHAPE = re.compile(r"A\d{4}")
_SERIAL_CHARSET_RE = re.compile(r"^[A-Z0-9]+$")

# --------------------------------------------------------------------------- #
# Scoring policy
# --------------------------------------------------------------------------- #

#: A ledger with less than this share of its weight actually evaluated cannot
#: clear an item -- absence of evidence is not evidence of authenticity.
MIN_COVERAGE_FOR_VERDICT = 0.60

#: A GENUINE verdict is capped below 1.0 on purpose. This pipeline inspects
#: packaging and printed identifiers; it cannot detect a counterfeit carrying a
#: cloned-but-valid serial, which is the exact failure mode of Apple's own
#: coverage lookup. Never tell a buyer a photo check is a certainty.
GENUINE_CONFIDENCE_CAP = 0.95

#: Confidence in a HIGH_RISK verdict. A single disqualifying finding is already
#: near-conclusive (these are physical impossibilities, not judgement calls);
#: each additional independent finding tightens it further.
_HIGH_RISK_DISQUALIFYING_BASE = 0.85
_HIGH_RISK_DISQUALIFYING_STEP = 0.07
_HIGH_RISK_MAX_CONFIDENCE = 0.99

#: Confidence when only soft signals (serial format, typography) failed. Real
#: evidence, but individually forgeable-looking enough to warrant a lower floor.
_HIGH_RISK_SOFT_BASE = 0.50
_HIGH_RISK_SOFT_STEP = 0.10
_HIGH_RISK_SOFT_MAX = 0.70


@dataclass(frozen=True)
class RuleOutcome:
    """Internal result of one rule, before it becomes a ledger line."""

    status: EvidenceStatus
    detail: str


@dataclass(frozen=True)
class Rule:
    tier: int
    parameter: str
    weight: float
    #: True when a FAIL is on its own sufficient to call the item high risk.
    disqualifying: bool
    evaluate: Callable[[VisionExtraction, str], RuleOutcome]


# --------------------------------------------------------------------------- #
# Matrix helpers
# --------------------------------------------------------------------------- #


def lookup_model_number(model_number: str) -> list[tuple[str, str]]:
    """Return every ``(variant, component)`` pair a given A-number belongs to.

    An empty list means the number is not an AirPods Pro 2 part at all.
    """
    hits: list[tuple[str, str]] = []
    needle = model_number.strip().upper()
    for variant, components in HARDWARE_MATRIX.items():
        for component, numbers in components.items():
            if needle in numbers:
                hits.append((variant, component))
    return hits


def resolve_variant(extraction: VisionExtraction) -> str:
    """Decide which variant's spec sheet the rest of the rules should use.

    The claim printed on the box wins when present. When the box does not state
    a variant, a recognised model number identifies it -- that inference is
    reported in the ledger so the reasoning stays visible.
    """
    if extraction.claimed_model_variant != VARIANT_UNKNOWN:
        return extraction.claimed_model_variant
    if extraction.extracted_model_number:
        hits = lookup_model_number(extraction.extracted_model_number)
        variants = {variant for variant, _ in hits}
        if len(variants) == 1:
            return variants.pop()
    return VARIANT_UNKNOWN


def _describe_components(hits: Iterable[tuple[str, str]]) -> str:
    return ", ".join(f"{variant} {component}" for variant, component in hits)


# --------------------------------------------------------------------------- #
# Tier 1 - hardware model-number matrix
# --------------------------------------------------------------------------- #


def _rule_model_number(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    number = extraction.extracted_model_number
    if not number:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No Apple model number (A-number) was legible in the supplied images. "
            "Photograph the fine print on the box underside or the inside of the case lid.",
        )

    hits = lookup_model_number(number)
    if not hits:
        # An A-number this tool does not know is not evidence of a counterfeit.
        # Apple uses A-numbers across every generation, and this matrix only
        # covers Pro 2 - so an unrecognised one usually means "different
        # product", not "fake". Corpus evidence: A3063 appeared on both a
        # confirmed counterfeit and a confirmed genuine unit.
        if _MODEL_NUMBER_SHAPE.fullmatch(number):
            return RuleOutcome(
                EvidenceStatus.UNKNOWN,
                f"Extracted model {number} is a well-formed Apple model number but is not an "
                f"AirPods Pro 2 part. Pro 2 uses A2931/A2699/A2700 (Lightning) and "
                f"A3047/A3048/A3049/A2968 (USB-C). This is most likely a different AirPods "
                f"generation, which this tool does not yet cover - confirm which product it is "
                f"before relying on any result here.",
            )
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Extracted model '{number}' is not a valid Apple model number at all. Genuine "
            f"Apple hardware carries an A-number in the form A#### - a malformed identifier "
            f"indicates fabricated packaging.",
        )

    matching = [hit for hit in hits if hit[0] == variant]

    # `variant` still Unknown with a recognised number means the number is shared
    # across variants, so it identifies the part but not the packaging.
    if variant == VARIANT_UNKNOWN:
        return RuleOutcome(
            EvidenceStatus.PASS,
            f"Extracted model {number} is a valid Pro 2 part ({_describe_components(hits)}), but "
            f"it is not unique to one variant, so the variant could not be determined.",
        )

    if matching:
        detail = f"Extracted model {number} matches {_describe_components(matching)}."
        if extraction.claimed_model_variant == VARIANT_UNKNOWN:
            # Keep the provenance of the variant visible: it was inferred from
            # this number, not read off the packaging.
            detail += (
                " The images did not state a variant, so it was inferred from this model number."
            )
        return RuleOutcome(EvidenceStatus.PASS, detail)

    return RuleOutcome(
        EvidenceStatus.FAIL,
        f"Cross-variant mismatch: {number} belongs to {_describe_components(hits)}, but the "
        f"item is presented as {variant}. Genuine packaging never pairs one variant's "
        f"model number with the other's branding.",
    )


# --------------------------------------------------------------------------- #
# Tier 2 - packaging and visual signatures
# --------------------------------------------------------------------------- #


def _rule_packaging_seal(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    if extraction.has_plastic_shrinkwrap:
        note = (
            " Paper pull-tabs were also reported, which is contradictory -- treat the "
            "shrink wrap as the decisive signal."
            if extraction.has_paper_pull_tabs
            else ""
        )
        return RuleOutcome(
            EvidenceStatus.FAIL,
            "Full plastic shrink wrap detected. Retail AirPods Pro 2 boxes are sealed with "
            "paper pull-tabs bearing green arrows on the top and bottom seams and never ship "
            "in plastic shrink wrap." + note,
        )
    if extraction.has_paper_pull_tabs:
        return RuleOutcome(
            EvidenceStatus.PASS,
            "Paper pull-tab seals with green arrows are present and no plastic shrink wrap "
            "was detected, matching Apple's retail packaging for this generation.",
        )
    return RuleOutcome(
        EvidenceStatus.UNKNOWN,
        "Neither plastic shrink wrap nor paper pull-tabs are visible, which is expected for "
        "an already-opened box. Seal type could not be assessed from these images.",
    )


def _rule_copyright_year(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    year = extraction.copyright_year
    if year is None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No copyright year was legible. It is printed in the fine print block on the "
            "underside of the box.",
        )

    if year > MAXIMUM_COPYRIGHT_YEAR:
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Copyright year {year} is later than any year observed on genuine AirPods Pro 2 "
            f"packaging (latest is {MAXIMUM_COPYRIGHT_YEAR}). Apple reprints boxes over a "
            f"product's life, but not beyond it.",
        )

    minimum = MINIMUM_COPYRIGHT_YEAR.get(variant, _PERMISSIVE_MINIMUM_YEAR)
    if year < minimum:
        if variant == VARIANT_UNKNOWN:
            return RuleOutcome(
                EvidenceStatus.FAIL,
                f"Copyright year {year} predates the AirPods Pro 2 line entirely; the earliest "
                f"variant (Lightning) launched in 2022.",
            )
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Copyright year {year} predates the {variant} release. Packaging for this variant "
            f"must carry a copyright of {minimum} or later.",
        )

    scope = variant if variant != VARIANT_UNKNOWN else "the AirPods Pro 2 line"
    return RuleOutcome(
        EvidenceStatus.PASS,
        f"Copyright year {year} is consistent with {scope} (minimum {minimum}).",
    )


def _rule_serial_format(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    serial = extraction.extracted_serial_number
    if not serial:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No serial number was legible in the supplied images.",
        )

    if not _SERIAL_CHARSET_RE.match(serial):
        illegal = sorted({char for char in serial if not char.isalnum()})
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Serial {serial} contains characters Apple does not use in serial numbers "
            f"({''.join(illegal) or 'non-alphanumeric characters'}).",
        )

    if len(serial) not in _VALID_SERIAL_LENGTHS:
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Serial {serial} is {len(serial)} characters long. Apple serial numbers are "
            f"{' or '.join(str(length) for length in _VALID_SERIAL_LENGTHS)} characters.",
        )

    return RuleOutcome(
        EvidenceStatus.PASS,
        f"Serial {serial} is well-formed ({len(serial)} uppercase alphanumeric characters). "
        f"Format only -- this check cannot detect a cloned serial copied from a genuine unit, "
        f"so confirm it separately at checkcoverage.apple.com.",
    )


def _rule_typography(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    kerning_note = (
        " A kerning or font-weight irregularity was also reported, but that observation is not "
        "treated as evidence on its own - see below."
        if extraction.font_kerning_irregularities
        else ""
    )

    if extraction.visible_spelling_errors:
        listed = ", ".join(f"'{word}'" for word in extraction.visible_spelling_errors)
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Misspelled text is printed on the packaging ({listed}). Apple's retail packaging is "
            f"proofed and offset-printed, so printed misspellings indicate a reproduction."
            + kerning_note,
        )

    if extraction.font_kerning_irregularities:
        # Measured 0-for-2 on a labelled corpus: kerning fired standalone only on
        # genuine units. Photo blur, compression and angle all mimic it, so it is
        # recorded as an observation and never as a finding.
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "A kerning or font-weight irregularity was reported, but no misspelled text was found. "
            "Photo blur, compression, and viewing angle reproduce this appearance on genuine "
            "packaging, so it is recorded as an observation rather than treated as evidence.",
        )

    return RuleOutcome(
        EvidenceStatus.PASS,
        "No misspelled text was found in the printed packaging copy.",
    )


# --------------------------------------------------------------------------- #
# Rule registry
# --------------------------------------------------------------------------- #

#: Ordered most to least decisive; the ledger is rendered in this order.
RULES: tuple[Rule, ...] = (
    Rule(
        tier=1,
        parameter="Model Number Compatibility",
        weight=5.0,
        disqualifying=True,
        evaluate=_rule_model_number,
    ),
    Rule(
        tier=2,
        parameter="Packaging Seal Type",
        weight=3.0,
        disqualifying=True,
        evaluate=_rule_packaging_seal,
    ),
    Rule(
        tier=2,
        parameter="Copyright Year Constraint",
        weight=2.0,
        disqualifying=True,
        evaluate=_rule_copyright_year,
    ),
    Rule(
        tier=2,
        parameter="Serial Number Format",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_serial_format,
    ),
    Rule(
        tier=2,
        parameter="Typography & Print Quality",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_typography,
    ),
)

_TOTAL_WEIGHT = sum(rule.weight for rule in RULES)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def evaluate(extraction: VisionExtraction) -> VerificationReport:
    """Run every rule and fold the outcomes into a verdict + Evidence Ledger."""
    variant = resolve_variant(extraction)

    ledger: list[EvidenceItem] = []
    evaluated_weight = 0.0
    disqualifying_failures = 0
    soft_failures = 0
    tier1_unresolved = False

    for rule in RULES:
        outcome = rule.evaluate(extraction, variant)
        ledger.append(
            EvidenceItem(
                tier=rule.tier,
                parameter=rule.parameter,
                status=outcome.status,
                detail=outcome.detail,
            )
        )

        if outcome.status is EvidenceStatus.UNKNOWN:
            if rule.tier == 1:
                tier1_unresolved = True
            continue

        evaluated_weight += rule.weight
        if outcome.status is EvidenceStatus.FAIL:
            if rule.disqualifying:
                disqualifying_failures += 1
            else:
                soft_failures += 1

    coverage = evaluated_weight / _TOTAL_WEIGHT if _TOTAL_WEIGHT else 0.0

    if disqualifying_failures or soft_failures:
        verdict = Verdict.HIGH_RISK_SUSPECTED
        if disqualifying_failures:
            confidence = min(
                _HIGH_RISK_MAX_CONFIDENCE,
                _HIGH_RISK_DISQUALIFYING_BASE
                + _HIGH_RISK_DISQUALIFYING_STEP * (disqualifying_failures - 1),
            )
        else:
            confidence = min(
                _HIGH_RISK_SOFT_MAX,
                _HIGH_RISK_SOFT_BASE + _HIGH_RISK_SOFT_STEP * (soft_failures - 1),
            )
    elif tier1_unresolved or coverage < MIN_COVERAGE_FOR_VERDICT:
        verdict = Verdict.INCONCLUSIVE
        confidence = coverage
    else:
        verdict = Verdict.GENUINE
        confidence = min(GENUINE_CONFIDENCE_CAP, coverage)

    return VerificationReport(
        summary_verdict=verdict,
        confidence_score=round(min(1.0, max(0.0, confidence)), 2),
        evidence_ledger=ledger,
    )


def evaluate_dict(payload: dict) -> dict:
    """Convenience wrapper: raw extraction dict in, Risk Output Schema dict out."""
    return evaluate(VisionExtraction.model_validate(payload)).to_dict()
