"""TypeSafe Jev backend (https://docs.typesafe.ai).

Jev returns typed judgments (Noul = calibrated probability, Score = graded level with a
confidence, Choice = one option with probabilities) about a JSON `state`, never free
text. Each question is answered independently, which is why per-item judgments are
stable across repeats. Priced per input token only.
"""

import json
import os
import time

import httpx

from sentiment.backends.base import (ABOUT_FALSE, ABOUT_INSTRUCTIONS, ABOUT_TRUE,
                                     SENTIMENT_INSTRUCTIONS, SENTIMENT_LEVELS, JudgeBackend,
                                     Judgment, Target, Usage, clamp, label_instructions,
                                     target_instructions, target_options)

API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")


class JevBackend(JudgeBackend):
    name = "jev"

    def __init__(self, cfg, transport: httpx.BaseTransport | None = None,
                 api_key: str | None = None, retries: int = 2):
        super().__init__(cfg)
        key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY is not set (or use JUDGE_BACKEND=claude)")
        self._http = httpx.Client(timeout=20, transport=transport, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        self.retries = retries
        self.questions = self._judge_questions()
        self.target_question = {"target": {"type": "choice",
                                           "instructions": target_instructions(cfg),
                                           "criteria": target_options(cfg)}}

    @property
    def model(self) -> str:
        return self.cfg.judge["jev_model"]

    def _judge_questions(self) -> dict:
        q = {
            "about": {"type": "noul", "instructions": ABOUT_INSTRUCTIONS,
                      "criteria": {"true": ABOUT_TRUE, "false": ABOUT_FALSE}},
            "sentiment": {"type": "score", "instructions": SENTIMENT_INSTRUCTIONS,
                          "criteria": SENTIMENT_LEVELS},
        }
        for k, d in self.cfg.aspects.items():
            q[f"aspect_{k}"] = {"type": "noul", "instructions": label_instructions(d)}
        for k, t in self.cfg.themes.items():
            q[f"theme_{k}"] = {"type": "noul", "instructions": label_instructions(t["description"])}
        return q

    def _ask(self, state: dict, questions: dict) -> tuple[dict, Usage]:
        body = json.dumps({"state": state, "model": self.model, "questions": questions})
        self.stats["requests"] += 1
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(API_URL, content=body)
            except httpx.TransportError as e:
                last = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if r.status_code == 429:
                    self.stats["rate_limited"] += 1
                last = f"HTTP {r.status_code}"
                time.sleep(min(float(r.headers.get("retry-after") or 2 ** attempt), 10))
                continue
            r.raise_for_status()
            data = r.json()
            return data["answers"], Usage(data.get("usage", {}).get("input_tokens", 0), 0)
        raise RuntimeError(f"Jev request failed after {self.retries + 1} attempts: {last}")

    def infer_target(self, state):
        a, usage = self._ask(state, self.target_question)
        t = a["target"]
        return Target(t["choice"], clamp(t["probabilities"].get(t["choice"], 0))), usage

    def judge(self, state):
        a, usage = self._ask(state, self.questions)
        return Judgment(
            about=clamp(a["about"]["noul"]),
            sentiment=clamp(a["sentiment"]["score"], 0, 4),
            confidence=clamp(a["sentiment"]["confidence"]),
            aspects={k: round(clamp(a[f"aspect_{k}"]["noul"]), 3) for k in self.cfg.aspects},
            themes={k: round(clamp(a[f"theme_{k}"]["noul"]), 3) for k in self.cfg.themes},
        ), usage
