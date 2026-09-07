"""The web sheet must not invent a specification either.

`web/src/data/sheet.ts` restates the rule table so the browser can render the
register, the coverage rail and the plot without a round trip. That copy is
exactly the kind of thing that silently rots: a weight changes in Python, the
website keeps printing the old one, and a buyer reads a number no rule
produces. These tests fail the moment the two drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import rules_engine
from schema import PanelName
from typing import get_args

WEB = Path(__file__).resolve().parent.parent / "web"
SHEET_TS = WEB / "src" / "data" / "sheet.ts"
SPECIMENS = WEB / "src" / "data" / "specimens.json"

pytestmark = pytest.mark.skipif(not SHEET_TS.exists(), reason="web frontend not present")


@pytest.fixture(scope="module")
def sheet_source() -> str:
    return SHEET_TS.read_text(encoding="utf-8")


def _ts_rule_blocks(source: str) -> list[str]:
    """Split the RULES array into one text block per rule."""
    start = source.index("export const RULES: Rule[] = [")
    end = source.index("\n];", start)
    body = source[start:end]
    # Each rule opens at a line that is exactly four spaces then "{".
    blocks = re.split(r"\n  \{\n", body)[1:]
    return blocks


def _field(block: str, name: str) -> str | None:
    match = re.search(rf"^    {name}: (.+?),?$", block, re.M)
    return match.group(1).rstrip(",") if match else None


def test_every_rule_is_present_in_the_same_order(sheet_source: str) -> None:
    ts_names = [
        re.search(r"parameter: '([^']+)'", block).group(1)  # type: ignore[union-attr]
        for block in _ts_rule_blocks(sheet_source)
    ]
    assert ts_names == [rule.parameter for rule in rules_engine.RULES]


def test_weights_tiers_and_effects_match(sheet_source: str) -> None:
    blocks = {
        re.search(r"parameter: '([^']+)'", b).group(1): b  # type: ignore[union-attr]
        for b in _ts_rule_blocks(sheet_source)
    }
    for rule in rules_engine.RULES:
        block = blocks[rule.parameter]
        assert float(_field(block, "weight") or "nan") == rule.weight, rule.parameter
        assert int(_field(block, "tier") or "-1") == rule.tier, rule.parameter
        assert (_field(block, "disqualifying") == "true") is rule.disqualifying, rule.parameter
        informational = _field(block, "informational") == "true"
        assert informational is rule.informational, rule.parameter


def test_total_weight_and_coverage_floor_match(sheet_source: str) -> None:
    total = sum(rule.weight for rule in rules_engine.RULES)
    # TOTAL_WEIGHT is computed in TS; the floor is a literal that can drift.
    floor = re.search(r"MIN_COVERAGE_FOR_VERDICT = ([\d.]+)", sheet_source)
    assert floor is not None
    assert float(floor.group(1)) == rules_engine.MIN_COVERAGE_FOR_VERDICT
    assert total == pytest.approx(19.5), "rule weights changed; check the sheet's rail"


def test_panel_bindings_match(sheet_source: str) -> None:
    blocks = {
        re.search(r"parameter: '([^']+)'", b).group(1): b  # type: ignore[union-attr]
        for b in _ts_rule_blocks(sheet_source)
    }
    for rule in rules_engine.RULES:
        block = blocks[rule.parameter]
        raw = _field(block, "answerableFrom") or "[]"
        ts_panels = re.findall(r"'([a-z_]+)'", raw)
        assert tuple(ts_panels) == tuple(rule.answerable_from), rule.parameter

        raw_required = _field(block, "requiresPanels")
        ts_required = re.findall(r"'([a-z_]+)'", raw_required or "")
        assert tuple(ts_required) == tuple(rule.requires_panels), rule.parameter


def test_every_web_rule_still_cites_a_source(sheet_source: str) -> None:
    for block in _ts_rule_blocks(sheet_source):
        parameter = re.search(r"parameter: '([^']+)'", block).group(1)  # type: ignore[union-attr]
        assert "source:" in block, f"{parameter} ships to the browser with no source"
        source_text = block.split("source:", 1)[1]
        assert re.search(r"['\"][^'\"]{10,}", source_text), f"{parameter} has an empty source"


def test_panel_vocabulary_matches_the_schema(sheet_source: str) -> None:
    union = re.search(r"export type PanelId =(.+?);", sheet_source, re.S)
    assert union is not None, "PanelId union not found"
    ts_ids = set(re.findall(r"'([a-z_]+)'", union.group(1)))
    assert ts_ids == set(get_args(PanelName))


def test_plotted_panels_cover_every_drawable_surface(sheet_source: str) -> None:
    """`other` is a bucket, not a surface: everything else must be on the plot."""
    drawn = set(re.findall(r"^    id: '([a-z_]+)',$", sheet_source, re.M))
    assert drawn == set(get_args(PanelName)) - {"other"}


# --------------------------------------------------------------------------- #
# The published specimens
# --------------------------------------------------------------------------- #

SERIAL_SHAPED = re.compile(r"\b(?![A-Z]{10}\b)[A-Z0-9]{10}\b")


def test_specimens_carry_no_readable_serial() -> None:
    """These are strangers' photographs. Nothing ships a whole serial."""
    if not SPECIMENS.exists():
        pytest.skip("no specimens filed")
    blob = SPECIMENS.read_text(encoding="utf-8")
    leaked = [
        token
        for token in SERIAL_SHAPED.findall(blob)
        if any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)
    ]
    assert not leaked, f"unredacted serial-shaped tokens in the published specimens: {leaked}"


def test_specimens_are_real_engine_output() -> None:
    if not SPECIMENS.exists():
        pytest.skip("no specimens filed")
    specimens = json.loads(SPECIMENS.read_text(encoding="utf-8"))
    assert specimens, "the filed records are empty"
    parameters = [rule.parameter for rule in rules_engine.RULES]
    verdicts = set()
    for specimen in specimens:
        report = specimen["report"]
        assert [item["parameter"] for item in report["evidence_ledger"]] == parameters
        for item in report["evidence_ledger"]:
            assert item["source"].strip(), f"{item['parameter']} filed with no source"
            assert item["status"] in {"PASS", "FAIL", "UNKNOWN"}
        verdicts.add(report["summary_verdict"])
    assert verdicts == {"GENUINE", "HIGH_RISK_SUSPECTED", "INCONCLUSIVE"}, (
        "the filed records should show all three outcomes, so INCONCLUSIVE is "
        "visibly a first-class result rather than a failure state"
    )


def test_no_authenticity_percentage_reaches_the_page() -> None:
    """The pipeline produces no such number; the sheet must not imply one."""
    page = (WEB / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
    for banned in ("% authentic", "authenticity score", "% genuine", "% real"):
        assert banned.lower() not in page.lower(), f"page implies a {banned!r} figure"
