"""Synthetic demo data so the dashboard works with no API keys.

Seeded with the FICTIONAL products in config.demo.yaml, never real ones: invented scores
for real vendors would read as findings about real companies. The database is flagged
(meta.demo = 1) so the dashboard shows a "Synthetic demo data" banner, and a real run
refuses to write into it.

Everything here is invented: thread titles are prefixed "[demo]", comments are generated
from templates, there are no usernames, and links point at example.com. The judgments
are generated too (model "demo"), including some off-topic and low-confidence rows so the
counting gates have something to drop. Written for the example config (AI coding
assistants) but it reads product, aspect and theme names from whatever config is loaded.
"""

import json
import math
import random
import time
from datetime import datetime, timezone

from sentiment.config import Config

# theme -> (aspect it implies, negative snippets, positive snippets). {p} = product.
SNIPPETS = {
    "code_quality": ("accuracy",
        ["{p} keeps writing code that compiles but quietly does the wrong thing.",
         "Half of what {p} generates needs rewriting before review. Not saving me time."],
        ["{p} writes cleaner tests than I do, honestly.",
         "The refactors {p} suggests are usually what I'd have done, just faster."]),
    "hallucinated_apis": ("accuracy",
        ["{p} invented a config flag that has never existed. Lost an hour to it.",
         "Asked {p} for a migration and it called three library methods that aren't real."],
        ["Surprised how rarely {p} makes up APIs now compared with last year.",
         "{p} actually checked the installed version before suggesting the call. Nice."]),
    "pricing_and_limits": ("pricing",
        ["Hit the {p} usage cap by lunchtime again. The new limits are rough.",
         "The {p} price rise is hard to justify for a small team."],
        ["For what it costs, {p} pays for itself in a week.",
         "{p}'s team plan is the best value of the ones we trialled."]),
    "speed_and_outages": ("reliability",
        ["{p} was down most of the morning and nobody said anything.",
         "Completions from {p} take so long I've already typed the line myself."],
        ["{p} has been rock solid for us this quarter.",
         "Latency on {p} is noticeably better since the last update."]),
    "context_and_large_codebases": ("accuracy",
        ["{p} loses track of anything outside the open file in our monorepo.",
         "On a large codebase {p} confidently edits the wrong module."],
        ["{p} understood the whole repo layout without me pointing at files.",
         "Being able to give {p} the full project context is a game changer for us."]),
    "ide_and_workflow": ("usability",
        ["The {p} plugin keeps fighting with my formatter.",
         "{p}'s review workflow adds more clicks than it saves."],
        ["{p} slots into my editor and terminal without any fuss.",
         "Love that {p} opens a proper diff I can review before applying."]),
    "privacy_and_security": (None,
        ["Our security team blocked {p} until they get clarity on code retention.",
         "{p} suggested hardcoding a secret into the config. Yikes."],
        ["{p}'s no-training option is what got it past our compliance review.",
         "Appreciate that {p} flags insecure patterns in what it suggests."]),
    "vs_general_chat_ai": ("accuracy",
        ["Honestly pasting into a general chat model gives better answers than {p}.",
         "{p} feels like a thin wrapper around a chat model with a worse prompt."],
        ["{p} beats copy-pasting into a chat window because it sees the actual code.",
         "Switched from a browser chatbot to {p} and won't go back."]),
    "vendor_conduct": ("support",
        ["{p} changed the plan terms with a week's notice. Not impressed.",
         "Support for {p} took nine days to answer a billing question."],
        ["{p}'s team fixed the bug I reported within a day.",
         "Good to see {p} publishing a clear changelog and roadmap."]),
}
GENERIC_NEG = ["{p} has been disappointing lately."]
GENERIC_POS = ["{p} is genuinely good."]
OFF_TOPIC = ["Anyone know a good cursor theme for dark mode?",
             "Job post: we're hiring, experience with {p} a plus.",
             "Is {p} the one with the purple logo?"]
TITLES = ["Which AI coding tool are you actually paying for?",
          "Weekly tools thread", "{p} after three months: honest review",
          "Is it just me or has {p} got worse?", "Switched to {p}, some thoughts",
          "AI assistants on a large legacy codebase", "Team rollout of {p}: lessons learned",
          "How do you review AI-generated code?"]
INFERRED = ["it did exactly this to me too, wrong import every time",
            "their pricing page changed again last week",
            "it's been much faster for me since the update"]
UNNAMED = ["lol", "this is the way", "which one is that?", "following"]

# Per-product personality: (share of volume, mean sentiment 0..4, drift over the window)
PROFILE = [(0.30, 1.6, +0.4), (0.24, 2.2, -0.3), (0.20, 2.4, +0.2),
           (0.10, 1.8, 0.0), (0.09, 1.7, +0.3), (0.07, 2.0, -0.2)]

DEMO_IDS = "reddit_id LIKE 't%\\_demo%' ESCAPE '\\'"


def _level(rng, mean: float) -> float:
    lvl = min(4, max(0, round(rng.gauss(mean, 1.1))))
    return round(min(4, max(0, lvl + rng.uniform(-0.25, 0.25))), 2)


def _clear(con) -> None:
    """Remove earlier demo rows (only rows with demo ids), so re-seeding is idempotent."""
    con.execute("DELETE FROM mentions WHERE item_id IN "
                f"(SELECT reddit_id FROM items WHERE {DEMO_IDS})")
    con.execute(f"DELETE FROM items WHERE {DEMO_IDS}")
    con.execute(f"DELETE FROM threads WHERE {DEMO_IDS}")
    con.execute("DELETE FROM annotations WHERE label LIKE '[demo]%'")
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('demo', '1')")


def seed(con, cfg: Config, days: int = 90, seed: int = 7) -> dict:
    rng = random.Random(seed)
    products = list(cfg.products)
    profiles = {p: PROFILE[i % len(PROFILE)] for i, p in enumerate(products)}
    weights = [profiles[p][0] for p in products]
    themes = list(cfg.themes)
    aspects = list(cfg.aspects)
    now = int(time.time())
    start = now - days * 86400
    primary = cfg.primary_product
    dip_day = days * 0.55          # the primary product gets a visible dip and recovery
    judged_at = datetime.now().isoformat(timespec="seconds")
    counts = {"threads": 0, "items": 0, "mentions": 0}
    _clear(con)

    def mean_for(p, day):
        _, base, drift = profiles[p]
        m = base + drift * (day / days)
        if p == primary:
            m -= 1.0 * math.exp(-((day - dip_day) / 5) ** 2)
        return m

    def add_mention(item_id, p, day, theme, source="name", target_p=None,
                    about=None, conf=None):
        about = about if about is not None else round(rng.uniform(0.65, 0.98), 3)
        conf = conf if conf is not None else round(rng.uniform(0.62, 0.95), 3)
        asp = {a: round(rng.uniform(0.02, 0.3), 3) for a in aspects}
        implied = SNIPPETS.get(theme, (None,))[0]
        if implied in asp:
            asp[implied] = round(rng.uniform(0.7, 0.97), 3)
        if aspects and rng.random() < 0.25:   # some mentions touch a second aspect
            asp[rng.choice(aspects)] = round(rng.uniform(0.55, 0.9), 3)
        th = {t: round(rng.uniform(0.01, 0.25), 3) for t in themes}
        if theme in th:
            th[theme] = round(rng.uniform(0.7, 0.98), 3)
        top = max(asp, key=asp.get) if asp else None
        con.execute("""INSERT OR IGNORE INTO mentions (item_id, product, source, target_p,
                       about, sentiment, sentiment_conf, aspect, aspects, themes, model,
                       judged_at) VALUES (?,?,?,?,?,?,?,?,?,?,'demo',?)""",
                    (item_id, p, source, target_p, about, _level(rng, mean_for(p, day)), conf,
                     top if top and asp[top] >= cfg.label_min else "general",
                     json.dumps(asp), json.dumps(th), judged_at))
        counts["mentions"] += 1

    def text_for(p, day, theme):
        neg, pos = SNIPPETS[theme][1:] if theme in SNIPPETS else (GENERIC_NEG, GENERIC_POS)
        good = rng.random() < mean_for(p, day) / 4
        return rng.choice(pos if good else neg).format(p=p)

    n_item = 0
    for day in range(days):
        for _ in range(rng.randint(2, 5)):
            n_t = counts["threads"]
            p = rng.choices(products, weights)[0]
            created = start + day * 86400 + rng.randint(0, 80000)
            tid = f"t3_demo{n_t:05d}"
            title = "[demo] " + rng.choice(TITLES).format(p=p)
            n_comments = rng.randint(1, 6)
            con.execute("""INSERT INTO threads (reddit_id, subreddit, title, url, created_utc,
                           num_comments, comments_fetched, comments_cursor, selftext)
                           VALUES (?,?,?,?,?,?,2,?,?)""",
                        (tid, rng.choice(cfg.subreddits or ["demo"]), title,
                         f"https://example.com/demo/thread/{n_t}", created, n_comments,
                         created, "Synthetic demo thread. Not real Reddit data."))
            counts["threads"] += 1
            if p in title:
                theme = rng.choice(themes) if themes else None
                con.execute("""INSERT INTO items (reddit_id, kind, thread_id, text, score,
                               created_utc) VALUES (?,?,?,?,?,?)""",
                            (tid, "post", tid, f"{title}\n{text_for(p, day, theme)}",
                             rng.randint(1, 400), created))
                counts["items"] += 1
                add_mention(tid, p, day, theme)

            parent = None
            for _ in range(n_comments):
                n_item += 1
                cid = f"t1_demo{n_item:06d}"
                c_created = min(now - 60, created + rng.randint(600, 3 * 86400))
                cday = (c_created - start) / 86400
                q = rng.choices(products, weights)[0] if rng.random() < 0.6 else p
                theme = rng.choice(themes) if themes else None
                roll = rng.random()
                if roll < 0.72:                       # names a product
                    text = text_for(q, cday, theme)
                    add = [(q, "name", None, None, None)]
                    others = [x for x in products if x != q]
                    if others and rng.random() < 0.15:  # a comparison: two products
                        q2 = rng.choice(others)
                        text += " " + text_for(q2, cday, theme)
                        add.append((q2, "name", None, None, None))
                elif roll < 0.80:                     # off-topic or unsure: gated out
                    text = rng.choice(OFF_TOPIC).format(p=q)
                    off = rng.random() < 0.6
                    add = [(q, "name", None,
                            round(rng.uniform(0.03, 0.4), 3) if off else None,
                            None if off else round(rng.uniform(0.3, 0.55), 3))]
                elif roll < 0.88 and parent:          # unnamed reply, product inferred
                    text = rng.choice(INFERRED)
                    add = [(q, "inferred", round(rng.uniform(0.72, 0.95), 3), None, None)]
                else:                                 # unnamed noise, checked and skipped
                    text = rng.choice(UNNAMED)
                    add = []
                con.execute("""INSERT INTO items (reddit_id, kind, thread_id, text, score,
                               created_utc, parent_id, target_checked)
                               VALUES (?,?,?,?,?,?,?,1)""",
                            (cid, "comment", tid, text, rng.randint(-3, 120), c_created,
                             parent if rng.random() < 0.5 else None))
                counts["items"] += 1
                for prod, source, tp, about, conf in add:
                    add_mention(cid, prod, cday, theme, source, tp, about, conf)
                parent = cid

    dip_date = datetime.fromtimestamp(start + dip_day * 86400, tz=timezone.utc)
    con.execute("""INSERT INTO annotations (event_date, competitor_name, label, description)
                   VALUES (?,?,?,?)""",
                (dip_date.strftime("%Y-%m-%d"), primary, "[demo] Plan limits changed",
                 "Fictional event to show how annotations line up with a dip."))
    con.commit()
    return counts
