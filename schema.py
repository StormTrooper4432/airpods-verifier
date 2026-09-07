"""Pydantic schemas for the AirPods Pro 2 authenticator.

Two families of models live here:

1. ``VisionExtraction`` - the *contract* the Vision LLM must fill. This is the
   only structure the LLM is allowed to produce. It contains observations, never
   judgements: the model reports what it can see, and Python decides what that
   means. Its JSON Schema is sent to the API via structured outputs, so every
   field is required (nullable where a value may legitimately be unreadable) and
   ``extra="forbid"`` renders ``additionalProperties: false``.

2. ``VerificationReport`` / ``EvidenceItem`` - the Evidence Ledger emitted by the
   deterministic rules engine. ``model_dump(mode="json")`` produces exactly the
   Risk Output Schema documented in REQUIREMENTS.md.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# Variant identifiers
# --------------------------------------------------------------------------- #

VARIANT_USB_C = "Pro 2 USB-C"
VARIANT_LIGHTNING = "Pro 2 Lightning"
VARIANT_UNKNOWN = "Unknown"

#: Literal (not Enum) so the generated JSON Schema inlines the enum values
#: instead of emitting a ``$ref``/``$defs`` pair -- the flattest, most portable
#: shape for the strict structured-outputs schema validator.
ClaimedVariant = Literal["Pro 2 USB-C", "Pro 2 Lightning", "Unknown"]

#: Surfaces a photograph can show. Panel identification is recorded as an
#: observation only - it does not drive any rule until its accuracy has been
#: measured against a labelled corpus. A confidently wrong panel ID feeding a
#: rule would manufacture exactly the false positives the corpus removed.
PanelName = Literal[
    "box_front",
    "box_back",
    "box_bottom_label",
    "box_side",
    "box_top",
    "case_exterior",
    "case_lid_underside",
    "earbud",
    "accessories",
    "other",
]

#: Strings the vision model tends to emit when a field is simply not legible.
_NULLISH = {"", "null", "none", "n/a", "na", "unknown", "not visible", "unreadable"}

_MODEL_NUMBER_RE = re.compile(r"\bA\d{4}\b")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _clean_optional_text(value: Any) -> Any:
    """Normalise the many ways an LLM says 'I could not read this'."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in _NULLISH:
            return None
        return stripped
    return value


# --------------------------------------------------------------------------- #
# Stage 1: Vision extraction contract
# --------------------------------------------------------------------------- #


class VisionExtraction(BaseModel):
    """Structured observations extracted from photographs by the Vision LLM.

    Field order matches PROMPTS.md so the prompt and the schema stay readable
    side by side.
    """

    model_config = ConfigDict(extra="forbid")

    claimed_model_variant: ClaimedVariant = Field(
        description=(
            "Which AirPods Pro 2 variant the packaging or hardware claims to be. "
            "Use 'Unknown' if the images do not make this determinable."
        )
    )
    extracted_model_number: str | None = Field(
        description="Apple A-number printed on the box or hardware, e.g. 'A3047'. Null if not legible."
    )
    extracted_serial_number: str | None = Field(
        description="Serial number printed on the box or hardware. Null if not legible."
    )
    serial_label_literal: str | None = Field(
        description=(
            "The serial line from the box label copied EXACTLY as printed, including brackets, "
            "punctuation and spacing - for example '(S) Serial No. WXLP2LT40J'. Reproduce the "
            "spacing faithfully; do not tidy it up. Null if the serial line is not legible."
        )
    )
    copyright_year: int | None = Field(
        description="Four-digit copyright year printed on the packaging. Null if not legible."
    )
    has_plastic_shrinkwrap: bool = Field(
        description="True if full plastic shrink wrap is visible around the box."
    )
    has_paper_pull_tabs: bool = Field(
        description="True if paper pull-tabs with green arrows are visible on the box seams."
    )
    shrinkwrap_has_pull_tab: bool | None = Field(
        description=(
            "Only meaningful when has_plastic_shrinkwrap is true: whether the plastic wrap has "
            "a pull tab / tear strip at the side. Null when the box is not wrapped, or when the "
            "sides are not visible."
        )
    )
    shrinkwrap_looks_shoddy: bool | None = Field(
        description=(
            "Only meaningful when has_plastic_shrinkwrap is true: whether the wrap is loose, "
            "wrinkled, yellowed, or has bulky seams rather than being tight with minimal seams. "
            "Null when the box is not wrapped."
        )
    )
    visible_panels: list[PanelName] = Field(
        description=(
            "Which surfaces of the product the images actually show. Empty list if none can be "
            "identified. Report only what is clearly identifiable."
        )
    )
    font_kerning_irregularities: bool = Field(
        description="True if letter spacing, weight, or alignment deviates from Apple's print standards."
    )
    visible_spelling_errors: list[str] = Field(
        description="Misspelled words found in printed text. Empty list if none."
    )

    @field_validator("extracted_model_number", mode="before")
    @classmethod
    def _normalise_model_number(cls, value: Any) -> Any:
        """Pull the bare A-number out of noise like 'Model A3047' or 'a3047.'."""
        value = _clean_optional_text(value)
        if not isinstance(value, str):
            return value
        match = _MODEL_NUMBER_RE.search(value.upper())
        # No match -> hand the raw text through so the engine can fail it loudly
        # rather than silently discarding a suspicious string.
        return match.group(0) if match else value.upper()

    @field_validator("extracted_serial_number", mode="before")
    @classmethod
    def _normalise_serial(cls, value: Any) -> Any:
        value = _clean_optional_text(value)
        if not isinstance(value, str):
            return value
        return re.sub(r"[\s\-]", "", value).upper()

    @field_validator("copyright_year", mode="before")
    @classmethod
    def _normalise_year(cls, value: Any) -> Any:
        """Accept 2022, '2022', '© 2022', 'Copyright 2022 Apple Inc.'."""
        value = _clean_optional_text(value)
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, str):
            match = _YEAR_RE.search(value)
            return int(match.group(0)) if match else None
        return value

    @field_validator("visible_spelling_errors", mode="before")
    @classmethod
    def _normalise_spelling_errors(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = _clean_optional_text(value)
            return [cleaned] if cleaned else []
        return value


# --------------------------------------------------------------------------- #
# Stage 2: Evidence Ledger output
# --------------------------------------------------------------------------- #


class EvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class Verdict(str, Enum):
    GENUINE = "GENUINE"
    HIGH_RISK_SUSPECTED = "HIGH_RISK_SUSPECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceItem(BaseModel):
    """One itemised, human-readable line of the Evidence Ledger.

    Deliberately limited to the four fields in the REQUIREMENTS.md output
    schema; rule weighting metadata stays internal to the rules engine.
    """

    tier: Annotated[int, Field(ge=1, le=2)]
    parameter: str
    status: EvidenceStatus
    detail: str
    #: Provenance of the rule's factual claim. Shown to the buyer so any line in
    #: the ledger can be independently checked.
    source: str


class VerificationReport(BaseModel):
    summary_verdict: Verdict
    confidence_score: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_ledger: list[EvidenceItem]
    #: Specific photographs that would resolve the open checks, derived from
    #: which panels were actually shown. An UNKNOWN a buyer can act on is worth
    #: far more than one they cannot.
    next_steps: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-safe dict matching the documented Risk Output Schema."""
        return self.model_dump(mode="json")
