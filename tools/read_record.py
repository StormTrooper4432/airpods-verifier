"""Join a downloaded log back into a scoreable record.

The deployed sheet keeps its record in memory, and the free tier discards that
after fifteen minutes idle. Both line types reach stdout regardless, so the log
viewer holds everything the buffer did - in two streams that have to be joined
by id:

    examination        {"id":"v8wxec","verdict":"INCONCLUSIVE",...}
    examination-label  {"id":"v8wxec","truth":"COUNTERFEIT"}

This reads a log the host exported, pairs them, and reports the same numbers
`/api/record` reports live - except that this survives a restart, which is the
whole point of it existing.

    python tools/read_record.py render.log
    render-cli logs --tail 5000 | python tools/read_record.py -

It reads only what the record already contains. No photographs, no serials, no
addresses - those never reach a record in the first place.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Iterable

EXAMINATION = "examination "
LABEL = "examination-label "
VIEW = "view "

#: The verdicts the engine can reach. Anything else in a log is a format change.
VERDICTS = ("GENUINE", "HIGH_RISK_SUSPECTED", "INCONCLUSIVE")
TRUTHS = ("GENUINE", "COUNTERFEIT")


def count_views(lines: Iterable[str]) -> dict[str, int]:
    """Page views, per path, from an exported log.

    The instance keeps these in memory and the free tier stops it after fifteen
    minutes idle, so the in-process total is only "since it woke". The log is
    the durable copy and this is what reads it back.
    """
    counts: dict[str, int] = {}
    for line in lines:
        at = line.find(VIEW)
        if at == -1:
            continue
        # "examination " never contains "view ", but a path might; require the
        # marker to start a token so a URL called /preview does not match.
        if at > 0 and line[at - 1] not in " \t":
            continue
        try:
            payload = json.loads(line[at + len(VIEW) :].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("path"), str):
            counts[payload["path"]] = counts.get(payload["path"], 0) + 1
    return counts


def parse(lines: Iterable[str]) -> tuple[dict[str, dict], int]:
    """Pair examinations with their labels. Returns records by id, and how many
    labels named an examination the log did not contain."""
    records: dict[str, dict] = {}
    labels: dict[str, str] = {}

    for line in lines:
        # Host log viewers prefix their own timestamp, so find the marker
        # anywhere rather than requiring it at the start.
        for marker, sink in ((LABEL, labels), (EXAMINATION, records)):
            at = line.find(marker)
            if at == -1:
                continue
            try:
                payload = json.loads(line[at + len(marker) :].strip())
            except json.JSONDecodeError:
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                break
            if sink is labels:
                truth = payload.get("truth")
                if truth in TRUTHS:
                    labels[payload["id"]] = truth
            else:
                # A later line for the same id is the same examination; keep the
                # first, which is the one the label was attached to.
                records.setdefault(payload["id"], payload)
            break

    orphans = 0
    for record_id, truth in labels.items():
        if record_id in records:
            records[record_id]["truth"] = truth
        else:
            orphans += 1
    return records, orphans


def agreement(records: Iterable[dict]) -> dict[str, Any]:
    """The two numbers that have governed every decision in this project."""
    scored = [r for r in records if r.get("truth") in TRUTHS and r.get("verdict") in VERDICTS]
    if not scored:
        return {"labelled": 0}

    def count(truth: str, verdict: str) -> int:
        return sum(1 for r in scored if r["truth"] == truth and r["verdict"] == verdict)

    return {
        "labelled": len(scored),
        "counterfeitPassedAsGenuine": count("COUNTERFEIT", "GENUINE"),
        "genuineAccused": count("GENUINE", "HIGH_RISK_SUSPECTED"),
        "counterfeitCaught": count("COUNTERFEIT", "HIGH_RISK_SUSPECTED"),
        "genuineCleared": count("GENUINE", "GENUINE"),
        "inconclusive": sum(1 for r in scored if r["verdict"] == "INCONCLUSIVE"),
    }


def summarise(records: dict[str, dict], orphans: int,
              views_by_path: dict[str, int] | None = None) -> dict[str, Any]:
    entries = list(records.values())
    views_by_path = views_by_path or {}
    views = sum(views_by_path.values())
    examined = [r for r in entries if r.get("outcome") == "examined"]
    reached = [r["reached"] for r in examined if isinstance(r.get("reached"), (int, float))]
    errors = [
        r["reached"] - r["forecast"]
        for r in examined
        if isinstance(r.get("reached"), (int, float)) and isinstance(r.get("forecast"), (int, float))
    ]

    return {
        "records": len(entries),
        "labelsWithNoExamination": orphans,
        "views": views,
        "viewsByPath": views_by_path,
        # Of the people who looked, how many ran something. Null rather than 0
        # when nobody looked, because 0/0 is not a rate.
        "ranAfterViewing": round(len(entries) / views, 3) if views else None,
        "outcomes": dict(Counter(r.get("outcome", "?") for r in entries).most_common()),
        "verdicts": dict(Counter(r["verdict"] for r in entries if r.get("verdict")).most_common()),
        "panelsOffered": dict(
            Counter(p for r in entries for p in r.get("offered") or []).most_common()
        ),
        "rulesThatFailed": dict(
            Counter(name for r in entries for name in r.get("failed") or []).most_common()
        ),
        "meanWeightReached": round(sum(reached) / len(reached), 2) if reached else None,
        "forecast": {
            "runs": len(errors),
            "exact": sum(1 for e in errors if e == 0),
            "undershot": sum(1 for e in errors if e > 0),
            "overshot": sum(1 for e in errors if e < 0),
            "worstUnder": round(max(errors), 2) if errors else 0,
            "worstOver": round(min(errors), 2) if errors else 0,
        }
        if errors
        else {"runs": 0},
        "agreement": agreement(entries),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", help="exported log file, or - for stdin")
    parser.add_argument(
        "--unlabelled",
        action="store_true",
        help="list examinations with no label yet, newest first, so they can be labelled",
    )
    args = parser.parse_args(argv)

    stream = sys.stdin if args.log == "-" else open(args.log, encoding="utf-8", errors="replace")
    try:
        lines = stream.readlines()
    finally:
        if stream is not sys.stdin:
            stream.close()
    records, orphans = parse(lines)
    views_by_path = count_views(lines)

    if not records and not views_by_path:
        print(
            "No examination lines in that log. They look like:\n"
            '  examination {"id":"v8wxec",...}',
            file=sys.stderr,
        )
        return 1

    if args.unlabelled:
        pending = sorted(
            (r for r in records.values() if not r.get("truth") and r.get("verdict")),
            key=lambda r: r.get("at", ""),
            reverse=True,
        )
        for record in pending:
            print(f"{record['id']}  {record.get('at','')}  {record['verdict']}")
        if not pending:
            print("Every examination with a verdict is labelled.", file=sys.stderr)
        return 0

    print(json.dumps(summarise(records, orphans, views_by_path), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
