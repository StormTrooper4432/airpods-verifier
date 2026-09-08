"""Collect a labelled corpus from eBay listings, through eBay's own API.

Not a scraper. eBay's robots.txt is explicit — "The use of robots or other
automated means to access the eBay site without the express permission of eBay
is strictly prohibited", and it names LLM-driven bots specifically — and it
directs integrations to the Browse API under eBay's API License Agreement. So
this speaks to that API with a registered key and nothing else.

What you need, once:

  1. Register at https://developer.ebay.com/ (free, keys are issued
     immediately, unlike Reddit's manual review).
  2. Create a keyset and export both halves:

        export EBAY_CLIENT_ID=...
        export EBAY_CLIENT_SECRET=...

Then:

    python tools/fetch_ebay.py --query "airpods pro 2" --limit 40 --out corpus/

Every image is filtered by tools/stock_photos before it is written: a listing's
padding is Apple's own press photography, which is a picture of a genuine unit
attached to a listing that may not be one. Admitting those would corrupt the
corpus with the single thing this tool must never learn from.

Nothing here labels anything. The verdict column is left blank for a human, the
way tools/run_corpus.py already expects.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import stock_photos  # noqa: E402

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"

#: eBay's default application-level allowance is 5,000 Browse calls a day. One
#: listing costs two (search is paged, then each item is fetched), so a run of
#: a few hundred listings is comfortably inside it. Pace anyway: a burst is
#: what gets a key throttled.
PAUSE_SECONDS = 0.35

USER_AGENT = "airpods-verifier/1.0 (corpus collection; +https://developer.ebay.com)"


class EbayError(RuntimeError):
    pass


@dataclass
class Listing:
    item_id: str
    title: str
    price: str
    condition: str
    seller: str
    url: str
    image_urls: list[str] = field(default_factory=list)


def _post(url: str, data: bytes, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise EbayError(f"{error.code} from {url}: {error.read()[:300].decode(errors='replace')}")


def _get(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            # Browse requires a marketplace; US has the deepest AirPods supply.
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise EbayError(f"{error.code} from {url}: {error.read()[:300].decode(errors='replace')}")


def token_for(client_id: str, client_secret: str) -> str:
    """Application token via client credentials. No user account is involved."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    ).encode()
    payload = _post(
        OAUTH_URL,
        body,
        {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    token = payload.get("access_token")
    if not token:
        raise EbayError(f"no access_token in the OAuth response: {payload}")
    return token


def search(token: str, query: str, limit: int) -> list[str]:
    """Item ids for a query, paged to `limit`."""
    ids: list[str] = []
    offset = 0
    while len(ids) < limit:
        page = min(200, limit - len(ids))
        params = urllib.parse.urlencode(
            {
                "q": query,
                "limit": page,
                "offset": offset,
                # Used items only: a sealed retail listing is usually stock
                # photography, and a used listing is where a seller has to
                # photograph the actual unit.
                "filter": "conditions:{USED}",
            }
        )
        payload = _get(f"{BROWSE_URL}?{params}", token)
        summaries = payload.get("itemSummaries") or []
        if not summaries:
            break
        ids += [summary["itemId"] for summary in summaries if summary.get("itemId")]
        offset += page
        time.sleep(PAUSE_SECONDS)
    return ids[:limit]


def fetch_listing(token: str, item_id: str) -> Listing | None:
    payload = _get(f"{ITEM_URL}{urllib.parse.quote(item_id, safe='')}", token)
    images: list[str] = []
    if payload.get("image", {}).get("imageUrl"):
        images.append(payload["image"]["imageUrl"])
    images += [
        entry["imageUrl"]
        for entry in payload.get("additionalImages") or []
        if entry.get("imageUrl")
    ]
    if not images:
        return None
    price = payload.get("price") or {}
    return Listing(
        item_id=item_id,
        title=payload.get("title", ""),
        price=f"{price.get('value', '')} {price.get('currency', '')}".strip(),
        condition=payload.get("condition", ""),
        seller=(payload.get("seller") or {}).get("username", ""),
        url=payload.get("itemWebUrl", ""),
        image_urls=images,
    )


def download(url: str, destination: Path) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False
    if len(data) < 4096:  # a placeholder pixel, not a photograph
        return False
    destination.write_bytes(data)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fetch_ebay",
        description="Collect listing photographs into a corpus, press shots excluded.",
    )
    parser.add_argument("--query", default="airpods pro 2nd generation")
    parser.add_argument("--limit", type=int, default=40, help="listings to fetch")
    parser.add_argument("--out", type=Path, default=Path("corpus"))
    parser.add_argument(
        "--keep-stock",
        action="store_true",
        help="write press shots too, for inspecting what the filter removed",
    )
    args = parser.parse_args(argv)

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not set.\n"
            "Register at https://developer.ebay.com/ — keys are issued immediately —\n"
            "create a production keyset, then export both halves.",
            file=sys.stderr,
        )
        return 2

    try:
        token = token_for(client_id, client_secret)
        print(f"searching: {args.query!r}")
        item_ids = search(token, args.query, args.limit)
        print(f"{len(item_ids)} listings")

        staging = args.out / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        candidates: list[stock_photos.Candidate] = []
        listings: dict[str, Listing] = {}

        for index, item_id in enumerate(item_ids, 1):
            listing = fetch_listing(token, item_id)
            time.sleep(PAUSE_SECONDS)
            if listing is None:
                continue
            listings[item_id] = listing
            for position, url in enumerate(listing.image_urls):
                path = staging / f"{index:03d}-{position:02d}.jpg"
                if not download(url, path):
                    continue
                candidate = stock_photos.inspect(item_id, path)
                if candidate:
                    candidates.append(candidate)
            print(f"  [{index}/{len(item_ids)}] {listing.title[:60]}")
    except EbayError as error:
        print(f"eBay API: {error}", file=sys.stderr)
        return 3

    # The filter only works across the whole batch: reuse is a relationship
    # between listings, not a property of one image.
    verdicts = stock_photos.judge(candidates)
    print(f"\nimages: {stock_photos.summarise(verdicts)}")

    written = 0
    for candidate in candidates:
        verdict = verdicts[candidate.path]
        if not verdict.keep and not args.keep_stock:
            candidate.path.unlink(missing_ok=True)
            continue
        folder = args.out / candidate.listing_id
        folder.mkdir(parents=True, exist_ok=True)
        prefix = "" if verdict.keep else "stock-"
        candidate.path.rename(folder / f"{prefix}{candidate.path.name}")
        written += 1

    kept_listings = sorted(
        {p.parent.name for p in args.out.glob("*/*.jpg") if not p.name.startswith("stock-")}
    )
    index_path = args.out / "listings.csv"
    with index_path.open("w", encoding="utf-8") as handle:
        handle.write("listing_id,expert_verdict,condition,price,seller,url,title\n")
        for item_id in kept_listings:
            listing = listings.get(item_id)
            if listing is None:
                continue
            title = listing.title.replace('"', "'")
            handle.write(
                f'{item_id},,"{listing.condition}","{listing.price}",'
                f'"{listing.seller}","{listing.url}","{title}"\n'
            )

    try:
        staging.rmdir()
    except OSError:
        pass

    print(f"{written} images written across {len(kept_listings)} listings -> {args.out}")
    print(f"index: {index_path} (expert_verdict is blank, for you to fill in)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
