#!/usr/bin/env python3
"""Build a photo corpus from a subreddit, one folder per post.

Reddit blocks unauthenticated JSON access with a bot challenge, so this uses the
official OAuth API. Create a credential once at https://www.reddit.com/prefs/apps
("script" type, redirect http://localhost:8080), then:

    export REDDIT_CLIENT_ID=...
    export REDDIT_CLIENT_SECRET=...
    python tools/fetch_reddit.py --subreddit AreMyAirpodsAuthentic --limit 60

Output layout - one directory per post, which is exactly what the pipeline wants
because a single listing's photos belong in a single request:

    corpus/<post_id>/
        01_<media_id>.jpg
        02_<media_id>.jpg
        post.json          # title, flair, score, permalink, comment snippets

The comment snippets matter: on an authentication subreddit the replies often
contain the community's verdict, which is a useful cross-check against an
expert label (never a substitute for one).

Downloaded photos are other people's; they stay gitignored. Use them to evaluate
the model, not to republish.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "python:airpods-verifier:0.1 (by /u/{user}; personal research PoC)"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

#: Reddit allows 100 requests/minute on OAuth. Stay well under it.
LISTING_DELAY_S = 1.5
IMAGE_DELAY_S = 0.4

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class RedditError(RuntimeError):
    pass


def _request(url: str, *, headers: dict, data: bytes | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "replace")
        raise RedditError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RedditError(f"Could not reach {url}: {exc.reason}") from exc


def get_token(client_id: str, client_secret: str, username: str | None) -> str:
    """Application-only OAuth token (read-only public data)."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    payload = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    raw = _request(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "User-Agent": USER_AGENT.format(user=username or "unknown"),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=payload,
    )
    token = json.loads(raw).get("access_token")
    if not token:
        raise RedditError(f"No access_token in response: {raw[:200]!r}")
    return token


def api_get(path: str, token: str, username: str | None, **params) -> dict:
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    raw = _request(
        url,
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": USER_AGENT.format(user=username or "unknown"),
        },
    )
    return json.loads(raw)


def image_urls(post: dict) -> list[tuple[str, str]]:
    """Return ``(media_id, url)`` for every image attached to a post.

    Handles the three shapes Reddit uses: native galleries, direct image
    submissions, and the preview block on link posts.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(media_id: str, url: str) -> None:
        url = html.unescape(url)
        if url not in seen:
            seen.add(url)
            found.append((media_id, url))

    # 1. Native gallery - the common case for "here are 8 photos of my box".
    if post.get("is_gallery") and isinstance(post.get("media_metadata"), dict):
        order = [
            item.get("media_id")
            for item in (post.get("gallery_data") or {}).get("items", [])
            if item.get("media_id")
        ]
        metadata = post["media_metadata"]
        for media_id in order or list(metadata):
            entry = metadata.get(media_id) or {}
            if entry.get("status") != "valid":
                continue
            source = (entry.get("s") or {}).get("u") or (entry.get("s") or {}).get("gif")
            if source:
                add(media_id, source)

    # 2. Direct image submission.
    url = post.get("url_overridden_by_dest") or post.get("url") or ""
    if url.lower().split("?")[0].endswith(_IMAGE_SUFFIXES):
        add(post.get("id", "direct"), url)

    # 3. Preview block (link posts, crossposts).
    for index, image in enumerate((post.get("preview") or {}).get("images", [])):
        source = (image.get("source") or {}).get("url")
        if source:
            add(f"preview{index}", source)

    return found


def fetch_posts(subreddit: str, token: str, username: str | None, limit: int, sort: str) -> list[dict]:
    posts: list[dict] = []
    after: str | None = None
    while len(posts) < limit:
        batch_size = min(100, limit - len(posts))
        params = {"limit": batch_size, "raw_json": 1}
        if after:
            params["after"] = after
        payload = api_get(f"/r/{subreddit}/{sort}", token, username, **params)
        children = payload.get("data", {}).get("children", [])
        if not children:
            break
        posts.extend(child["data"] for child in children)
        after = payload.get("data", {}).get("after")
        if not after:
            break
        time.sleep(LISTING_DELAY_S)
    return posts[:limit]


def fetch_top_comments(subreddit: str, post_id: str, token: str, username: str | None, count: int = 5) -> list[dict]:
    """Grab a few top comments - the community's read, useful as a cross-check."""
    try:
        payload = api_get(
            f"/r/{subreddit}/comments/{post_id}", token, username, limit=count, depth=1, raw_json=1
        )
    except RedditError:
        return []
    if len(payload) < 2:
        return []
    comments = []
    for child in payload[1].get("data", {}).get("children", []):
        data = child.get("data", {})
        body = (data.get("body") or "").strip()
        if body and data.get("author") != "AutoModerator":
            comments.append({"author": data.get("author"), "score": data.get("score"), "body": body[:600]})
    return comments[:count]


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit] or "post"


def download(url: str, destination: Path, username: str | None) -> bool:
    try:
        blob = _request(url, headers={"User-Agent": USER_AGENT.format(user=username or "unknown")})
    except RedditError as exc:
        print(f"      ! {exc}", file=sys.stderr)
        return False
    destination.write_bytes(blob)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subreddit", required=True)
    parser.add_argument("--limit", type=int, default=50, help="Posts to consider (default 50).")
    parser.add_argument("--sort", default="new", choices=["new", "hot", "top"])
    parser.add_argument("--out", type=Path, default=Path("corpus"))
    parser.add_argument("--min-images", type=int, default=1, help="Skip posts with fewer images.")
    parser.add_argument("--max-images", type=int, default=8, help="Cap images per post (token cost).")
    parser.add_argument("--no-comments", action="store_true")
    args = parser.parse_args()

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    if not (client_id and client_secret):
        print(
            "error: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET.\n"
            "Create a 'script' app at https://www.reddit.com/prefs/apps (redirect: http://localhost:8080).",
            file=sys.stderr,
        )
        return 2

    try:
        token = get_token(client_id, client_secret, username)
        print(f"authenticated; fetching r/{args.subreddit} ({args.sort}, up to {args.limit} posts)")
        posts = fetch_posts(args.subreddit, token, username, args.limit, args.sort)
    except RedditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{len(posts)} posts returned\n")
    args.out.mkdir(parents=True, exist_ok=True)
    kept = 0

    for post in posts:
        images = image_urls(post)[: args.max_images]
        title = post.get("title", "")
        if len(images) < args.min_images:
            print(f"  skip {post.get('id')}  ({len(images)} images)  {title[:50]}")
            continue

        folder = args.out / f"{post['id']}_{slugify(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        print(f"  {post['id']}  {len(images)} images  {title[:60]}")

        saved = []
        for index, (media_id, url) in enumerate(images, start=1):
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
            if suffix not in _IMAGE_SUFFIXES:
                suffix = ".jpg"
            destination = folder / f"{index:02d}_{slugify(media_id, 20)}{suffix}"
            if download(url, destination, username):
                saved.append(destination.name)
            time.sleep(IMAGE_DELAY_S)

        comments = [] if args.no_comments else fetch_top_comments(args.subreddit, post["id"], token, username)
        if comments:
            time.sleep(LISTING_DELAY_S)

        (folder / "post.json").write_text(
            json.dumps(
                {
                    "id": post.get("id"),
                    "title": title,
                    "selftext": (post.get("selftext") or "")[:2000],
                    "flair": post.get("link_flair_text"),
                    "score": post.get("score"),
                    "num_comments": post.get("num_comments"),
                    "created_utc": post.get("created_utc"),
                    "permalink": "https://reddit.com" + (post.get("permalink") or ""),
                    "images": saved,
                    "top_comments": comments,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        kept += 1

    print(f"\n{kept} post folders written to {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
