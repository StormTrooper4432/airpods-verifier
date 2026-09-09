"""The offline log joiner.

The deployed record lives in memory and the free tier discards it after fifteen
minutes idle, so the log is the durable copy and this is what reads it. If the
join is wrong, the only surviving measurement of the live tool is wrong - and
silently, since a mis-join still prints a plausible matrix.
"""

from __future__ import annotations

import json

import pytest

from tools import read_record

EXAMINED = (
    '2026-09-08T11:00:01Z examination {"id":"aaa111","at":"2026-09-08T11:00:01Z",'
    '"session":"s1","attempt":1,"outcome":"examined","offered":["box_back"],'
    '"forecast":9.5,"reached":12.0,"verdict":"HIGH_RISK_SUSPECTED","score":41,'
    '"failed":["Typography & Print Quality"]}'
)
CLEARED = (
    'examination {"id":"ccc333","at":"2026-09-08T11:09:10Z","session":"s3","attempt":1,'
    '"outcome":"examined","offered":["box_back"],"verdict":"GENUINE","score":88}'
)


class TestJoining:
    def test_a_label_attaches_to_its_examination(self):
        records, orphans = read_record.parse(
            [EXAMINED, 'examination-label {"id":"aaa111","truth":"COUNTERFEIT"}']
        )
        assert records["aaa111"]["truth"] == "COUNTERFEIT"
        assert orphans == 0

    def test_a_label_for_an_examination_the_log_lost_is_counted_not_dropped(self):
        """Logs get truncated. A label with no examination is a known unknown,
        not a silent zero - it means the log window is too short."""
        records, orphans = read_record.parse(
            ['examination-label {"id":"gone99","truth":"GENUINE"}']
        )
        assert records == {}
        assert orphans == 1

    def test_the_label_marker_is_not_read_as_an_examination(self):
        records, _ = read_record.parse(['examination-label {"id":"aaa111","truth":"GENUINE"}'])
        assert "aaa111" not in records

    def test_a_host_timestamp_prefix_does_not_hide_the_line(self):
        records, _ = read_record.parse(["2026-09-08T11:00:01.123Z  " + EXAMINED])
        assert "aaa111" in records

    def test_unrelated_lines_and_malformed_json_are_stepped_over(self):
        records, orphans = read_record.parse(
            [
                "cold start in 42s",
                "examination {not json at all}",
                "examination [1,2,3]",
                'examination {"no":"id"}',
                EXAMINED,
            ]
        )
        assert list(records) == ["aaa111"]
        assert orphans == 0

    def test_a_repeated_id_keeps_the_examination_the_label_was_attached_to(self):
        again = EXAMINED.replace('"score":41', '"score":99')
        records, _ = read_record.parse([EXAMINED, again])
        assert records["aaa111"]["score"] == 41


class TestTruthIsNarrow:
    """A label the rules cannot be scored against is not a label."""

    @pytest.mark.parametrize("truth", ["maybe?", "genuine", "FAKE", "", None, 1, True])
    def test_anything_but_the_two_words_is_refused(self, truth):
        records, orphans = read_record.parse(
            [EXAMINED, "examination-label " + json.dumps({"id": "aaa111", "truth": truth})]
        )
        assert "truth" not in records["aaa111"]
        assert orphans == 0


class TestAgreement:
    def test_it_names_the_two_numbers_that_govern_every_decision(self):
        records, _ = read_record.parse(
            [
                EXAMINED,
                'examination-label {"id":"aaa111","truth":"COUNTERFEIT"}',
                CLEARED,
                'examination-label {"id":"ccc333","truth":"GENUINE"}',
            ]
        )
        result = read_record.agreement(records.values())
        assert result["labelled"] == 2
        assert result["counterfeitPassedAsGenuine"] == 0
        assert result["genuineAccused"] == 0
        assert result["counterfeitCaught"] == 1
        assert result["genuineCleared"] == 1

    def test_a_counterfeit_that_passed_is_reported(self):
        passed = CLEARED.replace("ccc333", "bad001")
        records, _ = read_record.parse(
            [passed, 'examination-label {"id":"bad001","truth":"COUNTERFEIT"}']
        )
        assert read_record.agreement(records.values())["counterfeitPassedAsGenuine"] == 1

    def test_an_unlabelled_examination_is_not_scored_either_way(self):
        records, _ = read_record.parse([EXAMINED, CLEARED])
        assert read_record.agreement(records.values()) == {"labelled": 0}

    def test_a_label_on_an_examination_with_no_verdict_is_not_scored(self):
        """A rate-limited attempt has nothing to be right or wrong about."""
        refused = 'examination {"id":"ddd444","outcome":"rate-limited","offered":[]}'
        records, _ = read_record.parse(
            [refused, 'examination-label {"id":"ddd444","truth":"GENUINE"}']
        )
        assert read_record.agreement(records.values()) == {"labelled": 0}


class TestSummary:
    def test_the_forecast_error_is_reported_in_both_directions(self):
        """An undershooting forecast talks people out of examinations that would
        have worked; an overshooting one promises a verdict never delivered."""
        records, orphans = read_record.parse([EXAMINED])
        forecast = read_record.summarise(records, orphans)["forecast"]
        assert forecast["runs"] == 1
        assert forecast["undershot"] == 1
        assert forecast["worstUnder"] == 2.5

    def test_it_reports_which_rules_actually_decide_anything(self):
        records, orphans = read_record.parse([EXAMINED])
        summary = read_record.summarise(records, orphans)
        assert summary["rulesThatFailed"] == {"Typography & Print Quality": 1}


class TestCommandLine:
    def test_an_empty_log_says_what_a_record_line_looks_like(self, tmp_path, capsys):
        log = tmp_path / "empty.log"
        log.write_text("nothing to see here\n")
        assert read_record.main([str(log)]) == 1
        assert "examination" in capsys.readouterr().err

    def test_it_prints_json_a_pipe_can_read(self, tmp_path, capsys):
        log = tmp_path / "one.log"
        log.write_text(EXAMINED + "\n")
        assert read_record.main([str(log)]) == 0
        assert json.loads(capsys.readouterr().out)["records"] == 1

    def test_unlabelled_lists_what_still_needs_an_answer(self, tmp_path, capsys):
        log = tmp_path / "two.log"
        log.write_text(
            EXAMINED + "\n" + CLEARED + '\nexamination-label {"id":"aaa111","truth":"GENUINE"}\n'
        )
        assert read_record.main([str(log), "--unlabelled"]) == 0
        out = capsys.readouterr().out
        assert "ccc333" in out and "aaa111" not in out


def test_it_reads_no_field_a_record_never_contains():
    """The record holds no photographs, serials or addresses. This must not
    invent a dependency on one, or a future log will be expected to carry it.

    Checked against the code with prose stripped out - the docstrings discuss
    exactly these words, and a test that string-matches them breaks on a
    reworded comment instead of on a real new dependency."""
    import ast

    tree = ast.parse(open(read_record.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""  # blank every docstring
    code = ast.unparse(tree).lower()

    for banned in ("serial", "filename", "address", "user_agent", "photo", "image"):
        assert banned not in code, f"{banned} is not something a record carries"


class TestViewCounting:
    """Page views, read back from an exported log.

    The instance holds these in memory and the free tier stops it after fifteen
    minutes idle, so the live total only ever means "since it woke". The log is
    the durable copy.
    """

    VIEW = 'view {"path":"/","at":"2026-09-08T22:00:00Z"}'

    def test_views_are_counted_per_path(self):
        counts = read_record.count_views(
            [self.VIEW, self.VIEW, 'view {"path":"/about","at":"2026-09-08T22:01:00Z"}']
        )
        assert counts == {"/": 2, "/about": 1}

    def test_a_path_containing_the_word_view_is_not_a_marker(self):
        """The marker has to start a token, or /preview would count as one."""
        counts = read_record.count_views(['GET /preview 200', 'served overview 200'])
        assert counts == {}

    def test_examination_lines_are_not_views(self):
        counts = read_record.count_views([EXAMINED, CLEARED])
        assert counts == {}

    def test_a_host_timestamp_prefix_does_not_hide_a_view(self):
        assert read_record.count_views(["2026-09-08T22:00:00.123Z  " + self.VIEW]) == {"/": 1}

    def test_malformed_view_lines_are_stepped_over(self):
        counts = read_record.count_views(
            ["view {not json}", 'view {"no":"path"}', "view []", self.VIEW]
        )
        assert counts == {"/": 1}

    def test_the_summary_reports_the_rate_this_was_built_for(self):
        records, orphans = read_record.parse([EXAMINED])
        summary = read_record.summarise(records, orphans, {"/": 4})
        assert summary["views"] == 4
        assert summary["ranAfterViewing"] == 0.25

    def test_no_visitors_yet_is_null_rather_than_zero(self):
        """0/0 is not a rate, and printing 0 would state a finding from no data."""
        records, orphans = read_record.parse([EXAMINED])
        assert read_record.summarise(records, orphans, {})["ranAfterViewing"] is None

    def test_a_log_of_only_views_still_reports(self, tmp_path, capsys):
        """A round where everyone looked and nobody ran is the finding, not an error."""
        log = tmp_path / "views.log"
        log.write_text(self.VIEW + "\n" + self.VIEW + "\n")
        assert read_record.main([str(log)]) == 0
        assert json.loads(capsys.readouterr().out)["views"] == 2
