"""The corpus cache, and the trap it used to set.

A stored result holds an extraction, which is expensive and never changes, and
a verdict, which is cheap and changes every time a rule does. Keeping both in
one file under plain names meant anything reading `record["verdict"]` got the
verdict from whenever the extraction was run — history, presented as current.

That is not hypothetical. The stored verdict disagreed with the live rules on 5
of 20 posts, all in the same direction, and two separate analyses built on it
were wrong before anyone noticed. Renaming makes a naive read raise instead of
lying.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import run_corpus

ROOT = Path(__file__).resolve().parent.parent


class TestStoredVerdictsCannotBeMistakenForCurrent:
    def test_the_writer_never_emits_a_bare_verdict_field(self):
        """`verdict`, `confidence` and `ledger` must only exist after rescore."""
        import inspect

        source = inspect.getsource(run_corpus)
        written = source[source.index('"extraction": result.extraction'):]
        written = written[: written.index("return record")]
        for field in ("verdict", "confidence", "ledger"):
            assert f'"{field}":' not in written, (
                f'the writer emits a bare "{field}" to disk again; it goes stale the next '
                "time a rule changes and reads as though it were current"
            )
            assert f'"{field}_when_extracted":' in written, f"{field}_when_extracted is missing"

    def test_rescore_supplies_the_current_verdict(self):
        record = {
            "post_id": "x",
            "extraction": _minimal_extraction(),
            "verdict_when_extracted": "GENUINE",
        }
        fresh = run_corpus.rescore(record)
        assert "verdict" in fresh, "rescore must provide the current verdict"
        assert fresh["verdict_when_extracted"] == "GENUINE", "history is preserved, not overwritten"

    def test_rescore_does_not_mutate_what_it_was_given(self):
        """Analyses loop over records; a mutating rescore would poison later reads."""
        record = {"post_id": "x", "extraction": _minimal_extraction()}
        run_corpus.rescore(record)
        assert "verdict" not in record

    def test_a_stored_file_on_disk_carries_no_bare_verdict(self):
        results = ROOT / "results"
        stored = sorted(results.glob("*.json")) if results.is_dir() else []
        if not stored:
            pytest.skip("no cached corpus in this checkout")
        offenders = [p.name for p in stored if "verdict" in json.loads(p.read_text())]
        assert not offenders, f"stale-named verdict fields still on disk: {offenders[:5]}"


class TestCaptureGuidanceReflectsWhatWasMeasured:
    """The case lid serial is 0 of 20. Measured cause: framing distance.

    A lid filling the frame reads correctly at the shipped 1568px ceiling; the
    same lid at arm's length does not read at all. The guidance used to say
    "photograph the underside of the lid", which is what those seven people did.
    """

    def test_the_case_lid_step_asks_for_a_close_frame(self):
        import rules_engine

        step = rules_engine.PANEL_REQUESTS["case_lid_underside"]
        assert "fill the frame" in step.lower()
        assert "close" in step.lower()

    def test_the_earbud_step_asks_for_a_close_frame(self):
        import rules_engine

        assert "close" in rules_engine.PANEL_REQUESTS["earbud"].lower()


def _minimal_extraction() -> dict:
    """A real fixture, so this does not depend on another test module."""
    return json.loads((ROOT / "tests/sample_data/genuine_usbc.json").read_text())
