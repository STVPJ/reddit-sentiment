"""Minimal async client for the Arctic Shift Reddit archive (no Reddit API key needed).

https://github.com/ArthurHeitmann/arctic_shift
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import httpx

POSTS_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
COMMENTS_URL = "https://arctic-shift.photon-reddit.com/api/comments/search"
REQUEST_DELAY = 1.0   # seconds between requests (be polite; avoids 429s)
PAGE_LIMIT = 100      # Arctic Shift's per-request cap
MAX_RETRIES = 3       # on 429


def _log(msg: str) -> None:
    print(msg, flush=True)


def normalize_post(data: dict) -> dict | None:
    """Arctic Shift post -> the fields we store. None for deleted/removed or malformed."""
    post_id, subreddit = data.get("id"), data.get("subreddit")
    if not post_id or not subreddit:
        return None
    title = data.get("title", "")
    selftext = data.get("selftext", "") or ""
    if selftext in ("[deleted]", "[removed]") or title == "[deleted]":
        return None
    return {
        "reddit_id": f"t3_{post_id}",
        "subreddit": subreddit,
        "title": title,
        "selftext": selftext,
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/",
        "score": data.get("score", 0) or 0,
        "num_comments": data.get("num_comments", 0) or 0,
        "created_utc": int(data.get("created_utc", 0)),
    }


class ArcticShiftClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        ua = os.environ.get("ARCTIC_SHIFT_USER_AGENT", "reddit-sentiment/1.0")
        self._client = httpx.AsyncClient(headers={"User-Agent": ua}, timeout=30,
                                         follow_redirects=True, transport=transport)
        self._last = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _rate_limit(self) -> None:
        wait = REQUEST_DELAY - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def _get(self, url: str, params: dict) -> list[dict] | None:
        """One page. None on error, so callers never mistake an outage for "no more data"."""
        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limit()
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json().get("data", [])
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                _log(f"  HTTP {e.response.status_code} for {params}")
                return None
            except Exception as e:  # network, JSON
                _log(f"  error for {params}: {e}")
                return None
        return None

    async def fetch_subreddit_posts(self, subreddit: str, after: str, before: str) -> list[dict]:
        """All posts in a subreddit between two YYYY-MM-DD dates, oldest first."""
        out, cursor = [], after
        while True:
            page = await self._get(POSTS_URL, {"subreddit": subreddit, "after": cursor,
                                               "before": before, "limit": PAGE_LIMIT,
                                               "sort": "asc"})
            if not page:
                break
            out.extend(page)
            last = datetime.fromtimestamp(page[-1].get("created_utc", 0),
                                          tz=timezone.utc).strftime("%Y-%m-%d")
            if last <= cursor or len(page) < PAGE_LIMIT:
                break
            cursor = last
        return out

    async def fetch_comments_after(self, link_id: str, after_utc: int,
                                   limit: int = PAGE_LIMIT) -> list[dict] | None:
        """One page of a post's comments created after `after_utc`, OLDEST first.

        Arctic Shift returns newest first by default, which silently drops the early
        discussion on a busy thread; paging forward from a cursor reads it all and lets a
        later pass resume exactly where the last stopped.

        Returns None on error, distinct from [] (no more comments). `after_utc` must be
        > 0: Arctic Shift rejects after=0 with a 400, so floor it at the post's time.
        """
        return await self._get(COMMENTS_URL, {"link_id": link_id, "after": after_utc,
                                              "sort": "asc", "limit": limit})
