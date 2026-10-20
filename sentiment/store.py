"""SQLite store, shared with the dashboard (which reads it and writes only `annotations`)."""

import sqlite3
from pathlib import Path

from sentiment.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    reddit_id TEXT PRIMARY KEY,          -- t3_<id>
    subreddit TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    created_utc INTEGER NOT NULL,
    num_comments INTEGER DEFAULT 0,
    comments_fetched INTEGER DEFAULT 0,  -- 0 due · 1 first pass done · 2 both done · -1 not relevant
    comments_cursor INTEGER DEFAULT 0,   -- max created_utc of comments read so far
    selftext TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
    reddit_id TEXT PRIMARY KEY,          -- t3_ (post) or t1_ (comment)
    kind TEXT NOT NULL CHECK (kind IN ('post', 'comment')),
    thread_id TEXT NOT NULL REFERENCES threads(reddit_id),
    text TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    created_utc INTEGER NOT NULL,
    parent_id TEXT,
    target_checked INTEGER DEFAULT 0     -- 1 once "which product?" ran on an unnamed comment
);
CREATE TABLE IF NOT EXISTS mentions (
    item_id TEXT NOT NULL REFERENCES items(reddit_id),
    product TEXT NOT NULL,
    source TEXT DEFAULT 'name',          -- 'name' (regex found it) or 'inferred' (judge did)
    target_p REAL,                       -- "which product?" probability, inferred only
    about REAL,                          -- P(the text is really about this product)
    sentiment REAL,                      -- 0 strongly negative .. 4 strongly positive
    sentiment_conf REAL,
    aspect TEXT,                         -- top aspect, or 'general'
    aspects TEXT,                        -- JSON {aspect: p}
    themes TEXT,                         -- JSON {theme: p}
    model TEXT,
    judged_at TEXT,
    PRIMARY KEY (item_id, product)
);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    competitor_name TEXT DEFAULT NULL,
    label TEXT NOT NULL,
    description TEXT DEFAULT '',
    url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,                -- 'demo' = '1' when seeded with synthetic data
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_utc);
CREATE INDEX IF NOT EXISTS idx_mentions_unjudged ON mentions(judged_at);
CREATE INDEX IF NOT EXISTS idx_annotations_date ON annotations(event_date);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def is_demo(con) -> bool:
    row = con.execute("SELECT value FROM meta WHERE key = 'demo'").fetchone()
    return bool(row and row[0] == "1")
