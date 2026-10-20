"""Collect posts + comments from the configured subreddits via Arctic Shift.

Posts are read for a rolling window. Comments are read in two passes per thread, at
`comment_delay_h` (24 h) and again at `second_pass_h` (72 h). Each pass pages forward
from the thread's `comments_cursor`, oldest first, so the first pass reads the whole
discussion so far and the second picks up exactly what was posted since.

Posts are stored as items only when they name a product. EVERY comment in a relevant
thread is stored (with its parent id), so the "which product?" check can find mentions
that don't use a name ("it invented a method that doesn't exist", replying to a comment
about one product). Every thread is stored with its title and body as context.
"""

import time
from datetime import datetime, timedelta, timezone

from sentiment import arctic_shift
from sentiment.config import Config


def _day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def add_item(con, cfg: Config, pats, reddit_id, kind, thread_id, text, score, created,
             parent_id=None, keep_unnamed=False) -> int:
    """Store an item + its named product mentions. Returns the number of mentions.

    Items naming no product are kept only with keep_unnamed (comments in relevant
    threads), for the inferred-mention check.
    """
    products = cfg.find_mentions(text, pats)
    if not products and not keep_unnamed:
        return 0
    con.execute("""INSERT OR IGNORE INTO items
                   (reddit_id, kind, thread_id, text, score, created_utc, parent_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (reddit_id, kind, thread_id, text, score, created, parent_id))
    for p in products:
        con.execute("INSERT OR IGNORE INTO mentions (item_id, product) VALUES (?,?)",
                    (reddit_id, p))
    return len(products)


async def collect(con, cfg: Config, log, window_days: int | None = None,
                  max_threads: int | None = None, client=None) -> dict:
    c = cfg.collection
    window_days = window_days or c["post_window_days"]
    max_threads = max_threads or c["max_comment_threads"]
    pats = cfg.patterns()
    now = datetime.now(timezone.utc)
    after, before = _day(now - timedelta(days=window_days)), _day(now + timedelta(days=1))
    client = client or arctic_shift.ArcticShiftClient()
    stats = {"posts": 0, "post_mentions": 0, "threads_commented": 0,
             "threads_second_pass": 0, "comments_stored": 0, "comment_mentions": 0,
             "comment_fetch_errors": 0}
    try:
        for sub in cfg.subreddits:
            raw = await client.fetch_subreddit_posts(sub, after, before)
            for d in raw:
                p = arctic_shift.normalize_post(d)
                if not p:
                    continue
                stats["posts"] += 1
                relevant = bool(cfg.relevance_re.search(f"{p['title']}\n{p['selftext']}"))
                con.execute("""INSERT INTO threads (reddit_id, subreddit, title, url,
                               created_utc, num_comments, comments_fetched, selftext)
                               VALUES (?,?,?,?,?,?,?,?)
                               ON CONFLICT(reddit_id) DO UPDATE SET
                               num_comments = excluded.num_comments,
                               selftext = excluded.selftext""",
                            (p["reddit_id"], p["subreddit"], p["title"], p["url"],
                             p["created_utc"], p["num_comments"], 0 if relevant else -1,
                             p["selftext"][:4000]))
                stats["post_mentions"] += add_item(
                    con, cfg, pats, p["reddit_id"], "post", p["reddit_id"],
                    f"{p['title']}\n{p['selftext']}", p["score"], p["created_utc"])
            log(f"  r/{sub}: {len(raw)} posts")
        con.commit()

        now_ts = int(time.time())
        second_h = c["second_pass_h"]
        for state, age_h in ((0, c["comment_delay_h"]), (1, second_h)):
            due = con.execute("""SELECT reddit_id, comments_cursor, created_utc FROM threads
                                 WHERE comments_fetched = ? AND created_utc < ?
                                 AND num_comments > 0
                                 ORDER BY created_utc DESC LIMIT ?""",
                              (state, now_ts - age_h * 3600, max_threads)).fetchall()
            for t in due:
                # Floor at the post's own time: Arctic Shift rejects after=0.
                cursor = max(t["comments_cursor"] or 0, t["created_utc"])
                failed = False
                for _ in range(c["max_pages_per_thread"]):
                    # cursor - 1: a same-second comment is re-read rather than skipped
                    # (items are INSERT OR IGNORE, so re-reads are harmless).
                    page = await client.fetch_comments_after(t["reddit_id"], cursor - 1)
                    if page is None:
                        failed = True     # outage: keep the thread queued, keep progress
                        break
                    for cm in page:
                        cursor = max(cursor, int(cm.get("created_utc") or 0))
                        body = cm.get("body") or ""
                        if not cm.get("id") or body in ("[deleted]", "[removed]"):
                            continue
                        stats["comment_mentions"] += add_item(
                            con, cfg, pats, f"t1_{cm['id']}", "comment", t["reddit_id"],
                            body, cm.get("score") or 0, int(cm.get("created_utc") or 0),
                            parent_id=cm.get("parent_id"), keep_unnamed=True)
                        stats["comments_stored"] += 1
                    if len(page) < arctic_shift.PAGE_LIMIT:
                        break
                # A thread already past the second pass at its first read (every
                # backfilled thread) has just been read in full: count both passes.
                done = 2 if (state == 0 and t["created_utc"] < now_ts - second_h * 3600) \
                    else state + 1
                con.execute("""UPDATE threads SET comments_fetched = ?, comments_cursor = ?
                               WHERE reddit_id = ?""",
                            (state if failed else done, cursor, t["reddit_id"]))
                if failed:
                    stats["comment_fetch_errors"] += 1
                else:
                    stats["threads_commented" if state == 0 else "threads_second_pass"] += 1
        con.commit()
    finally:
        await client.close()
    return stats
