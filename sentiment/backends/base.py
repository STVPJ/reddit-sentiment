"""The judge interface and the wording of the questions both backends ask."""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sentiment.config import Config

SENTIMENT_LEVELS = [
    "Strongly negative: says it is wrong, unreliable, overpriced, not worth using, "
    "or warns others off it.",
    "Somewhat negative: complaints or reservations outweigh any praise.",
    "Neutral: factual, balanced, undecided, or asks a question without an opinion.",
    "Somewhat positive: mostly favourable, with minor reservations.",
    "Strongly positive: praises it clearly or recommends it to others.",
]

ABOUT_INSTRUCTIONS = ("Does `text` give an opinion about, or describe experience with, the "
                      "product named in `product`? `context` is only there to resolve what "
                      "`text` refers to.")
ABOUT_TRUE = ("The author says something about how that product works, performs, costs or "
              "compares, from use, testing or a clear opinion.")
ABOUT_FALSE = ("The product is only named in passing, listed, asked about without any "
               "opinion, or the word means something else (e.g. a mouse cursor), or the "
               "text is about something else entirely.")
SENTIMENT_INSTRUCTIONS = "How does the author of `text` feel about the product named in `product`?"


def label_instructions(description: str) -> str:
    return (f"Does `text` itself discuss {description}, for the product named in "
            f"`product`? Ignore topics that appear only in `context`.")


def target_instructions(cfg: Config) -> str:
    return (f"Which {cfg.domain} is the author of `text` talking about? Use `context` (the "
            f"thread, original post and the comment `text` replies to) only to resolve "
            f"words like \"it\" or \"their tool\".")


def target_options(cfg: Config) -> dict[str, str]:
    """choice -> description, ending with 'none'."""
    out = {}
    for name, forms in cfg.products.items():
        other = [f for f in forms if f != name]
        out[name] = f"{name} (also called: {', '.join(other)[:200]})" if other else name
    out["none"] = cfg.none_choice
    return out


@dataclass
class Judgment:
    about: float                  # P(text is really about the product)
    sentiment: float              # 0 strongly negative .. 4 strongly positive
    confidence: float             # confidence in the sentiment
    aspects: dict[str, float]     # aspect -> P(present)
    themes: dict[str, float]      # theme -> P(present)


@dataclass
class Target:
    choice: str                   # a product name or "none"
    p: float


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __iadd__(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        return self


def clamp(x, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, float(x)))


class JudgeBackend(ABC):
    """One request per item. Subclasses implement the two single-item calls; the base
    class runs them in parallel and turns any failure into None (retried next run)."""

    name = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workers = int(cfg.judge.get("workers", 6))
        self.stats = {"requests": 0, "rate_limited": 0, "failed": 0}

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def infer_target(self, state: dict) -> tuple[Target, Usage]:
        """Which product (or 'none') is this comment about? Raises on failure."""

    @abstractmethod
    def judge(self, state: dict) -> tuple[Judgment, Usage]:
        """About / sentiment / aspects / themes for one (text, product). Raises on failure."""

    def _many(self, fn, states: list[dict]) -> tuple[list, Usage]:
        total = Usage()
        if not states:
            return [], total

        def one(state):
            try:
                return fn(state)
            except Exception as e:
                self.stats["failed"] += 1
                print(f"  {self.name}: request failed: {e}", flush=True)
                return None, Usage()

        with ThreadPoolExecutor(min(self.workers, len(states))) as pool:
            results = list(pool.map(one, states))
        for _, u in results:
            total += u
        return [r for r, _ in results], total

    def infer_targets(self, states: list[dict]) -> tuple[list[Target | None], Usage]:
        return self._many(self.infer_target, states)

    def judge_many(self, states: list[dict]) -> tuple[list[Judgment | None], Usage]:
        return self._many(self.judge, states)
