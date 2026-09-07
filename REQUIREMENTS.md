# REQUIREMENTS.md - AirPods Pro 2 Ground Truth Specifications

## Hardware Matrix

### 1. Model Numbers (Tier 1 Rule)

**Ground truth now lives in `knowledge/airpods_pro_2.yaml`**, where every entry carries a
`source`. This section is a summary; the YAML is authoritative.

- USB-C Variant (introduced 2023): A3047, A3048, A3049; charging case A2968
- Lightning Variant (introduced 2022): A2931, A2699, A2698; charging case A2700

Source: Apple, "Identify your AirPods". Apple lists the three set numbers together without
stating which is the left earbud, the right earbud, or the case, so the component mapping is
recorded as unspecified rather than guessed. A2698 was missing from the original version of
this document.

### 2. Packaging & Visual Signatures (Tier 2 Rule)
- Shrink Wrap: **Being wrapped is NOT evidence of a counterfeit.** Most genuine Pro 2 units are
  unwrapped and sealed with paper pull-tabs bearing green arrows, but genuine sealed retail stock
  does sometimes ship shrink-wrapped. The discriminator is the wrap itself:
  - wrapped AND no side pull tab -> FAIL
  - wrapped AND loose / wrinkled / yellowed / heavily seamed -> FAIL
  - wrapped AND side pull tab AND tight, minimal seams -> PASS
  - wrapped but sides not visible -> UNKNOWN
  - Source: r/AreMyAirpodsAuthentic community guide, ruled authoritative by the project owner.
    This supersedes the earlier "wrapped means counterfeit" rule, which would have accused
    genuine sealed units.
- Copyright Year Constraints (a range, not just a floor):
  - Lightning Variant: 2022 <= year <= 2025
  - USB-C Variant: 2023 <= year <= 2025
  - Source: Apple "Identify your AirPods" for the introduction years; project owner for the
    2025 observed ceiling. Apple reprints boxes over a product's life, so the ceiling is an
    observed maximum rather than a release date.

---

## Risk Output Schema
The pipeline output must conform to this structure:
{
  "summary_verdict": "GENUINE | HIGH_RISK_SUSPECTED | INCONCLUSIVE",
  "confidence_score": 0.0,
  "evidence_ledger": [
    {
      "tier": 1,
      "parameter": "Model Number Compatibility",
      "status": "PASS | FAIL | UNKNOWN",
      "detail": "Extracted model A3047 matches Pro 2 USB-C part.",
      "source": "Apple, 'Identify your AirPods' (support.apple.com)"
    }
  ],
  "next_steps": [
    "Photograph the sticker on the underside of the box - it carries the part number, the model numbers and the serial. (resolves: Model Number Compatibility, Serial Number Format)"
  ]
}

`source` is rendered under any FAILING check, so a buyer can trace every
accusation. `next_steps` is derived from which panels were actually photographed
and is chosen by greedy set cover, so it asks for the fewest photographs that
would resolve the open checks.

---

## Note on this document

`knowledge/airpods_pro_2.yaml` is authoritative. This file is a human-readable
summary and will drift. The YAML carries per-entry sources, the full AirPods
product family for identification, regional variants, and the measurement
records behind every threshold.