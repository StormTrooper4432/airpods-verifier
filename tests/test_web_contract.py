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
import shutil
import subprocess
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


# --------------------------------------------------------------------------- #
# The live record
# --------------------------------------------------------------------------- #

REDACT_TS = WEB / "src" / "lib" / "redact.ts"
EXAMINE_TS = WEB / "src" / "lib" / "examine.ts"


def test_live_reports_are_masked_at_the_boundary() -> None:
    """A serial belongs to whoever owns the box, not to whoever photographed it.

    The filed records were masked when their JSON was authored. That left the
    live record - a stranger's box, by the surface's own framing - printing the
    serial in full on the same page. Masking has to sit where every report
    crosses, so a rule added later cannot leak one by wording its detail
    differently.
    """
    source = EXAMINE_TS.read_text(encoding="utf-8")
    assert "redactReport" in source, "examine() does not mask the report it returns"
    parse_line = next(
        line for line in source.splitlines() if "JSON.parse(stdout)" in line
    )
    assert "redactReport" in parse_line, (
        "the parsed report reaches a caller before it is masked: "
        f"{parse_line.strip()!r}"
    )


def test_the_shipped_masking_pattern_hits_serials_and_nothing_else() -> None:
    """Exercise the real regex out of redact.ts, not a copy of it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")

    pattern = re.search(r"^const SERIAL = (/.+/g);$", REDACT_TS.read_text(encoding="utf-8"), re.M)
    assert pattern is not None, "redact.ts no longer declares SERIAL as a literal"

    cases = {
        # masked: Apple serials, 10 characters and the Pro 1's 12
        "Serial KY2622QC6F is well-formed": True,
        "'(S) Serial No. DKM4QF3FPT'": True,
        "Serial GX2K9L0PQRST here": True,
        # untouched: everything else the ledger prints
        "Extracted model A2698 matches Pro 2 Lightning part.": False,
        "Part number MTJV3AM/A": False,
        "Copyright year 2024 is consistent with Pro 2 USB-C": False,
        "UPC 194253340003 on the label": False,
        "HIGH_RISK_SUSPECTED and INCONCLUSIVE": False,
        "Assembled in Vietnam": False,
    }
    script = (
        f"const SERIAL = {pattern.group(1)};\n"
        "const mask = t => t.slice(0,4) + '.'.repeat(t.length-6) + t.slice(-2);\n"
        "const cases = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(cases.map(c => c.replace(SERIAL, mask) !== c)));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script, json.dumps(list(cases))],
        capture_output=True,
        text=True,
        check=True,
    )
    changed = json.loads(proc.stdout)
    for (text, should_mask), was_masked in zip(cases.items(), changed):
        assert was_masked is should_mask, (
            f"{text!r} was {'masked' if was_masked else 'left alone'}, "
            f"but should have been {'masked' if should_mask else 'left alone'}"
        )


def test_nothing_the_sheet_can_print_carries_a_whole_serial() -> None:
    """Both halves of the page held to one standard, not two."""
    for path in (SPECIMENS, WEB / "src" / "pages" / "index.astro"):
        if not path.exists():
            continue
        blob = path.read_text(encoding="utf-8")
        leaked = [
            token
            for token in SERIAL_SHAPED.findall(blob)
            if any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)
        ]
        assert not leaked, f"{path.name} carries serial-shaped tokens: {leaked}"


def test_the_browsers_coverage_forecast_matches_the_engine() -> None:
    """The sheet tells a visitor what their photographs can reach before they
    send them. If that forecast drifts from what the rules engine actually
    does, the sheet promises a verdict the engine will refuse - or talks
    someone out of an examination that would have worked.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")

    scenarios = [
        [],
        ["box_front", "case_exterior", "earbud"],
        ["box_front", "case_exterior", "earbud", "box_bottom_label"],
        ["box_bottom_label", "case_lid_underside"],
        ["box_front", "box_back", "box_side", "box_top"],
        [p for p in get_args(PanelName) if p != "other"],
    ]

    def python_ceiling(panels: list[str]) -> float:
        shown = set(panels)
        total = 0.0
        for rule in rules_engine.RULES:
            if rule.weight == 0:
                continue
            if rule.requires_panels and not set(rule.requires_panels) <= shown:
                continue
            if rule.answerable_from and not (set(rule.answerable_from) & shown):
                continue
            total += rule.weight
        return total

    script = (
        "import { reachableWeight } from './src/data/sheet.ts';\n"
        "const sets = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(sets.map(reachableWeight)));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script, json.dumps(scenarios)],
        capture_output=True,
        text=True,
        cwd=WEB,
    )
    if proc.returncode != 0:
        pytest.skip(f"node could not import the sheet module: {proc.stderr.strip()[:200]}")

    from_browser = json.loads(proc.stdout)
    for panels, browser_value in zip(scenarios, from_browser):
        assert browser_value == pytest.approx(python_ceiling(panels)), (
            f"the sheet forecasts {browser_value} for {panels}, "
            f"the engine reaches {python_ceiling(panels)}"
        )


def test_the_instinctive_photo_set_cannot_reach_a_verdict() -> None:
    """The measurement this warning exists for, kept as a test.

    On the labelled corpus the underside label was photographed in 8 of 20
    posts and the case lid in none. The surfaces people do photograph - box
    front, case, earbuds - top out below the coverage floor, so eleven of
    twenty examinations were unanswerable before the model saw them. If a rule
    change ever makes that set sufficient, this test should fail and the
    warning copy should be revisited.
    """
    instinctive = {"box_front", "case_exterior", "earbud"}
    total = sum(rule.weight for rule in rules_engine.RULES)
    reachable = sum(
        rule.weight
        for rule in rules_engine.RULES
        if rule.weight
        and (not rule.requires_panels or set(rule.requires_panels) <= instinctive)
        and (not rule.answerable_from or set(rule.answerable_from) & instinctive)
    )
    assert reachable / total < rules_engine.MIN_COVERAGE_FOR_VERDICT, (
        "the instinctive photo set now reaches the coverage floor; the sheet's "
        "pre-flight warning is out of date"
    )


def test_the_edge_panel_set_matches_the_engine(sheet_source: str) -> None:
    """The browser must not send someone to a surface the engine won't read.

    `mostValuablePanel` broke ties by array order, which put box_top ahead of
    box_side for the same promised 3.0 — and box_top is precisely the panel the
    engine refuses to read a missing pull tab from, because the extractor has
    labelled an opened box's interior tray box_top. Identical forecast, strictly
    worse photograph. The tiebreak now consults this set, so the set has to stay
    equal to the engine's or the advice silently rots again.
    """
    match = re.search(
        r"export const PANELS_SHOWING_AN_EDGE: PanelId\[\] = \[([^\]]*)\]", sheet_source
    )
    assert match, "sheet.ts no longer exports PANELS_SHOWING_AN_EDGE"
    shipped = {name for name in re.findall(r"'([^']+)'", match.group(1))}
    assert shipped == set(rules_engine._PANELS_SHOWING_AN_EDGE), (
        f"web says {sorted(shipped)}, engine says "
        f"{sorted(rules_engine._PANELS_SHOWING_AN_EDGE)}"
    )


def test_the_tiebreak_actually_prefers_the_decisive_panel(sheet_source: str) -> None:
    """Runs the shipped function rather than re-implementing it in Python.

    The first version of this tiebreak passed the panel OBJECT where the helper
    expected an id, so the check was always false and the ranking was unchanged
    while the code read as though it worked. Only executing it catches that.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")

    script = """
      import { mostValuablePanel } from './src/data/sheet.ts';
      const shown = ['box_bottom_label', 'case_lid_underside'];
      console.log(JSON.stringify(mostValuablePanel(shown)));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=WEB, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    best = json.loads(result.stdout.strip())
    assert best["panel"] == "box_side", (
        f"tie went to {best['panel']}; box_side carries the same weight and is the only "
        "panel that can establish a missing tear strip"
    )


def test_the_plot_badge_and_the_forecast_are_the_same_calculation() -> None:
    """A surface cannot be worth two different numbers on one screen.

    The plot printed each panel's weight by summing every rule that listed it in
    `answerableFrom`, ignoring `requiresPanels` — so Serial Cross-Match, which
    needs the underside label AND the case lid, was credited whole to the label.
    The badge read 16.5 and the forecast a few inches below it read 12.5, for
    the same photograph. The badge now defers to reachableWeight, and this fails
    if anyone reintroduces a second way of counting.
    """
    boxnet = WEB / "src" / "components" / "BoxNet.astro"
    source = boxnet.read_text(encoding="utf-8")

    assert "reachableWeight([panel])" in source, (
        "BoxNet no longer defers to reachableWeight for the panel badge"
    )
    assert not re.search(r"RULES\s*\.filter\([^)]*answerableFrom", source), (
        "BoxNet is summing rule weights itself again instead of asking "
        "reachableWeight; that is how the badge and the forecast drifted apart"
    )


def test_the_underside_label_alone_still_clears_the_verdict_floor() -> None:
    """The sheet tells people one photograph is enough. It has to stay true.

    This is a claim printed to visitors, not an internal invariant. If a weight
    or the floor ever moves so the label alone stops clearing it, the copy
    becomes a lie — so run the shipped calculation rather than a Python
    restatement of it, which could agree with itself while the page disagrees.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")

    script = """
      import { reachableWeight, canReachVerdict, TOTAL_WEIGHT, MIN_COVERAGE_FOR_VERDICT }
        from './src/data/sheet.ts';
      console.log(JSON.stringify({
        label: reachableWeight(['box_bottom_label']),
        ok: canReachVerdict(['box_bottom_label']),
        floor: TOTAL_WEIGHT * MIN_COVERAGE_FOR_VERDICT,
        instinct: reachableWeight(['box_front', 'box_back', 'box_top']),
      }));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script], cwd=WEB, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout.strip())

    assert got["ok"], (
        f"the underside label alone reaches {got['label']}, under the {got['floor']:.1f} "
        "floor — index.astro tells visitors it is enough for a verdict"
    )
    assert got["instinct"] < got["floor"], (
        "index.astro also tells visitors the front, back and top together are NOT enough; "
        f"they now reach {got['instinct']} against a floor of {got['floor']:.1f}"
    )


def test_the_sheet_prints_the_numbers_it_computes(sheet_source: str) -> None:
    """The empty-state copy must not hard-code a weight that has moved on.

    It states the label's reach and the instinctive set's reach. Both are
    interpolated from the shipped functions rather than typed in, and this fails
    if someone replaces either with a literal.
    """
    index = (WEB / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
    # The literal string also appears as the server-rendered placeholder; the
    # branch that computes the numbers is the last occurrence, in the script.
    at = index.rindex("Nothing added yet.")
    empty_state = index[at : at + 600]
    assert "reachableWeight(['box_bottom_label'])" in empty_state
    assert "reachableWeight(['box_front', 'box_back', 'box_top'])" in empty_state


def test_the_masthead_does_not_promise_a_check_that_has_never_run() -> None:
    """Serial Cross-Match is 0 PASS, 0 FAIL, 20 UNKNOWN over the whole corpus.

    Apple prints those serials at around a millimetre; the case lid was in shot
    in 7 posts and legible in none. The masthead used to say the record "reads
    the printed box against Apple's published hardware matrix, and the four
    serials against each other", stating as routine a comparison that has never
    once happened. It is conditional now, and this fails if the flat claim
    returns. See the serial-legibility note in knowledge/airpods_pro_2.yaml.
    """
    index = (WEB / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
    stand = index[index.index("masthead-stand") : index.index("masthead-stand") + 700]

    assert "whenever more than one can be" in stand, (
        "the masthead no longer qualifies the serial cross-check"
    )
    assert "and the four serials against each other" not in stand, (
        "the masthead states the four-serial comparison as something the record does; "
        "it has never resolved on any corpus post"
    )
