#!/usr/bin/env python3
"""Run the pipeline over a corpus of post folders and emit a review sheet.

    python tools/run_corpus.py corpus/ --out results/

For each folder it sends that post's photos as one request, scores the
extraction with the rules engine, and writes:

    results/<post_id>.json      extraction + ledger + post metadata
    results/review.md           one row per post, with a BLANK expert column
    results/review.csv          same, for a spreadsheet

The expert column is blank on purpose. Fill it in without looking at the
pipeline's verdict, then run --score to get a confusion matrix. A tool that
grades its own homework tells you nothing.

Free-tier rate limits are the binding constraint, so requests are paced and
429s back off exponentially. Already-processed posts are skipped, so an
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import is_transient_failure  # noqa: E402
from rules_engine import evaluate  # noqa: E402
from schema import Verdict, VisionExtraction  # noqa: E402
from vision_extractor import VisionExtractionError, extract  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

#: Free tiers cap requests per minute; this keeps us comfortably under.
DEFAULT_DELAY_S = 4.0
MAX_RETRIES = 4

#: Kept as a module-level alias so the retry policy has one definition shared
#: with the model-fallback path in vision_extractor.
is_retryable = is_transient_failure


def post_folders(root: Path) -> list[Path]:
    return sorted(
        folder
        for folder in root.iterdir()
        if folder.is_dir() and any(f.suffix.lower() in IMAGE_SUFFIXES for f in folder.iterdir())
    )


def images_in(folder: Path, cap: int) -> list[Path]:
    return sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)[:cap]


def process(folder: Path, *, provider, model, effort, cap, allow_lite=False, lite_only=False) -> dict:
    images = images_in(folder, cap)
    metadata = {}
    post_json = folder / "post.json"
    if post_json.is_file():
        metadata = json.loads(post_json.read_text(encoding="utf-8"))

    record: dict = {
        "post_id": metadata.get("id", folder.name),
        "folder": folder.name,
        "title": metadata.get("title", ""),
        "permalink": metadata.get("permalink", ""),
        "flair": metadata.get("flair"),
        "num_images": len(images),
        "top_comments": metadata.get("top_comments", []),
    }

    delay = DEFAULT_DELAY_S
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = extract(
                images, provider=provider, model=model, effort=effort,
                allow_lite=allow_lite, lite_only=lite_only,
            )
            break
        except VisionExtractionError as exc:
            message = str(exc)
            if is_retryable(message) and attempt < MAX_RETRIES:
                wait = delay * (2**attempt)
                print(f"      transient failure; waiting {wait:.0f}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            record["error"] = message
            return record

    report = evaluate(result.extraction)
    record.update(
        {
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "estimated_cost_usd": result.estimated_cost_usd,
            "extraction": result.extraction.model_dump(mode="json"),
            # Named for what they are: the verdict THIS extraction produced under
            # the rules in force when it was run. Rules change; extractions do
            # not. Anything reading these as current is reading history — which
            # has happened, twice in one sitting, and silently: the stored
            # verdict disagreed with the live rules on 5 of 20 posts and every
            # analysis built on it was wrong in the same direction. Call
            # rescore() and read `verdict`, which only ever exists in memory.
            "verdict_when_extracted": report.summary_verdict.value,
            "confidence_when_extracted": report.confidence_score,
            "ledger_when_extracted": report.to_dict()["evidence_ledger"],
        }
    )
    return record


def rescore(record: dict) -> dict:
    """Recompute verdict, ledger and next steps from a stored extraction.

    This is the ONLY way to get a current verdict out of a stored result. The
    file on disk carries `*_when_extracted` fields, which are history and are
    named so that reading them by accident is impossible.
    """
    extraction = record.get("extraction")
    if not extraction:
        return record
    report = evaluate(VisionExtraction.model_validate(extraction))
    record = dict(record)
    record["verdict"] = report.summary_verdict.value
    record["confidence"] = report.confidence_score
    record["ledger"] = report.to_dict()["evidence_ledger"]
    record["next_steps"] = report.next_steps
    return record


def failed_checks(record: dict) -> str:
    return "; ".join(
        item["parameter"] for item in record.get("ledger", []) if item["status"] == "FAIL"
    ) or "-"


def write_review(records: list[dict], out: Path) -> None:
    rows = []
    for record in records:
        extraction = record.get("extraction") or {}
        rows.append(
            {
                "post_id": record["post_id"],
                "expert_verdict": "",  # you fill this in, blind
                "pipeline_verdict": record.get("verdict", "ERROR"),
                "confidence": record.get("confidence", ""),
                "failed_checks": failed_checks(record),
                "model_number": extraction.get("extracted_model_number") or "",
                "variant": extraction.get("claimed_model_variant") or "",
                "copyright": extraction.get("copyright_year") or "",
                "shrinkwrap": extraction.get("has_plastic_shrinkwrap", ""),
                "images": record.get("num_images", 0),
                "title": (record.get("title") or "")[:60],
                "permalink": record.get("permalink", ""),
            }
        )

    with (out / "review.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])) if rows else None
        if writer:
            writer.writeheader()
            writer.writerows(rows)

    lines = [
        "# Corpus review sheet",
        "",
        f"{len(records)} posts. Fill in `expert_verdict` **before** reading the pipeline column",
        "(GENUINE / HIGH_RISK_SUSPECTED / INCONCLUSIVE), then:",
        "",
        "```bash",
        "python tools/run_corpus.py --score results/",
        "```",
        "",
        "| post | expert | pipeline | conf | failed checks | model# | link |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['post_id']} |  | {row['pipeline_verdict']} | {row['confidence']} | "
            f"{row['failed_checks']} | {row['model_number'] or '-'} | {row['permalink']} |"
        )
    (out / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def refresh_review(results_dir: Path) -> None:
    """Re-score every stored extraction and rewrite the review sheet in place.

    Keeps the expert_verdict column, which is hand-entered and must survive.
    """
    csv_path = results_dir / "review.csv"
    existing: dict[str, str] = {}
    if csv_path.is_file():
        existing = {
            row["post_id"]: row.get("expert_verdict", "")
            for row in csv.DictReader(csv_path.open(encoding="utf-8"))
        }

    records = []
    for path in sorted(results_dir.glob("*.json"), key=lambda p: (len(p.stem), p.stem)):
        records.append(rescore(json.loads(path.read_text(encoding="utf-8"))))
    if not records:
        return
    write_review(records, results_dir)

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    for row in rows:
        row["expert_verdict"] = existing.get(row["post_id"], row.get("expert_verdict", ""))
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def score(results_dir: Path) -> int:
    """Compare filled-in expert labels against the pipeline."""
    refresh_review(results_dir)
    csv_path = results_dir / "review.csv"
    if not csv_path.is_file():
        print(f"error: {csv_path} not found - run the corpus first.", file=sys.stderr)
        return 1

    rows = [r for r in csv.DictReader(csv_path.open(encoding="utf-8")) if r["expert_verdict"].strip()]
    if not rows:
        print("No expert labels filled in yet. Add them to review.csv, then re-run --score.")
        return 0

    labels = [v.value for v in Verdict]
    matrix = {expert: {pipeline: 0 for pipeline in labels} for expert in labels}
    unknown = 0
    for row in rows:
        expert = row["expert_verdict"].strip().upper()
        pipeline = row["pipeline_verdict"].strip().upper()
        if expert not in matrix or pipeline not in labels:
            unknown += 1
            continue
        matrix[expert][pipeline] += 1

    total = sum(sum(r.values()) for r in matrix.values())
    correct = sum(matrix[label][label] for label in labels)
    print(f"\nLabelled: {total} posts" + (f" ({unknown} unparseable)" if unknown else ""))
    print(f"Agreement: {correct}/{total} = {correct/total:.1%}\n" if total else "")

    width = max(len(label) for label in labels) + 2
    header = "expert \\ pipeline"
    print(f"{header:>{width}} " + " ".join(f"{label[:12]:>13}" for label in labels))
    for expert in labels:
        print(f"{expert:>{width}} " + " ".join(f"{matrix[expert][p]:>13}" for p in labels))

    # The number that actually matters for a buyer-protection tool.
    fake_passed = matrix["HIGH_RISK_SUSPECTED"]["GENUINE"]
    genuine_flagged = matrix["GENUINE"]["HIGH_RISK_SUSPECTED"]
    print(f"\n  Called a real fake GENUINE (worst case): {fake_passed}")
    print(f"  Called a genuine unit HIGH RISK        : {genuine_flagged}")
    if fake_passed:
        print("\n  A fake passed. That is the failure mode that costs someone $200 -")
        print("  investigate those posts before tuning anything else.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path, nargs="?", help="Directory of post folders.")
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--effort", default="medium")
    parser.add_argument(
        "--lite-only",
        action="store_true",
        help="Use only Flash-Lite models. For when the full-Flash family is out of daily quota.",
    )
    parser.add_argument(
        "--allow-flash",
        "--allow-lite",
        dest="allow_lite",
        action="store_true",
        help="Extend the chain to full-Flash after Lite. Measured 2026-09-08: identical "
             "identifying fields to Lite on 7 posts, and quota exhausted after 7 requests.",
    )
    parser.add_argument("--max-images", type=int, default=4, help="Images per post (token cost).")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S, help="Seconds between posts.")
    parser.add_argument("--limit", type=int, help="Only process the first N folders.")
    parser.add_argument("--score", action="store_true", help="Score filled-in labels in --out.")
    args = parser.parse_args()

    if args.score:
        return score(args.out if args.corpus is None else args.corpus)

    if args.corpus is None:
        parser.error("a corpus directory is required (or pass --score)")
    if not args.corpus.is_dir():
        print(f"error: {args.corpus} is not a directory", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    folders = post_folders(args.corpus)[: args.limit]
    print(f"{len(folders)} post folders in {args.corpus}\n")

    records: list[dict] = []
    spend = 0.0
    for index, folder in enumerate(folders, start=1):
        cached = args.out / f"{folder.name}.json"
        if cached.is_file():
            record = json.loads(cached.read_text(encoding="utf-8"))
            # Re-score against the CURRENT rules. The extraction is the
            # expensive part and is safe to reuse; the verdict is cheap and goes
            # stale the moment a rule changes. Trusting the cached verdict made
            # a scoring run report false accusations that had already been fixed.
            record = rescore(record)
            cached.write_text(json.dumps(record, indent=2), encoding="utf-8")
            records.append(record)
            print(f"[{index}/{len(folders)}] {folder.name}  (cached, re-scored)")
            continue

        print(f"[{index}/{len(folders)}] {folder.name}", flush=True)
        record = process(
            folder, provider=args.provider, model=args.model, effort=args.effort,
            cap=args.max_images, allow_lite=args.allow_lite, lite_only=args.lite_only,
        )
        # Only cache successes. Caching an error would make a re-run replay the
        # failure forever, which is how a transient outage becomes permanent
        # missing data in the corpus.
        if "error" not in record:
            cached.write_text(json.dumps(record, indent=2), encoding="utf-8")
        records.append(record)
        spend += record.get("estimated_cost_usd") or 0.0

        if "error" in record:
            print(f"      ERROR: {record['error'][:120]}", flush=True)
        else:
            print(f"      {record['verdict']} @ {record['confidence']}  ({failed_checks(record)})", flush=True)
        time.sleep(args.delay)

    if records:
        write_review(records, args.out)
        errors = sum(1 for r in records if "error" in r)
        print(f"\n{len(records)} processed, {errors} errors, est. spend ${spend:.4f}")
        print(f"Review sheet: {args.out}/review.md  and  {args.out}/review.csv")
        print("Fill in expert_verdict, then: python tools/run_corpus.py --score results/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
