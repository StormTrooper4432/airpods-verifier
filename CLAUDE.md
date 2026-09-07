# CLAUDE.md - AirPods Pro 2 Authenticator PoC

## Project Context
You are building an end-to-end Python ML & Rules Engine pipeline to authenticate **AirPods Pro 2 (Lightning & USB-C)** boxes and hardware using photo evidence. 

The architecture is **hybrid**:
1. **Vision AI Extraction (LLM)**: Parses unstructured image data into structured JSON (OCR, physical traits, typography checks).
2. **Deterministic Rules Engine**: Validates extracted JSON against Apple's strict hardware matrix to yield a zero-shot, explainable **Evidence Ledger**.

---

## Technical Stack & Constraints
- **Language**: Python 3.10+
- **Environment**: Virtual environment (`.venv`)
- **Key Libraries**: `pydantic` (schema validation), `anthropic` (Claude Vision API execution), `pytest` (test suite).
- **Core Principle**: Do NOT use LLMs to guess the authenticity score directly. LLMs ONLY perform extraction into structured schemas; Python code evaluates constraints.

---

## Directory Structure
```text
airpods-verifier/
├── CLAUDE.md              project brief (this file)
├── REQUIREMENTS.md        ground-truth summary; knowledge/ is authoritative
├── PROMPTS.md             the vision system prompt, loaded at runtime
├── README.md              what the project is, and what it cannot do
├── schema.py              Pydantic contracts: LLM output + Evidence Ledger
├── rules_engine.py        rules, verdict aggregation, panel-aware next steps
├── knowledge/
│   └── airpods_pro_2.yaml ground truth - EVERY entry carries a `source`
├── providers.py           Gemini and Claude behind one protocol
├── vision_extractor.py    prompt loading, image prep, local pixel analysis
├── font_forensics.py      geometric typeface comparison (informational only)
├── image_quality.py       sharpness measurement
├── main.py                CLI and ledger rendering
├── tools/
│   ├── run_corpus.py      batch scoring + blind-label confusion matrix
│   ├── check_models.py    probe which provider models actually serve requests
│   └── fetch_reddit.py    corpus collection (needs Reddit OAuth approval)
├── web/                   Astro front end - the examination record
│   ├── src/pages/index.astro       the surface; POST runs the real pipeline
│   ├── src/lib/examine.ts          shells out to main.py, never decides
│   ├── src/lib/redact.ts           serial masking, at the report boundary
│   ├── src/lib/guilloche.ts        generated engine-turned ornament
│   ├── src/data/sheet.ts           rule table mirrored for the browser
│   └── src/data/specimens.json     three real corpus records, serials masked
└── tests/
    ├── test_rules.py, test_extractor.py, test_font_forensics.py, test_packaging.py
    ├── test_web_contract.py        fails when the web copy drifts from Python
    └── sample_data/       synthetic fixtures + the labelled photo corpus
```

The web layer is a **renderer, not a second engine**. It shells out to
`main.py` and prints what comes back; the browser never evaluates a rule. See
`DESIGN.md` for the visual system and `.impeccable/surfaces/` for the surface
brief.

---

## Rules Learned the Hard Way

These were paid for with false positives on real units. Read before adding a rule.

1. **Every rule needs a cited source.** Enforced by tests. A rule without
   provenance is an invented specification - "a polished counterfeit detector
   containing counterfeit information". Precedence: Apple's published pages >
   the project owner (domain expert) > community guide.

2. **Never assert an Apple specification from memory.** This includes model
   IDs and provider model availability: `gemini-2.5-flash` appears in Google's
   documented free-tier list and returns 404 for new accounts.

3. **Subjective visual judgements have produced a false positive every single
   time they were trusted** - kerning irregularity, "shoddy" wrap. Objective
   printed features have held up - misspellings, `(S) Serial` spacing, model
   numbers.

4. **Absence of evidence is never evidence of authenticity.** An unreadable
   Tier 1 check forces INCONCLUSIVE. Coverage is deliberately NOT rescoped to
   the panels provided, or a case-only photoset would clear a unit nobody
   photographed the box of.

5. **Measure before believing.** Synthetic tests said font comparison separated
   genuine from counterfeit by 10x; photographs said the variation within
   genuine units exceeds the gap to a counterfeit. The rule ships informational
   with zero weight.

6. **Anything the sheet prints is masked, not trusted to be clean.** The web
   sheet once printed a stranger's whole serial in a finding while the filed
   records 400px below it were masked - two opposite standards on one page,
   caught in review, not by a test. Masking now happens at the one
   boundary every report crosses (`web/src/lib/redact.ts`), so a rule added later
   cannot leak one by wording its detail differently. A per-rule fix would
   have rotted on the next rule.

7. **A test that checks a copy of the rule proves nothing.**
   `test_web_contract.py` extracts the shipped regex out of `redact.ts` and
   runs cases through node, rather than re-implementing the pattern in Python
   where the two could drift apart silently.

8. **Reference photographs never leave the machine.** `knowledge/real_*` and
   `knowledge/fake` are blocked from every vision API in code, not by
   convention - they carry real serials, and free-tier terms allow providers to
   retain submitted content.
