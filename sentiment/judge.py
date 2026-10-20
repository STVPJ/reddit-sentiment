"""Pipeline stages that call the judge: infer unnamed mentions, then judge every mention.

Named mentions are found in code (config.find_mentions). The judge only answers fixed
questions about one (text, product) at a time; it never decides what happens next.
"""

import json
from datetime import datetime

from sentiment.backends import JudgeBackend, Usage
from sentiment.config import Config


def context_for(con, thread_id: str, parent_id: str | None) -> dict:
    """Thread title, post excerpt and the replied-to comment, for comments."""
    t = con.execute("SELECT title, selftext FROM threads WHERE reddit_id = ?",
                    (thread_id,)).fetchone()
    ctx = {"reddit_thread_title": t["title"] if t else "",
           "original_post_excerpt": (t["selftext"] or "")[:1000] if t else ""}
    if parent_id and parent_id.startswith("t1_"):
        p = con.execute("SELECT text FROM items WHERE reddit_id = ?", (parent_id,)).fetchone()
        if p:
            ctx["replying_to_comment"] = p["text"][:1000]
    return ctx


def state_for(con, cfg: Config, item, product: str) -> dict:
    """`text` = only the author's own words; `context` = what they're replying within.

    Kept apart so labels describe the author's text, not the parent comment's topics.
    """
    context = context_for(con, item["thread_id"], item["parent_id"]) \
        if item["kind"] == "comment" else {}
    return {"text": item["text"][:3000], "context": context,
            "product": {"name": product,
                        "also_known_as": cfg.products.get(product, [product])}}


def infer_targets(con, cfg: Config, backend: JudgeBackend,
                  limit: int = 300) -> tuple[int, int, int, Usage]:
    """"Which product?" on comments that name none.

    Returns (asked, new_mentions, failed, usage). Failed comments stay unchecked and are
    retried next run, so a caller looping over batches must stop when a batch fails or
    it would re-ask the same comments forever.
    """
    rows = con.execute("""
        SELECT i.* FROM items i
        WHERE i.kind = 'comment' AND i.target_checked = 0
          AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.item_id = i.reddit_id)
        LIMIT ?""", (limit,)).fetchall()
    if not rows:
        return 0, 0, 0, Usage()
    states = [{"text": r["text"][:3000],
               "context": context_for(con, r["thread_id"], r["parent_id"])} for r in rows]
    answers, usage = backend.infer_targets(states)
    new = failed = 0
    for r, t in zip(rows, answers):
        if t is None:
            failed += 1
            continue
        if t.choice != "none" and t.choice in cfg.products and t.p >= cfg.target_min:
            con.execute("""INSERT OR IGNORE INTO mentions (item_id, product, source, target_p)
                           VALUES (?,?,'inferred',?)""", (r["reddit_id"], t.choice, t.p))
            new += 1
        con.execute("UPDATE items SET target_checked = 1 WHERE reddit_id = ?",
                    (r["reddit_id"],))
    con.commit()
    return len(rows), new, failed, usage


def judge_pending(con, cfg: Config, backend: JudgeBackend,
                  limit: int = 300) -> tuple[int, int, Usage]:
    """Judge unjudged mentions. Returns (judged, failed, usage)."""
    rows = con.execute("""
        SELECT m.product, i.* FROM mentions m JOIN items i ON i.reddit_id = m.item_id
        WHERE m.judged_at IS NULL LIMIT ?""", (limit,)).fetchall()
    if not rows:
        return 0, 0, Usage()
    answers, usage = backend.judge_many([state_for(con, cfg, r, r["product"]) for r in rows])
    now = datetime.now().isoformat(timespec="seconds")
    judged = failed = 0
    for r, j in zip(rows, answers):
        if j is None:
            failed += 1          # stays unjudged; retried next run
            continue
        top = max(j.aspects, key=j.aspects.get) if j.aspects else None
        con.execute("""UPDATE mentions SET about=?, sentiment=?, sentiment_conf=?, aspect=?,
                       aspects=?, themes=?, model=?, judged_at=?
                       WHERE item_id=? AND product=?""",
                    (j.about, j.sentiment, j.confidence,
                     top if top and j.aspects[top] >= cfg.label_min else "general",
                     json.dumps(j.aspects), json.dumps(j.themes),
                     f"{backend.name}:{backend.model}", now, r["reddit_id"], r["product"]))
        judged += 1
    con.commit()
    return judged, failed, usage
