"""Load config.yaml: the single source for products, subreddits, labels and thresholds.

The dashboard reads the same file, so the two can never count different mentions.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
DEMO_CONFIG = DEFAULT_CONFIG.with_name("config.demo.yaml")

THRESHOLD_KEYS = ("about_min", "conf_min", "label_min", "target_min", "min_n")


@dataclass
class Config:
    title: str
    domain: str
    primary_product: str
    products: dict[str, list[str]]           # canonical name -> surface forms
    none_choice: str
    subreddits: list[str]
    relevance_re: re.Pattern
    aspects: dict[str, str]                  # key -> description
    themes: dict[str, dict]                  # key -> {label, description}
    thresholds: dict[str, float]
    collection: dict[str, int]
    judge: dict = field(default_factory=dict)

    @property
    def about_min(self) -> float:
        return self.thresholds["about_min"]

    @property
    def conf_min(self) -> float:
        return self.thresholds["conf_min"]

    @property
    def label_min(self) -> float:
        return self.thresholds["label_min"]

    @property
    def target_min(self) -> float:
        return self.thresholds["target_min"]

    def patterns(self) -> dict[str, re.Pattern]:
        return {name: re.compile(r"(?<![\w])(" + "|".join(re.escape(f) for f in forms)
                                 + r")(?![\w])", re.I)
                for name, forms in self.products.items()}

    def find_mentions(self, text: str, pats: dict[str, re.Pattern] | None = None) -> list[str]:
        pats = pats or self.patterns()
        return [name for name, p in pats.items() if p.search(text or "")]


def read_raw(path: Path) -> dict:
    """YAML with an optional `extends: <file>` (relative to this one); top-level keys
    here replace the base file's."""
    raw = yaml.safe_load(path.read_text()) or {}
    base = raw.pop("extends", None)
    if base:
        raw = {**read_raw(path.parent / base), **raw}
    return raw


def load(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG)
    raw = read_raw(path)

    products: dict[str, list[str]] = {}
    for p in raw["products"]:
        name = p["name"]
        forms = {name, *(p.get("aliases") or [])}
        products[name] = sorted(forms, key=len, reverse=True)
    if not products:
        raise ValueError("config: at least one product is required")

    primary = raw.get("primary_product") or next(iter(products))
    if primary not in products:
        raise ValueError(f"config: primary_product {primary!r} is not in products")

    thresholds = {k: float(v) for k, v in (raw.get("thresholds") or {}).items()}
    missing = [k for k in THRESHOLD_KEYS if k not in thresholds]
    if missing:
        raise ValueError(f"config: thresholds missing {missing}")
    for k in THRESHOLD_KEYS[:4]:
        if not 0 <= thresholds[k] <= 1:
            raise ValueError(f"config: thresholds.{k} must be between 0 and 1")

    themes = {}
    for key, t in (raw.get("themes") or {}).items():
        if isinstance(t, str):
            t = {"description": t}
        themes[key] = {"label": t.get("label") or key.replace("_", " ").capitalize(),
                       "description": t["description"]}

    for key in [*raw.get("aspects", {}), *themes]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"config: label key {key!r} must be lower_snake_case")

    judge = dict(raw.get("judge") or {})
    judge["backend"] = os.environ.get("JUDGE_BACKEND") or judge.get("backend", "jev")
    judge["claude_model"] = (os.environ.get("CLAUDE_MODEL")
                             or judge.get("claude_model", "claude-haiku-5-5"))
    judge.setdefault("jev_model", "jev-1.13.0")
    judge.setdefault("workers", 6)

    return Config(
        title=raw.get("title", "Reddit Sentiment"),
        domain=raw.get("domain", "product"),
        primary_product=primary,
        products=products,
        none_choice=raw.get("none_choice", "None of the listed products."),
        subreddits=list(raw.get("subreddits") or []),
        relevance_re=re.compile(raw["relevance_regex"], re.I),
        aspects=dict(raw["aspects"]),
        themes=themes,
        thresholds=thresholds,
        collection={"post_window_days": 3, "comment_delay_h": 24, "second_pass_h": 72,
                    "max_comment_threads": 200, "max_pages_per_thread": 5,
                    **(raw.get("collection") or {})},
        judge=judge,
    )


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or "./data")


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH") or data_dir() / "sentiment.db")
