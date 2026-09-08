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

import datetime

import inspect
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import yaml

from font_forensics import FONT_SCATTER_THRESHOLD, MEASURABLE_GLYPH_HEIGHT_PX
from schema import (
    VARIANT_UNKNOWN,
    EvidenceItem,
    LocalAnalysis,
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

#: A-number -> generation name, for generations this tool identifies but does
#: not verify. Naming the product beats reporting "unknown number".
OTHER_GENERATIONS: dict[str, dict] = {}
for _entry in KNOWLEDGE.get("other_generations", []) + KNOWLEDGE.get("charging_cases", []):
    for _number in _entry["numbers"]:
        # A2190 covers two different cases that differ only by serial suffix.
        # Keep the first and note the ambiguity rather than silently dropping one.
        if _number in OTHER_GENERATIONS:
            existing = OTHER_GENERATIONS[_number]
            OTHER_GENERATIONS[_number] = {
                **existing,
                "name": f"{existing['name']} or {_entry['name']}",
                "ambiguous": True,
            }
        else:
            OTHER_GENERATIONS[_number] = _entry

SHRINKWRAP_RULES: dict = KNOWLEDGE["packaging"]["shrinkwrap"]

#: Accent-stripped forms of words that legitimately appear on Pro 2 packaging.
EXPECTED_ACCENTED_WORDS: frozenset[str] = frozenset(
    entry.lower()
    for entry in KNOWLEDGE["packaging"].get("expected_accented_words", [])
    if isinstance(entry, str)
)


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)
    )


def is_accent_loss(word: str) -> bool:
    """True if this "misspelling" is just a word that lost its diacritics.

    OCR drops accents routinely, and the extractor then reports its own reading
    loss as a printing defect. Corpus post 3 was accused this way on a genuine
    box that printed "Audifonos inalambricos" correctly. Real counterfeit
    misspellings are letter substitutions - post 4's "MagSate", "Appie",
    "Bums", "swalliwed" - which survive this filter untouched.
    """
    normalised = strip_accents(word).lower().strip(" .,;:!?()")
    return any(normalised == expected for expected in EXPECTED_ACCENTED_WORDS)

#: Used when the variant is still unknown at copyright-check time: the most
#: permissive bound, so an unidentified box is never failed on a technicality.
_PERMISSIVE_MINIMUM_YEAR = min(MINIMUM_COPYRIGHT_YEAR.values())

#: Observed across the owner's four genuine units, seven corpus listings and two
#: confirmed counterfeits: every serial is 10 uppercase alphanumeric characters,
#: except the AirPods Pro 1 (2019) at 12.
#:
#: This does NOT discriminate counterfeits - both confirmed fakes use 10
#: characters, exactly like genuine units. It catches a fabricated or misread
#: identifier, which is why the rule is non-disqualifying.
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
    evaluate: Callable[..., RuleOutcome]
    #: Where this rule's factual claim comes from. Required, and enforced by a
    #: test: a rule with no provenance is an invented specification, which is
    #: how a counterfeit detector ends up containing counterfeit information.
    source: str
    #: Observation-only rules never contribute a FAIL and carry no weight.
    informational: bool = False
    #: Panels ANY of which can answer this rule. Empty means "any image might".
    #: Used to tell "you did not photograph this" apart from "I looked and it
    #: was not legible" - two UNKNOWNs that need completely different advice.
    answerable_from: tuple[str, ...] = ()
    #: Panels ALL of which are needed. A cross-check between two surfaces is not
    #: answerable from either one alone, so without this the advice reads
    #: "re-shoot what you sent" when the real problem is a surface never
    #: photographed at all.
    requires_panels: tuple[str, ...] = ()


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


def identifies_another_product(extraction: VisionExtraction) -> str | None:
    """Name the product when the model number says this is NOT an AirPods Pro 2.

    Packaging ground truth in this project is Pro 2 specific - the pull-tab
    seal comes from a Pro 2 guide and the copyright range from Pro 2 release
    dates. Applying either to a different generation is an unsourced
    extrapolation, and it produced a false accusation on a genuine AirPods 4
    ANC whose packaging simply differs.
    """
    number = extraction.extracted_model_number
    if not number:
        return None
    entry = OTHER_GENERATIONS.get(number)
    return entry["name"] if entry else None


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
        generation = OTHER_GENERATIONS.get(number)
        if generation is not None:
            return RuleOutcome(
                EvidenceStatus.UNKNOWN,
                # Precise about what is and is not withheld. The checks sourced
                # for Pro 2 packaging - seal type, copyright range, this one -
                # are scoped out. The checks that hold for any Apple product are
                # not: Apple never repeats a serial and never ships a box whose
                # label disagrees with the case inside it, whatever the
                # generation. So this item can still be CONDEMNED on those, and
                # only clearing it is impossible. Saying "no finding can be
                # made" overstated it, and a buyer who reads that and stops
                # looking is worse off than one who reads the rows below.
                f"Extracted model {number} is a genuine Apple model number, but it belongs to "
                f"{generation['name']} (introduced {generation['introduced']}), not AirPods Pro 2. "
                f"The checks sourced specifically for Pro 2 packaging are withheld, so this unit "
                f"cannot be cleared here - a counterfeit {generation['name']} and a genuine one "
                f"look the same to those. The serial checks below still apply to any Apple "
                f"product and can still catch a fake.",
            )

        if _MODEL_NUMBER_SHAPE.fullmatch(number):
            return RuleOutcome(
                EvidenceStatus.UNKNOWN,
                f"Extracted model {number} is a well-formed Apple model number but matches no "
                f"AirPods Pro generation known to this tool. It may be another Apple product, a "
                f"generation not yet in the knowledge base, or a misread - confirm the number "
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


#: Panels that can actually show the tear-strip. The tab runs along a side
#: edge, so the front face cannot establish its absence however sharp the
#: photograph is. Measured on the corpus: of four items failed for a missing
#: tab, three were photographed from a side and one - post 13 - from the front
#: alone, and that one is the only item where this rule out-claimed the domain
#: expert.
#:
#: box_top was in this set and has been removed. Two failure modes needed to
#: turn it into a false accusation have both been seen in this extractor: it
#: has reported shrink-wrap on an already-opened box (post 6), and it has
#: labelled the interior tray of an opened box "box_top" (post 10). Together
#: those manufacture an edge view that does not exist. box_side is unambiguous
#: - an interior tray is never mistaken for it - and dropping box_top costs
#: nothing measurable: every corpus item failed for a missing tab also carries
#: box_side, so no verdict changes.
_PANELS_SHOWING_AN_EDGE = frozenset({"box_side"})


def _rule_packaging_seal(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    """Seal check.

    Being shrink-wrapped is NOT evidence of a counterfeit. Genuine sealed Pro 2
    units do ship wrapped; the discriminator is whether the wrap has a side pull
    tab and is tight with minimal seams. The blanket "wrapped means fake" claim
    is common online and wrong, and coding it would accuse genuine sealed units.
    """
    other = identifies_another_product(extraction)
    if other is not None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"This item's model number identifies it as {other}, and the seal expectations here "
            f"are sourced for AirPods Pro 2 packaging specifically. Applying them to a different "
            f"generation would be a guess, so no finding is made.",
        )

    if extraction.has_plastic_shrinkwrap:
        # "Looks shoddy" is a subjective quality judgement, and those have now
        # produced a false positive twice: kerning on posts 3 and 7, and this
        # field on post 6 - where the model reported wrap AND shoddy wrap on an
        # opened box with no plastic on it at all. It is recorded as a note and
        # never fails on its own. Only the objective, checkable condition -
        # wrapped with no side pull tab - is treated as evidence.
        shoddy_note = (
            " The wrap was also described as loose or poorly finished, which is recorded as an "
            "observation only."
            if extraction.shrinkwrap_looks_shoddy
            else ""
        )

        if extraction.shrinkwrap_has_pull_tab is False:
            # The tab is on the SIDE. A photograph of the front face cannot
            # establish that it is missing, and a model reporting "no tab" from
            # one is reporting the limit of its viewpoint, not a fact about the
            # box. Corpus post 13 was accused on exactly that: three
            # photographs, none of them an edge, wrapped front only - and the
            # domain expert called it inconclusive.
            #
            # This is the converse of the standing rule that absence of
            # evidence is never evidence of authenticity. It is not evidence of
            # forgery either.
            if not (set(extraction.visible_panels) & _PANELS_SHOWING_AN_EDGE):
                return RuleOutcome(
                    EvidenceStatus.UNKNOWN,
                    "The box is shrink-wrapped and no pull tab is visible, but no photograph "
                    "shows a side edge - which is where the tear-strip runs. Absence from a "
                    "view that could not show it is not a finding. Photograph the side of the "
                    "box and this resolves either way." + shoddy_note,
                )
            return RuleOutcome(
                EvidenceStatus.FAIL,
                "The box is shrink-wrapped with no pull tab at the side. Genuine wrapped retail "
                "units carry a tear-strip pull tab; wrap without one indicates the box was "
                "re-sealed after the fact." + shoddy_note,
            )
        if extraction.shrinkwrap_has_pull_tab:
            return RuleOutcome(
                EvidenceStatus.PASS,
                "The box is shrink-wrapped and a side pull tab is present, consistent with "
                "factory-sealed retail stock. Most genuine Pro 2 units are not wrapped at all - "
                "either state is normal." + shoddy_note,
            )
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "The box was reported as shrink-wrapped, but no side pull tab could be seen either "
            "way. Wrapping alone is not evidence; photograph the box edges to resolve this."
            + shoddy_note,
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


def _this_year() -> int:
    """The current calendar year.

    A function rather than a module constant so a long-running process cannot
    hold a stale year, and so tests can freeze it.
    """
    return datetime.date.today().year


def _rule_copyright_year(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    other = identifies_another_product(extraction)
    if other is not None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"This item's model number identifies it as {other}. The copyright range here is "
            f"derived from AirPods Pro 2 release dates and does not apply to another generation.",
        )

    year = extraction.copyright_year
    if year is None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No copyright year was legible. It is printed in the fine print block on the "
            "underside of the box.",
        )

    # A year later than the present one is impossible: no box is printed in the
    # future. That is the only ceiling that cannot age, and it is the only one
    # that convicts.
    if year > _this_year():
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Copyright year {year} is later than the current year ({_this_year()}). "
            f"A box cannot be printed with a future copyright.",
        )

    # Above the OBSERVED ceiling is an observation, not a conviction. The
    # ceiling is four genuine units seen by one person, and it does not move
    # when the calendar does: on 2026-09-08 it stood at 2025, so a genuine box
    # reprinted this year was being called HIGH RISK. Measured on the corpus,
    # softening this costs nothing - the only item it convicted, post 9, is a
    # confirmed counterfeit that fails Typography & Print Quality as well.
    ceiling = COPYRIGHT_YEARS.get(variant, {}).get("max", MAXIMUM_COPYRIGHT_YEAR)
    if year > ceiling:
        return RuleOutcome(
            EvidenceStatus.PASS,
            f"Copyright year {year} is later than any year observed on genuine "
            f"{variant} packaging (latest seen is {ceiling}), which is recorded but not "
            f"charged: Apple reprints boxes, and the observed ceiling is a handful of "
            f"units rather than a specification.",
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


#: Genuine label prints "(S) Serial No." with a space after the (S) marker.
_SERIAL_LABEL_RE = re.compile(r"\(\s*S\s*\)(?P<gap>\s*)serial", re.IGNORECASE)


#: Glyph pairs an OCR confuses on printed serials. Observed: a genuine label
#: reading L0YT6B4RKN came back as LOYT6B4RKN - letter O for digit zero - off a
#: reflective panel.
#:
#: These fold ONLY for comparison, never for display: the buyer is shown what
#: was read. A mismatch that survives folding differs by something no reader
#: confuses, while two genuinely different serials still differ in many
#: characters, so this cannot mask a real mismatch. Accusing someone because a
#: highlight turned a zero into an O would be the same class of error as the
#: reflective-print misspelling above.
_CONFUSABLE_GLYPHS = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "S": "5", "Z": "2", "B": "8", "G": "6",
})


def _same_serial(left: str, right: str) -> bool:
    """Equal once glyphs no reader can tell apart are folded together."""
    return left.upper().translate(_CONFUSABLE_GLYPHS) == right.upper().translate(
        _CONFUSABLE_GLYPHS
    )


def _rule_serial_cross_match(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    """Check the serials against each other.

    The strongest rule available that needs no reference data at all: it asks
    only whether the identifiers on one item are internally consistent, so it
    works on any generation, any region, and cannot be defeated by cloning a
    serial from a genuine unit - the thing that defeats Apple's own coverage
    lookup.

    Apple prints a serial in four places: the box label, the underside of the
    case lid, and each earbud stem. The box and case serials identify the same
    case and must match. Each earbud carries its OWN serial, different from the
    other bud and from the case. An earbud serial equal to the case or box
    serial is a counterfeit tell, because a genuine unit never repeats one.
    """
    box = extraction.extracted_serial_number
    case = extraction.serial_case_lid
    left = extraction.serial_left_earbud
    right = extraction.serial_right_earbud

    known = {"box label": box, "case lid": case, "left earbud": left, "right earbud": right}
    present = {name: value for name, value in known.items() if value}
    if len(present) < 2:
        missing = [name for name, value in known.items() if not value]
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"Only {len(present)} of the four serials was legible, so they cannot be cross-checked. "
            f"Apple prints one on the box label, one under the case lid and one on each earbud "
            f"stem. Photograph: {', '.join(missing)}.",
        )

    # Serials that must be DIFFERENT from one another. A repeat means one
    # component was cloned or the printing is fabricated.
    duplicates: list[str] = []
    unique_required = [
        (name, value) for name, value in present.items() if name != "box label"
    ]
    for index, (name, value) in enumerate(unique_required):
        for other_name, other_value in unique_required[index + 1 :]:
            if name == "case lid" or other_name == "case lid":
                continue  # box/case equality is handled separately below
            if _same_serial(value, other_value):
                duplicates.append(f"{name} and {other_name} share the serial {value}")

    for name, value in unique_required:
        if name != "case lid" and case and _same_serial(value, case):
            duplicates.append(f"the {name} repeats the case serial {value}")

    if duplicates:
        return RuleOutcome(
            EvidenceStatus.FAIL,
            "Serials that must differ are identical: "
            + "; ".join(duplicates)
            + ". On a genuine set the case and each earbud carry their own distinct serial, so a "
            "repeated one indicates fabricated printing or a swapped component.",
        )

    if box and case and not _same_serial(box, case):
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"The box label serial ({box}) does not match the serial under the case lid ({case}). "
            f"These identify the same case and always agree on a genuine set - a mismatch means "
            f"the case in this box is not the case it was sold with.",
        )

    checked = ", ".join(sorted(present))
    if box and case:
        return RuleOutcome(
            EvidenceStatus.PASS,
            f"The box label and case lid serials match, and every serial that should be distinct "
            f"is distinct ({checked}). This is an internal-consistency check, so unlike a coverage "
            f"lookup it is not defeated by a serial cloned from a genuine unit.",
        )
    return RuleOutcome(
        EvidenceStatus.UNKNOWN,
        f"The serials read so far are all distinct as expected ({checked}), but the box label and "
        f"case lid serials could not both be read, so the pair that must MATCH is unverified. "
        f"Photograph the box underside label and the underside of the case lid.",
    )


def _rule_serial_label_spacing(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    """Check the '(S) Serial' marker on the box label.

    Genuine labels print a space between the (S) marker and the word Serial.
    Counterfeit labels routinely run them together. This is a binary, printed
    feature - no subjective judgement - which is why it is trusted where
    "looks shoddy" and kerning are not.
    """
    literal = extraction.serial_label_literal
    if not literal:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "The serial line on the box label was not legible, so the '(S) Serial' marker could "
            "not be checked. Photograph the underside label straight on and close up.",
        )

    match = _SERIAL_LABEL_RE.search(literal)
    if match is None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"No '(S) Serial' marker was found in the transcribed label text ({literal!r}), so "
            f"the spacing could not be checked.",
        )

    if match.group("gap") == "":
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"The box label prints '(S)Serial' with no space after the (S) marker "
            f"({literal!r}). Genuine Apple labels place a space there. This is one of the most "
            f"reliable single tells on the printed label.",
        )

    return RuleOutcome(
        EvidenceStatus.PASS,
        f"The box label prints '(S) Serial' with the expected space after the (S) marker "
        f"({literal!r}).",
    )


#: Surfaces that actually carry printed packaging copy. "No misspellings found"
#: means nothing unless one of them was photographed - the case, the lid and the
#: earbuds carry no prose to misspell.
_PANELS_CARRYING_PRINTED_COPY = frozenset({"box_back", "box_front", "box_bottom_label"})

#: A reported misspelling has to look like a word. The extractor returns OCR
#: fragments alongside real findings - corpus post 15 reported 'b,' next to
#: 'Apnie' - and a fragment is a reading artefact, not a printing defect.
_MINIMUM_MISSPELLING_LETTERS = 3

#: How many misspellings it takes to convict.
#:
#: One is not enough, and this was paid for. Newer AirPods packaging prints on a
#: finish that throws highlights, and a washed-out stroke turns "Apple" into
#: "Appie" - a single l-to-i substitution. Corpus post 21, a genuine retail box
#: with a price sticker still on it, was called HIGH RISK on exactly that.
#:
#: The pattern cannot separate the cases. A real counterfeit in the corpus reads
#: "Califomia" (rn-to-m) and another "certifed", both single substitutions an
#: OCR would plausibly make. Sharpness does not separate them either: the
#: reflective genuine box scores 220 against 182 and 492 for two fakes.
#:
#: What does separate them is how many. Measured across the labelled corpus:
#:
#:     GENUINE                0 of 5 items reported ANY misspelling
#:     HIGH_RISK_SUSPECTED    4 of 9, at counts 4, 2, 1, 1
#:
#: So two or more is a printing defect and one is a reading artefact. The cost
#: is real and was measured before making the change: corpus posts 2 and 16 are
#: counterfeits whose only evidence was a single misspelling, and both fall to
#: INCONCLUSIVE, taking agreement from 13/20 to 11/20.
#:
#: That trade is deliberate. INCONCLUSIVE tells a buyer the tool cannot settle
#: it and asks for another photograph. Accusing a genuine box tells them to walk
#: away from a real unit, and it will keep happening as reflective packaging
#: becomes the norm.
_MISSPELLINGS_TO_CONVICT = 2


def _is_word_shaped(token: str) -> bool:
    return sum(character.isalpha() for character in token) >= _MINIMUM_MISSPELLING_LETTERS


def _rule_typography(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    kerning_note = (
        " A kerning or font-weight irregularity was also reported, but that observation is not "
        "treated as evidence on its own - see below."
        if extraction.font_kerning_irregularities
        else ""
    )

    reported = list(extraction.visible_spelling_errors)
    fragments = [word for word in reported if not _is_word_shaped(word)]
    words = [word for word in reported if _is_word_shaped(word)]
    accent_only = [word for word in words if is_accent_loss(word)]
    real_errors = [word for word in words if not is_accent_loss(word)]
    fragment_note = (
        f" {len(fragments)} reported item(s) too short to be a word were discarded as reading "
        f"artefacts."
        if fragments
        else ""
    )

    if len(real_errors) == 1:
        # One is a reading artefact until something corroborates it.
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"One misspelling was read from the packaging ('{real_errors[0]}'). A single one is "
            f"reported and not charged: reflective print turns a washed-out stroke into a wrong "
            f"letter, and a genuine box has been accused this way. Two or more is a printing "
            f"defect. Re-photograph the printed panels without glare if you want this settled."
            + fragment_note
            + kerning_note,
        )

    if real_errors:
        listed = ", ".join(f"'{word}'" for word in real_errors)
        return RuleOutcome(
            EvidenceStatus.FAIL,
            f"Misspelled text is printed on the packaging ({listed}). Apple's retail packaging is "
            f"proofed and offset-printed, so printed misspellings indicate a reproduction."
            + fragment_note
            + kerning_note,
        )

    if accent_only:
        dropped = ", ".join(f"'{word}'" for word in accent_only)
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"The only reported spelling issues ({dropped}) are words that appear on genuine "
            f"packaging and differ solely by missing accents. OCR routinely drops diacritics, so "
            f"this is treated as a reading artefact rather than a printing defect." + kerning_note,
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

    # Finding no misspellings in copy nobody photographed is not a pass. The
    # case, the lid and the earbuds carry no printed prose, so a clean reading
    # of them says nothing about the packaging - and granting PASS here adds
    # weight toward a verdict on the strength of absent evidence, which is the
    # one thing this pipeline must never do.
    if not (set(extraction.visible_panels) & _PANELS_CARRYING_PRINTED_COPY):
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "No misspellings were reported, but no surface carrying printed packaging copy was "
            "photographed - the case, the lid and the earbuds have no prose to check. Photograph "
            "the box back or the underside label and this becomes a real reading."
            + fragment_note,
        )

    return RuleOutcome(
        EvidenceStatus.PASS,
        "No misspelled text was found in the printed packaging copy." + fragment_note,
    )


def _rule_visible_panels(extraction: VisionExtraction, variant: str) -> RuleOutcome:
    """Record which surfaces were photographed. Observation only.

    This never fails. Panel identification is not yet accurate enough to drive a
    finding - the extractor has confidently mislabelled a Pro 3 box as a Pro 2 -
    and a wrong panel ID feeding a rule would manufacture false positives. Its
    accuracy is being measured against a labelled corpus first; only then do the
    panel-dependent rules in the knowledge base get promoted.

    Two rules DO now consult the panel list, but only to withhold a conclusion:
    a missing pull tab needs an edge view, and a clean spelling reading needs a
    printed surface. A wrong panel there costs a finding, never invents one.
    Measured on the nine posts where those guards decide something, the list was
    right eight times; the ninth called an opened box's interior tray box_top.
    See the panel-accuracy note in knowledge/airpods_pro_2.yaml.
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


#: Preference order when two panels would unblock the same number of checks.
#: The box underside label carries the part number, model numbers and serial in
#: one printed block, so it is the single most informative photograph anyone can
#: take; hardware serials are a fallback when the box is gone.
PANEL_PRIORITY: tuple[str, ...] = (
    "box_bottom_label",
    "box_back",
    "case_lid_underside",
    "earbud",
    "box_side",
    "box_front",
    "box_top",
    "case_exterior",
)


#: panel -> how to ask a seller (or yourself) for that photograph.
PANEL_REQUESTS: dict[str, str] = {
    "box_bottom_label": (
        "Photograph the sticker on the underside of the box - it carries the part number, the "
        "model numbers and the serial."
    ),
    "box_back": (
        "Photograph the back of the box - it carries the copyright line and the regulatory marks."
    ),
    "case_lid_underside": (
        "Open the case and fill the frame with the underside of the lid - get as close as your "
        "camera will focus. The serial is etched there at about a millimetre, and it should match "
        "the one on the box. Standing back far enough to get the whole case in shot is what makes "
        "it unreadable: measured, a lid filling the frame reads correctly and the same lid at "
        "arm's length does not read at all."
    ),
    "earbud": (
        "Photograph the stem of each earbud as close as your camera will focus - each carries its "
        "own serial, different from the case and from each other. This print is smaller than the "
        "lid's, so a whole-earbud shot will not resolve it."
    ),
    "box_front": "Photograph the front of the box.",
    "box_side": "Photograph the sides of the box, including any seals or pull tabs.",
    "box_top": "Photograph the top of the box, including the seams.",
    "case_exterior": "Photograph the outside of the case, including the hinge and the back button.",
}


def _rule_label_typography(
    extraction: VisionExtraction, variant: str, local: LocalAnalysis | None = None
) -> RuleOutcome:
    """Compare the label's letterforms against San Francisco.

    Wrong font is the most-cited counterfeit tell in the owner's catalogue, and
    the one an LLM cannot judge - a vision encoder discards glyph geometry before
    it reasons. So the measurement is made locally in Python and arrives here as
    a number; this rule only decides what the number means.

    The statistic is scatter, not distance. Two genuine labels sat +2.4% and
    -6.0% from San Francisco purely from image scale and ink spread, and -6.0%
    is indistinguishable from Arial. But typefaces differ from each other
    non-uniformly, so a wrong font makes per-glyph deviations DISAGREE while
    genuine print makes them agree at any scale.

    Resolution decides whether any of that is worth saying. Measured across
    four typefaces degraded the way a photograph degrades them, separation is
    real from about 45px of glyph height and INVERTED at 23px, where a
    substitute scores better than San Francisco. A whole-box-underside photo
    puts this print at 28-35px. So the rule's first job is to say whether the
    photograph resolves letterform at all, and to ask for the one that would.
    """
    if local is not None and not local.font_letterforms_resolved:
        height = local.median_glyph_height_px or 0
        if height > 0:
            return RuleOutcome(
                EvidenceStatus.UNKNOWN,
                f"The label's letterforms are {height:.0f} pixels tall in these images, and "
                f"separating Apple's typeface from a substitute needs about "
                f"{MEASURABLE_GLYPH_HEIGHT_PX}. Below that the measurement is not merely weak: "
                f"a substitute typeface can score better than the real one. Fill the frame with "
                f"the printed label, straight on, and this check becomes worth running.",
            )
    if local is None or local.font_scatter_percent is None:
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            "The label's letterforms were not measured. This check needs a close, straight-on "
            "photograph of the printed label block.",
        )

    scatter = local.font_scatter_percent
    shape = local.font_shape_distance
    if shape is not None:
        # Above the gate the shape descriptor is the better statistic: it
        # separated San Francisco from Helvetica, Arial and Verdana by 1.8x to
        # 3.8x where the bounding-box ratio was erratic. Still reported rather
        # than voted, because no counterfeit label has yet been photographed
        # above the gate - there is nothing to calibrate a threshold against.
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"The label's letterforms sit {shape:.3f} from San Francisco across "
            f"{local.font_glyphs_measured} glyphs, measured at "
            f"{local.median_glyph_height_px:.0f} pixels - high enough to resolve letterform. "
            f"Reported, not scored: no counterfeit label has been photographed this close, so "
            f"there is no measured figure to compare it against yet.",
        )
    if scatter >= FONT_SCATTER_THRESHOLD:
        # UNKNOWN, not FAIL. The measurement is real but uncalibrated: genuine
        # labels reach 3.0% at the resolution phone photos actually produce, so
        # this cannot yet distinguish a substitute typeface from a genuine label
        # shot slightly worse.
        return RuleOutcome(
            EvidenceStatus.UNKNOWN,
            f"The label's letterforms deviate from San Francisco by {scatter:.1f}% across "
            f"{local.font_glyphs_measured} glyphs. Reported for information only: genuine labels "
            f"have measured up to 3.0% at this resolution, so this figure cannot yet separate a "
            f"substitute typeface from a genuine label photographed slightly worse.",
        )
    return RuleOutcome(
        EvidenceStatus.PASS,
        f"The label's letterforms are consistent with San Francisco, measured across "
        f"{local.font_glyphs_measured} glyphs (shape deviations agree to within {scatter:.1f}%). "
        f"This compares letterform geometry against Apple's own typeface, not appearance.",
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
        source="Apple, 'Identify your AirPods' (support.apple.com)",
        answerable_from=("box_bottom_label", "case_lid_underside", "earbud"),
    ),
    Rule(
        tier=2,
        parameter="Packaging Seal Type",
        weight=3.0,
        disqualifying=True,
        evaluate=_rule_packaging_seal,
        source="r/AreMyAirpodsAuthentic community guide, Step 1 (ruled authoritative by owner)",
        answerable_from=("box_front", "box_back", "box_side", "box_top"),
    ),
    Rule(
        tier=2,
        parameter="Copyright Year Constraint",
        weight=2.0,
        disqualifying=True,
        evaluate=_rule_copyright_year,
        source="Apple 'Identify your AirPods' (introduction years); owner (2025 ceiling)",
        answerable_from=("box_back", "box_bottom_label"),
    ),
    Rule(
        tier=2,
        parameter="Serial Number Format",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_serial_format,
        source=(
            "Observed on 4 genuine units, 7 corpus listings and 2 counterfeits: 10 characters "
            "throughout, except AirPods Pro 1 at 12. Well-formedness only - both counterfeits "
            "used 10 characters too, so length does not discriminate"
        ),
        answerable_from=("box_bottom_label", "case_lid_underside", "earbud"),
    ),
    Rule(
        # Tier 2, not Tier 1, despite being the strongest check here. An
        # unresolved Tier 1 rule blocks a GENUINE verdict outright, and on a
        # SEALED box the case lid and earbud serials are physically unreachable
        # - so at Tier 1 this rule would make every sealed unit permanently
        # unverifiable. A FAIL still disqualifies; only the absence is tolerated.
        tier=2,
        parameter="Serial Cross-Match",
        weight=4.0,
        disqualifying=True,
        evaluate=_rule_serial_cross_match,
        source=(
            "r/AreMyAirpodsAuthentic community guide, Step 2 (four serial locations); "
            "Apple 'Identify your AirPods' (serial is on the underside of the case lid)"
        ),
        answerable_from=("box_bottom_label", "case_lid_underside", "earbud"),
        # A cross-check needs BOTH surfaces. Holding only the box label cannot
        # answer it, and saying "re-shoot what you sent" when the case was never
        # opened is useless advice - the case lid is the single most valuable
        # photograph a buyer can take, since it is what a cloned serial cannot
        # survive.
        requires_panels=("box_bottom_label", "case_lid_underside"),
    ),
    Rule(
        tier=2,
        parameter="Serial Label Format",
        weight=2.5,
        disqualifying=True,
        evaluate=_rule_serial_label_spacing,
        source="Project owner (domain expert); corroborated by community-guide label photographs",
        answerable_from=("box_bottom_label",),
    ),
    Rule(
        tier=2,
        parameter="Label Typography",
        # Weight 0 and informational: this REPORTS a measurement, it does not
        # vote. Four genuine labels span 0.69%-3.0% scatter, and the only
        # substitute figures available (3.3%-7.9%) come from synthetic renders
        # at a different scale, so the populations are not comparable and the
        # 2.0% threshold would flag genuine boxes. Calibrating it needs
        # counterfeit labels photographed at the 30-40px resolution real phone
        # photos actually produce - which the owner does not have.
        weight=0.0,
        # NOT disqualifying yet. A genuine AirPods Pro 1 label measured 2.25% to
        # 5.52% depending only on where the crop edge fell, well past the 2.0%
        # threshold - so on today's evidence a disqualifying font rule would
        # accuse real boxes. The baseline is two genuine labels; it needs more,
        # and it needs crop contamination detected, before this can convict on
        # its own. Same discipline that demoted kerning and shoddy-wrap after
        # each produced a false positive on first contact with real data.
        disqualifying=False,
        evaluate=_rule_label_typography,
        source=(
            "Measured against San Francisco (Apple's packaging typeface, shipped with macOS). "
            "NOT CALIBRATED - reported as an observation, never as a finding"
        ),
        answerable_from=("box_bottom_label",),
        informational=True,
    ),
    Rule(
        tier=2,
        parameter="Typography & Print Quality",
        weight=1.5,
        disqualifying=False,
        evaluate=_rule_typography,
        source="Community guide (printed misspellings); corpus measurement (kerning excluded)",
        answerable_from=("box_back", "box_front", "box_bottom_label"),
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


def _apply(
    rule: Rule, extraction: VisionExtraction, variant: str, local: LocalAnalysis | None
) -> RuleOutcome:
    """Call a rule with the arguments it declares.

    Most rules are pure functions of the extraction; only the ones needing
    locally computed measurements take a third parameter. Deciding by signature
    rather than by catching TypeError matters - catching it would swallow a
    genuine TypeError raised inside a rule and silently rerun it with fewer
    arguments.
    """
    if len(inspect.signature(rule.evaluate).parameters) >= 3:
        return rule.evaluate(extraction, variant, local)
    return rule.evaluate(extraction, variant)


def evaluate(
    extraction: VisionExtraction, local: LocalAnalysis | None = None
) -> VerificationReport:
    """Run every rule and fold the outcomes into a verdict + Evidence Ledger."""
    variant = resolve_variant(extraction)

    shown = set(extraction.visible_panels)
    unanswerable: list[Rule] = []
    unreadable: list[Rule] = []

    ledger: list[EvidenceItem] = []
    evaluated_weight = 0.0
    disqualifying_failures = 0
    soft_failures = 0
    tier1_unresolved = False

    for rule in RULES:
        outcome = _apply(rule, extraction, variant, local)
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
            # Two very different UNKNOWNs: the panel that answers this rule was
            # never photographed, or it was and the value still could not be
            # read. Only the first is fixed by taking another photo.
            missing_required = set(rule.requires_panels) - shown
            if missing_required:
                unanswerable.append(rule)
            elif rule.answerable_from and not (shown & set(rule.answerable_from)):
                unanswerable.append(rule)
            elif shown:
                unreadable.append(rule)
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
        next_steps=_next_steps(unanswerable, unreadable, shown),
    )


def _still_blocked(rule: Rule, covered: set[str]) -> bool:
    """True while a rule is still missing a photograph it needs.

    A rule requiring SEVERAL panels stays queued until every one is covered -
    dropping it after the first meant the case lid was never requested, because
    the box label alone was treated as resolving a cross-check that needs both
    surfaces.
    """
    if set(rule.requires_panels) - covered:
        return True
    if rule.requires_panels:
        return False
    return bool(rule.answerable_from) and not (covered & set(rule.answerable_from))


def _next_steps(
    unanswerable: list[Rule], unreadable: list[Rule], shown: set[str]
) -> list[str]:
    """Turn open checks into the fewest photographs that would resolve them.

    Greedy set cover: repeatedly pick the panel that unblocks the most remaining
    checks. Listing every panel that *could* answer a rule produces four ways to
    say "photograph the box", which is noise - a buyer standing in front of a
    seller needs a short list, in order.

    Coverage is deliberately NOT rescoped to the panels provided. A unit
    photographed only from the case would otherwise reach high coverage and a
    GENUINE verdict without anyone having seen the box - missing panels are
    missing evidence, not irrelevant evidence.
    """
    steps: list[str] = []
    remaining = list(unanswerable)
    covered: set[str] = set(shown)

    while remaining:
        # Which unshown panel would unblock the most outstanding checks?
        tally: dict[str, list[Rule]] = {}
        for rule in remaining:
            for panel in tuple(rule.requires_panels) + tuple(rule.answerable_from):
                if panel not in covered and panel in PANEL_REQUESTS:
                    tally.setdefault(panel, []).append(rule)
        if not tally:
            break

        def rank(panel: str) -> tuple[float, int]:
            """Rank by how much EVIDENCE a photograph unlocks, not how many
            checks it happens to touch.

            Counting checks undervalues the case lid: it unblocks one rule, but
            that rule is the serial cross-match, the only check a cloned serial
            cannot survive. Weight is the engine's own statement of how much
            each check is worth, so the advice should use it.
            """
            preference = (
                len(PANEL_PRIORITY) - PANEL_PRIORITY.index(panel)
                if panel in PANEL_PRIORITY
                else 0
            )
            return (sum(rule.weight for rule in tally[panel]), preference)

        best = max(tally, key=rank)
        resolved = sorted({rule.parameter for rule in tally[best]})
        steps.append(f"{PANEL_REQUESTS[best]} (resolves: {', '.join(resolved)})")
        covered.add(best)
        # A rule needing SEVERAL panels stays in the queue until every one is
        # covered. Dropping it after the first satisfied panel meant the case
        # lid was never requested - the box label alone was treated as having
        # resolved a cross-check that needs both surfaces.
        remaining = [rule for rule in remaining if _still_blocked(rule, covered)]

    if unreadable:
        # One instruction, not one per rule. The advice is identical for every
        # unreadable check, and repeating it three times buries the requests
        # that actually differ.
        names = ", ".join(sorted(rule.parameter for rule in unreadable))
        steps.append(
            f"Re-shoot the surfaces you already photographed in even light, filling the frame "
            f"with the fine print - the text was not legible enough to check {names}."
        )

    return steps


def evaluate_dict(payload: dict) -> dict:
    """Convenience wrapper: raw extraction dict in, Risk Output Schema dict out."""
    return evaluate(VisionExtraction.model_validate(payload)).to_dict()
