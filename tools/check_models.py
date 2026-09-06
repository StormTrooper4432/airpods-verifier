#!/usr/bin/env python3
"""Probe which models in a provider's fallback chain actually serve requests.

Model availability is not what the docs' pricing page says it is: models get
retired for new accounts, and free-tier quota is metered per model. Run this
before trusting a chain, and after editing one.

    python tools/check_models.py [--provider gemini]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import get_provider  # noqa: E402


def probe_gemini(client, model: str) -> tuple[str, str]:
    try:
        client.interactions.create(
            model=model,
            input=[{"type": "text", "text": "hi"}],
            generation_config={"max_output_tokens": 5},
        )
        return "OK", "has quota and serves this API"
    except Exception as exc:  # noqa: BLE001 - classifying, not handling
        text = str(exc)
        message = text.split("'message':")[-1][:120].strip() if "'message':" in text else text[:120]
        if "404" in text:
            return "GONE", message
        if "429" in text or "quota" in text.lower():
            return "QUOTA", message
        return "ERROR", message


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="gemini")
    args = parser.parse_args()

    if args.provider != "gemini":
        print("Only the gemini chain is probeable without spending money; skipping.")
        return 0

    from google import genai

    # One client for every probe: a fresh Client() per call gets closed by the
    # SDK's lifecycle management before the next request goes out.
    client = genai.Client()
    chain = get_provider(args.provider).fallback_chain
    print(f"Probing the {args.provider} fallback chain ({len(chain)} models)\n")
    gone = 0
    for model in chain:
        status, detail = probe_gemini(client, model)
        print(f"  {model:26} {status:6} {detail}")
        gone += status == "GONE"

    if gone:
        print(f"\n{gone} model(s) are retired - remove them from fallback_chain.")
        return 1
    print("\nChain is valid. QUOTA entries are temporary; GONE would be fatal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
