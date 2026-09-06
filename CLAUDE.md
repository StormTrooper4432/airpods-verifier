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

## Directory Structure Strategy
```text
airpods-verifier-poc/
├── CLAUDE.md
├── REQUIREMENTS.md
├── PROMPTS.md
├── schema.py
├── rules_engine.py
├── vision_extractor.py
├── main.py
└── tests/
    ├── test_rules.py
    └── sample_data/