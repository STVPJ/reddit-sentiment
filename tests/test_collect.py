"""Two-pass, paged comment fetch against a fake Arctic Shift (no network)."""
import asyncio
import time

from sentiment import collect


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    async def fetch_subreddit_posts(self, *a):
        return []

    async def fetch_comments_after(self, link_id, after):
        self.calls.append(after)
        return self.pages.pop(0) if self.pages else []

    async def close(self):
        pass


def run_collect(con, cfg, pages, state, cursor=0, age_h=48):
    created = int(time.time()) - age_h * 3600     # 48 h: first pass due, second not yet
    con.execute("""INSERT INTO threads (reddit_id, subreddit, title, url, created_utc,
                   num_comments, comments_fetched, comments_cursor)
                   VALUES ('t3_z','webdev','T','u',?,9,?,?)""", (created, state, cursor))
    fake = FakeClient(pages)
    cfg.subreddits = []
    asyncio.run(collect.collect(con, cfg, lambda m: None, client=fake))
    row = con.execute("SELECT comments_fetched, comments_cursor FROM threads").fetchone()
    n = con.execute("SELECT count(*) FROM items WHERE kind='comment'").fetchone()[0]
    return tuple(row), n, fake.calls, created


def test_pages_forward_from_post_time(con, cfg):
    t0 = int(time.time()) - 40 * 3600
    page1 = [{"id": f"a{i}", "body": "Cursor is great", "created_utc": t0 + i} for i in range(100)]
    page2 = [{"id": "b1", "body": "Cursor is bad", "created_utc": t0 + 500}]
    (st, cur), n, calls, created = run_collect(con, cfg, [page1, page2], state=0)
    assert (st, cur, n) == (1, t0 + 500, 101)
    assert len(calls) == 2 and calls[1] == t0 + 99 - 1
    # first page starts at the post's own time, never 0 (Arctic Shift 400s on after=0)
    assert calls[0] == created - 1
    assert con.execute("SELECT count(*) FROM mentions").fetchone()[0] == 101


def test_outage_keeps_thread_queued(con, cfg):
    (st, cur), n, _, _ = run_collect(con, cfg, [None], state=1, cursor=123, age_h=96)
    assert st == 1 and n == 0


def test_unnamed_comments_are_kept_for_inference(con, cfg):
    t0 = int(time.time()) - 40 * 3600
    page = [{"id": "x", "body": "it made up a method again", "created_utc": t0}]
    _, n, _, _ = run_collect(con, cfg, [page], state=0)
    assert n == 1
    assert con.execute("SELECT count(*) FROM mentions").fetchone()[0] == 0
