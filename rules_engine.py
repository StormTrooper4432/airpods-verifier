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
from pathlib import Path
from typing import Callable, Iterable

import yaml

from schema import (
    VARIANT_UNKNOWN,
    EvidenceItem,
    EvidenceStatus,
    Verdict,
    VerificationReport,
    VisionExtraction,
)

# --------------------------------------------------------------------------- #
# Ground truth (knowledge/airpods_pro_2.yaml)
# --------------------------------------------------------------------------- #

KNOWLEDGE_PATH = Path(__file__).with_name("knowledge") / "airpods_pro_2.yaml"


def load_knowledge(path: Path | str = KNOWLEDGE_PATH) -> dict:
    """Read the product knowledge base.

    Data lives in YAML so adding a generation is a data edit; rule logic stays
    in tested Python. Every factual entry carries a `source` - see the header of
    the YAML file for why.
    """
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


KNOWLEDGE = load_knowledge()

#: number -> {"variant": str, "component": str | None, "source": str}
MODEL_NUMBERS: dict[str, dict] = {
    entry["number"]: entry for entry in KNOWLEDGE["model_numbers"]
}

#: variant -> {"min": int, "max": int, "source": str}
COPYRIGHT_YEARS: dict[str, dict] = KNOWLEDGE["copyright_years"]

MINIMUM_COPYRIGHT_YEAR: dict[str, int] = {
    variant: bounds["min"] for variant, bounds in COPYRIGHT_YEARS.items()
}

#: Latest copyright year observed on genuine Pro 2 retail packaging. Apple
#: reprints boxes over a product's life, so this is an observed ceiling.
MAXIMUM_COPYRIGHT_YEAR: int = max(bounds["max"] for bounds in COPYRIGHT_YEARS.values())

SHRINKWRAP_RULES: dict = KNOWLEDGE["packaging"]["shrinkwrap"]

#: Used when the variant is still unknown at copyright-check time: the most
#: permissive bound, so an unidentified box is never failed on a technicality.
_PERMISSIVE_MINIMUM_YEAR = min(MINIMUM_COPYRIGHT_YEAR.values())

#: Apple serial numbers are uppercase alphanumeric. Legacy units use 12
#: characters; devices from the randomised-serial era use 10.
#: NOTE: this length constraint has no external source - see RULE_SOURCES.
_VALID_SERIAL_LENGTHS = (10, 12)
_SERIAL_CHARSET_RE = re.compile(r"^[A-Z0-9]+$")

#: Shape of an Apple model number, independent of which product it belongs to.
_MODEL_NUMBER_SHAPE = re.compile(r"A\d{4}")


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
    #: Where this rule's factual claim comes from. Required, and enforced by a
    #: test: a rule with no provenance is an invented specification, which is
    #: how a counterfeit detector ends up containing counterfeit information.
    source: str
    #: Observation-only rules never contribute a FAIL and carry no weight.
    informational: bool = False


# --------------------------------------------------------------------------- #
# Matrix helpers
# --------------------------------------------------------------------------- #


def lookup_model_number(model_number: str) -> list[tuple[str, str]]:
    """Return every ``(variant, component)`` pair a given A-number belongs to.

    An empty list means the number is not a known Pro 2 part. ``component`` is
    a human-readable label; where the source does not say which component a
    number belongs to, it reads "part" rather than a guessed left/right.
    """
    entry = MODEL_NUMBERS.get(model_number.strip().upper())
    if entry is None:
        return []
    return [(entry["variant"], entry.get("component") or "part")]


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
    """Seal check.

    Being shrink-wrapped is NOT evidence of a counterfeit. Genuine sealed Pro 2
    units do ship wrapped; the discriminator is whether the wrap has a side pull
    tab and is tight with minimal seams. The blanket "wrapped means fake" claim
    is common online and wrong, and coding it would accuse genuine sealed units.
    """
    if extraction.has_plastic_shrinkwrap:
        if extraction.shrinkwrap_has_pull_tab is False:
            return RuleOutcome(
                EvidenceStatus.FAIL,
                "The box is shrink-wrapped with no pull tab at the side. Genuine wrapped retail "
                "units carry a tear-strip pull tab; wrap without one indicates the box was "
                "re-sealed after the fact.",
            )
        if extraction.shrinkwrap_looks_shoddy:
            return RuleOutcome(
                EvidenceStatus.FAIL,
                "The shrink wrap is loose, wrinkled, yellowed, or heavily seamed. Apple's factory "
                "wrap is tight with minimal seams.",
            )
        if extraction.shrinkwrap_has_pull_tab:
            return RuleOutcome(
                EvidenceStatus.PASS,
                "The box is shrink-wrapped with a side pull tab and the wrap looks tight and "
                "cleanly seamed, which is consistent with factory-sealed retail stock. Note that "
                "most genuine Pro 2 units are not wrapped at all - either state is normal.",
            )
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "The box is shrink-wrapped, but the sides are not visible so the presence of a pull "
            "tab could not be checked. Wrapping alone is not evidence either way; photograph the "
            "box edges to resolve this.",
        )

    if extraction.has_paper_pull_tabs:
        return RuleOutcome(
            EvidenceStatus.PASS,
            "Paper pull-tab seals with green arrows are present and the box is not wrapped, "
            "matching Apple's retail packaging for this generation.",
        )

    return RuleOutcome(
        EvidenceStatus.UNKNOWN,
        "Neither plastic wrap nor paper pull-tabs are visible, which is expected for an "
        "already-opened box. Seal type could not be assessed from these images.",
    )


def _rule_copyright_year(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    year = extraction.copyright_year
    if year is None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No copyright year was legible. It is printed in the fine print block on the "
            "underside of the box.",
        )

    ceiling = COPYRIGHT_YEARS.get(variant, {}).get("max", MAXIMUM_COPYRIGHT_YEAR)
    if year > ceiling:
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Copyright year {year} is later than any year observed on genuine AirPods Pro 2 "
            f"packaging (latest is {ceiling}). Apple reprints boxes over a product's life, but "
            f"not beyond it.",
        )

    minimum = COPYRIGHT_YEARS.get(variant, {}).get('min', _PERMISSIVE_MINIMUM_YEAR)
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


def _rule_visible_panels(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    """Record which surfaces were photographed. Observation only.

    This never fails. Panel identification is not yet accurate enough to drive a
    finding - the extractor has confidently mislabelled a Pro 3 box as a Pro 2 -
    and a wrong panel ID feeding a rule would manufacture false positives. Its
    accuracy is being measured against a labelled corpus first; only then do the
    panel-dependent rules in the knowledge base get promoted.
    """
    panels = extraction.visible_panels
    if not panels:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No product surface could be identified in these images.",
        )

    labels = {
        "box_bottom_label": "the box underside label",
        "box_back": "the box rear panel",
        "box_front": "the box front",
        "box_side": "a box side",
        "box_top": "the box top",
        "case_exterior": "the case exterior",
        "case_lid_underside": "the underside of the case lid",
        "earbud": "an earbud",
        "accessories": "accessories",
        "other": "an unidentified surface",
    }
    seen = ", ".join(labels.get(panel, panel) for panel in panels)
    missing = [
        name
        for panel, name in (
            ("box_bottom_label", "the box underside label (model number and serial)"),
            ("case_lid_underside", "the underside of the case lid (serial)"),
        )
        if panel not in panels
    ]
    detail = f"Images show: {seen}."
    if missing:
        detail += " Not photographed: " + "; ".join(missing) + "."
    return RuleOutcome(EvidenceStatus.UNKNOWN, detail)


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
        source="Apple, 'Identify your AirPods' (support.apple.com)",
    ),
    Rule(
        tier=2,
        parameter="Packaging Seal Type",
        weight=3.0,
        disqualifying=True,
        evaluate=_rule_packaging_seal,
        source="r/AreMyAirpodsAuthentic community guide, Step 1 (ruled authoritative by owner)",
    ),
    Rule(
        tier=2,
        parameter="Copyright Year Constraint",
        weight=2.0,
        disqualifying=True,
        evaluate=_rule_copyright_year,
        source="Apple 'Identify your AirPods' (introduction years); owner (2025 ceiling)",
    ),
    Rule(
        tier=2,
        parameter="Serial Number Format",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_serial_format,
        source="UNSOURCED - length constraint is unverified; do not promote to Tier 1",
    ),
    Rule(
        tier=2,
        parameter="Typography & Print Quality",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_typography,
        source="Community guide (printed misspellings); corpus measurement (kerning excluded)",
    ),
    Rule(
        tier=2,
        parameter="Photographed Surfaces",
        weight=0.0,
        disqualifying=False,
        evaluate=_rule_visible_panels,
        source="Observation only - no factual claim made",
        informational=True,
    ),
)

_TOTAL_WEIGHT = sum(rule.weight for rule in RULES if not rule.informational)


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
                source=rule.source,
            )
        )

        # Observation-only rules are reported but never scored.
        if rule.informational:
            continue

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
