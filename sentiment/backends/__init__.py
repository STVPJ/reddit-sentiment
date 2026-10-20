"""Pluggable judge backends. Both answer the same fixed questions with the same output
shape, so the rest of the pipeline doesn't know which one ran.

    JUDGE_BACKEND=jev     TypeSafe Jev: a typed-judgment model (needs TYPESAFE_API_KEY)
    JUDGE_BACKEND=claude  Claude with structured output (needs ANTHROPIC_API_KEY)
"""

from sentiment.backends.base import JudgeBackend, Judgment, Target, Usage
from sentiment.config import Config


def get_backend(cfg: Config) -> JudgeBackend:
    name = cfg.judge["backend"]
    if name == "jev":
        from sentiment.backends.jev import JevBackend
        return JevBackend(cfg)
    if name == "claude":
        from sentiment.backends.claude import ClaudeBackend
        return ClaudeBackend(cfg)
    raise ValueError(f"unknown judge backend {name!r} (expected 'jev' or 'claude')")


__all__ = ["JudgeBackend", "Judgment", "Target", "Usage", "get_backend"]
