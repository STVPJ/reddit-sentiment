"""Both judge backends against mocked HTTP: same questions in, same shape out."""
import json

import anthropic
import httpx
import httpx2
import pytest

from sentiment.backends import get_backend
from sentiment.backends.claude import ClaudeBackend
from sentiment.backends.jev import JevBackend

STATE = {"text": "Cursor invented an API", "context": {},
         "product": {"name": "Cursor", "also_known_as": ["Cursor"]}}


# ---------------------------------------------------------------- Jev
def jev_answers(cfg):
    a = {"about": {"noul": 0.91}, "sentiment": {"score": 0.6, "confidence": 0.8}}
    for k in cfg.aspects:
        a[f"aspect_{k}"] = {"noul": 0.7 if k == "accuracy" else 0.1}
    for k in cfg.themes:
        a[f"theme_{k}"] = {"noul": 0.95 if k == "hallucinated_apis" else 0.05}
    return a


def test_jev_judge(cfg):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req, body))
        return httpx.Response(200, json={"answers": jev_answers(cfg),
                                         "usage": {"input_tokens": 1500}})

    b = JevBackend(cfg, transport=httpx.MockTransport(handler), api_key="test-key")
    out, usage = b.judge_many([STATE])
    j = out[0]
    assert j.about == 0.91 and j.sentiment == 0.6 and j.confidence == 0.8
    assert j.aspects["accuracy"] == 0.7 and j.themes["hallucinated_apis"] == 0.95
    assert usage.input_tokens == 1500
    req, body = seen[0]
    assert req.headers["authorization"] == "Bearer test-key"
    assert body["model"] == cfg.judge["jev_model"] and body["state"] == STATE
    assert {"about", "sentiment", "aspect_pricing", "theme_vendor_conduct"} <= set(body["questions"])


def test_jev_target_and_retry(cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        q = json.loads(req.content)["questions"]["target"]
        assert q["type"] == "choice" and "none" in q["criteria"]
        return httpx.Response(200, json={"answers": {"target": {
            "choice": "Cursor", "probabilities": {"Cursor": 0.82, "none": 0.1}}}})

    b = JevBackend(cfg, transport=httpx.MockTransport(handler), api_key="k")
    out, _ = b.infer_targets([{"text": "it broke", "context": {}}])
    assert (out[0].choice, out[0].p) == ("Cursor", 0.82)
    assert b.stats["rate_limited"] == 1


def test_jev_client_error_returns_none(cfg):
    b = JevBackend(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401)),
                   api_key="k")
    out, _ = b.judge_many([STATE])
    assert out == [None] and b.stats["failed"] == 1


def test_jev_requires_key(cfg, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        JevBackend(cfg)


# ---------------------------------------------------------------- Claude
def claude_client(handler):
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(
                                   transport=httpx2.MockTransport(handler)))


def message(payload, stop_reason="end_turn"):
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if payload is not None:
        content.append({"type": "text", "text": json.dumps(payload)})
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 900, "output_tokens": 120}}


def test_claude_judge(cfg):
    seen = []
    payload = {"about": 0.88, "sentiment": 1, "confidence": 0.9,
               "aspects": {k: (0.8 if k == "accuracy" else 0.0) for k in cfg.aspects},
               "themes": {k: (0.9 if k == "hallucinated_apis" else 0.0) for k in cfg.themes}}

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=message(payload))

    b = ClaudeBackend(cfg, client=claude_client(handler))
    out, usage = b.judge_many([STATE])
    j = out[0]
    assert (j.about, j.sentiment, j.confidence) == (0.88, 1, 0.9)
    assert j.aspects["accuracy"] == 0.8 and j.themes["hallucinated_apis"] == 0.9
    assert (usage.input_tokens, usage.output_tokens) == (900, 120)
    body = seen[0]
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    schema = body["output_config"]["format"]["schema"]
    assert set(schema["properties"]["themes"]["required"]) == set(cfg.themes)
    assert "temperature" not in body and "tool_choice" not in body
    assert json.loads(body["messages"][0]["content"]) == STATE


def test_claude_target(cfg):
    def handler(req):
        enum = json.loads(req.content)["output_config"]["format"]["schema"][
            "properties"]["product"]["enum"]
        assert enum[-1] == "none" and "Cursor" in enum
        return httpx2.Response(200, json=message({"product": "Cursor", "probability": 1.4}))

    out, _ = ClaudeBackend(cfg, client=claude_client(handler)).infer_targets(
        [{"text": "it broke", "context": {}}])
    assert (out[0].choice, out[0].p) == ("Cursor", 1.0)     # clamped to [0, 1]


def test_claude_refusal_returns_none(cfg):
    b = ClaudeBackend(cfg, client=claude_client(
        lambda r: httpx2.Response(200, json=message(None, stop_reason="refusal"))))
    out, _ = b.judge_many([STATE])
    assert out == [None]


def test_get_backend_selects_by_config(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg.judge["backend"] = "claude"
    assert isinstance(get_backend(cfg), ClaudeBackend)
    cfg.judge["backend"] = "nope"
    with pytest.raises(ValueError):
        get_backend(cfg)
