"""CLI entry point: photographs (or a saved extraction) -> Evidence Ledger.

    python main.py box_front.jpg box_underside.jpg case.jpg
    python main.py --extraction tests/sample_data/genuine_usbc.json   # no API call
    python main.py box.jpg --provider anthropic                       # compare models
    python main.py box.jpg --json > report.json

Vision provider defaults to Gemini (free on the Google AI Studio tier). Set
GEMINI_API_KEY, or ANTHROPIC_API_KEY when using --provider anthropic.

Exit codes make the tool usable from a script:
    0  GENUINE
    1  HIGH_RISK_SUSPECTED
    2  INCONCLUSIVE
    3  the pipeline could not run (bad input, API failure)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from providers import PROVIDERS
from rules_engine import evaluate
from schema import EvidenceStatus, Verdict, VerificationReport, VisionExtraction

EXIT_CODES = {
    Verdict.GENUINE: 0,
    Verdict.HIGH_RISK_SUSPECTED: 1,
    Verdict.INCONCLUSIVE: 2,
}
EXIT_PIPELINE_ERROR = 3

_STATUS_MARKS = {
    EvidenceStatus.PASS: "PASS",
    EvidenceStatus.FAIL: "FAIL",
    EvidenceStatus.UNKNOWN: "----",
}

_VERDICT_HEADLINES = {
    Verdict.GENUINE: "GENUINE - consistent with a genuine unit",
    Verdict.HIGH_RISK_SUSPECTED: "HIGH RISK - do not buy without further checks",
    Verdict.INCONCLUSIVE: "INCONCLUSIVE - not enough readable evidence",
}

_ANSI = {
    EvidenceStatus.PASS: "\033[32m",
    EvidenceStatus.FAIL: "\033[31m",
    EvidenceStatus.UNKNOWN: "\033[90m",
}
_RESET = "\033[0m"
_BOLD = "\033[1m"

_RULE_WIDTH = 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airpods-verifier",
        description="Authenticate AirPods Pro 2 packaging and hardware from photographs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "images",
        nargs="*",
        type=Path,
        help="Photographs of the box and hardware. Include the box underside fine print.",
    )
    parser.add_argument(
        "--extraction",
        type=Path,
        metavar="FILE",
        help="Skip the vision call and run the rules engine on a saved extraction JSON file.",
    )
    parser.add_argument(
        "--save-extraction",
        type=Path,
        metavar="FILE",
        help="Write the vision extraction to a file so it can be re-scored without another API call.",
    )
    parser.add_argument(
        "--variant",
        choices=["Pro 2 USB-C", "Pro 2 Lightning"],
        help="Override the variant the seller claims, instead of relying on the photographs.",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        help="Vision backend for the extraction stage (default: gemini, free tier).",
    )
    parser.add_argument("--model", help="Model ID for the extraction stage.")
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Extraction effort. Lower is cheaper; 'medium' is the default.",
    )
    parser.add_argument(
        "--lite-only",
        action="store_true",
        help="Use only Flash-Lite models (when the full-Flash family is out of quota).",
    )
    parser.add_argument(
        # The flag adds full Flash; it never added Lite. "--allow-lite" is kept
        # as an alias so existing scripts keep working, but the name says the
        # opposite of what it does and is no longer the one documented.
        "--allow-flash",
        "--allow-lite",
        dest="allow_lite",
        action="store_true",
        help="Extend the chain to the full-Flash models after Lite. Measured on 7 corpus "
             "posts: same identifying fields as Lite, slightly worse at localising the label, "
             "and out of free-tier quota after 7 multi-image requests.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Fail instead of downgrading to a lesser model when the primary is overloaded.",
    )
    parser.add_argument(
        "--sources",
        action="store_true",
        help="Show the provenance of every check, not only the ones that failed.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as raw JSON.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour.")
    return parser


def load_extraction_file(path: Path) -> VisionExtraction:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return VisionExtraction.model_validate(payload)


def _colorize(text: str, status: EvidenceStatus, enabled: bool) -> str:
    return f"{_ANSI[status]}{text}{_RESET}" if enabled else text


def _wrap(text: str, indent: str, width: int = _RULE_WIDTH) -> str:
    return textwrap.fill(
        text, width=width, initial_indent=indent, subsequent_indent=indent
    )


def render_report(
    report: VerificationReport, *, color: bool, show_sources: bool = False
) -> str:
    lines: list[str] = ["", "=" * _RULE_WIDTH, "  EVIDENCE LEDGER", "=" * _RULE_WIDTH]

    current_tier: int | None = None
    for item in report.evidence_ledger:
        if item.tier != current_tier:
            current_tier = item.tier
            label = (
                "TIER 1 - HARDWARE IDENTITY MATRIX"
                if item.tier == 1
                else "TIER 2 - PACKAGING & VISUAL SIGNATURES"
            )
            lines.append("")
            lines.append(f"{_BOLD if color else ''}{label}{_RESET if color else ''}")

        mark = _colorize(f"[{_STATUS_MARKS[item.status]}]", item.status, color)
        lines.append(f"  {mark} {item.parameter}")
        lines.append(_wrap(item.detail, indent="         "))
        # Provenance is shown wherever the tool makes an accusation. Being told
        # an item is fake without being told where that claim comes from is not
        # something a buyer can act on or argue with.
        if show_sources or item.status is EvidenceStatus.FAIL:
            source = _colorize(f"source: {item.source}", EvidenceStatus.UNKNOWN, color)
            lines.append(_wrap(source, indent="         "))

    headline = _VERDICT_HEADLINES[report.summary_verdict]
    status_for_color = {
        Verdict.GENUINE: EvidenceStatus.PASS,
        Verdict.HIGH_RISK_SUSPECTED: EvidenceStatus.FAIL,
        Verdict.INCONCLUSIVE: EvidenceStatus.UNKNOWN,
    }[report.summary_verdict]

    lines.append("")
    lines.append("=" * _RULE_WIDTH)
    lines.append(f"  VERDICT: {_colorize(headline, status_for_color, color)}")
    # For an inconclusive result the score is coverage - how much of the check
    # could be run - not confidence in a finding. Labelling it "Confidence"
    # invites reading 0.74 as "probably fine", which is the opposite of what an
    # inconclusive verdict means.
    label = (
        "Evidence coverage"
        if report.summary_verdict is Verdict.INCONCLUSIVE
        else "Confidence"
    )
    lines.append(f"  {label}: {report.confidence_score:.2f}")
    lines.append("=" * _RULE_WIDTH)

    if report.next_steps:
        lines.append("")
        lines.append(f"{_BOLD if color else ''}WHAT TO ASK FOR NEXT{_RESET if color else ''}")
        for index, step in enumerate(report.next_steps, start=1):
            lines.append(_wrap(f"{index}. {step}", indent="     ", width=_RULE_WIDTH))

    if report.summary_verdict is Verdict.GENUINE:
        lines.append("")
        lines.append(
            _wrap(
                "This check covers packaging and printed identifiers only. It cannot detect a "
                "counterfeit carrying a serial cloned from a genuine unit, so also confirm the "
                "serial at checkcoverage.apple.com before paying.",
                indent="  ",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _render_usage(result) -> str:
    """One-line provenance footer: which model read the photos, and what it cost."""
    if result.free_tier:
        cost = "free tier"
    elif result.estimated_cost_usd is not None:
        cost = f"est. ~${result.estimated_cost_usd:.4f}"
    else:
        cost = "cost unknown (model not in pricing table)"
    return (
        f"{result.provider}/{result.model} | {result.input_tokens} in / "
        f"{result.output_tokens} out | cached {result.cached_tokens} | {cost}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if bool(args.images) == bool(args.extraction):
        parser.error("Provide either one or more image paths, or --extraction FILE (not both).")

    try:
        if args.extraction:
            extraction = load_extraction_file(args.extraction)
            result = None
            local = None
        else:
            # Imported lazily so --extraction runs (and the tests import main)
            # without needing the anthropic SDK configured.
            import vision_extractor

            kwargs = {}
            if args.provider:
                kwargs["provider"] = args.provider
            if args.model:
                kwargs["model"] = args.model
            if args.effort:
                kwargs["effort"] = args.effort
            if args.no_fallback:
                kwargs["fallback"] = False
            if args.allow_lite:
                kwargs["allow_lite"] = True
            if args.lite_only:
                kwargs["lite_only"] = True

            result = vision_extractor.extract(args.images, **kwargs)
            extraction = result.extraction
            # Pixel measurements Python makes for itself - no API call, no cost.
            local = vision_extractor.analyse_locally(args.images, extraction)

            if args.save_extraction:
                args.save_extraction.write_text(
                    json.dumps(extraction.model_dump(mode="json"), indent=2), encoding="utf-8"
                )
    except Exception as exc:  # surfaced to the user, not a stack trace
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PIPELINE_ERROR

    if args.variant:
        extraction = extraction.model_copy(update={"claimed_model_variant": args.variant})

    report = evaluate(extraction, local)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        color = not args.no_color and sys.stdout.isatty()
        print(render_report(report, color=color, show_sources=args.sources))
        if result is not None:
            print(f"  {_render_usage(result)}\n", file=sys.stderr)

    return EXIT_CODES[report.summary_verdict]


if __name__ == "__main__":
    raise SystemExit(main())
