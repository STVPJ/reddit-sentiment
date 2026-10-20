"""Inferred mentions, context separation and multi-label judging, with a fake backend."""
import json

from sentiment import collect, judge
from sentiment.backends import JudgeBackend, Judgment, Target, Usage


class FakeBackend(JudgeBackend):
    name = "fake"

    def __init__(self, cfg, targets=None, judgment=None, fail=()):
        super().__init__(cfg)
        self.targets, self.judgment, self.fail, self.seen = targets or {}, judgment, fail, {}

    @property
    def model(self):
        return "fake-1"

    def infer_target(self, state):
        self.seen[state["text"]] = state
        if state["text"] in self.fail:
            raise RuntimeError("boom")
        return Target(*self.targets[state["text"]]), Usage(10, 0)

    def judge(self, state):
        self.seen[state["text"]] = state
        if state["text"] in self.fail:
            raise RuntimeError("boom")
        return self.judgment, Usage(20, 5)


def seed_thread(con, cfg):
    con.execute("""INSERT INTO threads (reddit_id, subreddit, title, url, created_utc, selftext)
                   VALUES ('t3_q','webdev','Cursor pricing?','u',1,'what do you pay')""")
    pats = cfg.patterns()
    for rid, parent, text in [("t1_p", None, "Cursor charged us 40 a seat"),
                              ("t1_r", "t1_p", "it invented an API twice on me"),
                              ("t1_x", "t1_p", "lol"),
                              ("t1_y", None, "which one is best?")]:
        collect.add_item(con, cfg, pats, rid, "comment", "t3_q", text, 1, 2,
                         parent_id=parent, keep_unnamed=True)


def test_infer_targets(con, cfg):
    seed_thread(con, cfg)
    fb = FakeBackend(cfg, targets={"it invented an API twice on me": ("Cursor", 0.9),
                                   "lol": ("none", 0.95),
                                   "which one is best?": ("Cursor", 0.5)})
    asked, new, failed, usage = judge.infer_targets(con, cfg, fb)
    ms = {r[0]: r[1] for r in con.execute("SELECT item_id, source FROM mentions")}
    assert (asked, new, failed) == (3, 1, 0) and usage.input_tokens == 30
    assert ms == {"t1_p": "name", "t1_r": "inferred"}
    st = fb.seen["it invented an API twice on me"]
    # the parent comment is context, never part of the author's own text
    assert st["context"]["replying_to_comment"] == "Cursor charged us 40 a seat"
    assert "40" not in st["text"]


def test_failed_target_check_stays_queued(con, cfg):
    seed_thread(con, cfg)
    fb = FakeBackend(cfg, targets={"lol": ("none", 0.9), "which one is best?": ("none", 0.9)},
                     fail={"it invented an API twice on me"})
    _, _, failed, _ = judge.infer_targets(con, cfg, fb)
    assert failed == 1
    left = con.execute("SELECT reddit_id FROM items WHERE target_checked = 0 "
                       "AND reddit_id NOT IN (SELECT item_id FROM mentions)").fetchall()
    assert [r[0] for r in left] == ["t1_r"]


def test_judge_pending_multi_label(con, cfg):
    seed_thread(con, cfg)
    aspects = {k: (0.8 if k in ("pricing", "accuracy") else 0.1) for k in cfg.aspects}
    themes = {k: (0.9 if k == "pricing_and_limits" else 0.1) for k in cfg.themes}
    fb = FakeBackend(cfg, judgment=Judgment(0.9, 1.0, 0.85, aspects, themes))
    judged, failed, usage = judge.judge_pending(con, cfg, fb)
    assert (judged, failed) == (1, 0) and usage.output_tokens == 5
    r = con.execute("SELECT * FROM mentions WHERE item_id='t1_p'").fetchone()
    on = sorted(k for k, v in json.loads(r["aspects"]).items() if v >= cfg.label_min)
    assert on == ["accuracy", "pricing"] and r["aspect"] in on
    assert json.loads(r["themes"])["pricing_and_limits"] == 0.9
    assert r["model"] == "fake:fake-1" and r["sentiment"] == 1.0


def test_failed_judgment_is_retried_next_run(con, cfg):
    seed_thread(con, cfg)
    fb = FakeBackend(cfg, fail={"Cursor charged us 40 a seat"})
    judged, failed, _ = judge.judge_pending(con, cfg, fb)
    assert (judged, failed) == (0, 1)
    assert con.execute("SELECT count(*) FROM mentions WHERE judged_at IS NULL").fetchone()[0] == 1
