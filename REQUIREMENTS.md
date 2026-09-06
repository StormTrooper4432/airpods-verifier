# REQUIREMENTS.md - AirPods Pro 2 Ground Truth Specifications

## Hardware Matrix

### 1. Model Numbers (Tier 1 Rule)
- USB-C Variant:
  - Left Earbud: A3047
  - Right Earbud: A3048
  - Charging Case: A3049 or A2968
- Lightning Variant:
  - Left Earbud: A2931
  - Right Earbud: A2699
  - Charging Case: A2700

### 2. Packaging & Visual Signatures (Tier 2 Rule)
- Shrink Wrap: Genuine AirPods Pro 2 NEVER use full plastic shrink wrap. They use paper pull tabs with green arrows on the top and bottom.
  - has_plastic_shrinkwrap == True -> HIGH RISK / COUNTERFEIT
- Copyright Year Constraints:
  - Lightning Variant: Released 2022. Copyright must be >= 2022.
  - USB-C Variant: Released late 2023. Copyright must be >= 2023.

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
      "detail": "Extracted model A3047 matches Pro 2 USB-C Left Earbud."
    }
  ]
}