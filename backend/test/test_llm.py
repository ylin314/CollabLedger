"""Unit tests for LLM JSON parsing and retry behavior (no external service)."""

import json

import pytest

from backend.agent.config import AgentConfig
from backend.agent.llm import LLMClient, _extract_json
from backend.services import recommend as recommend_module


def test_extract_json_repairs_missing_comma_between_objects():
    raw = '{"reasons": [{"user_id": 1, "summary": "a"}\n{"user_id": 2, "summary": "b"}]}'
    data = _extract_json(raw)
    assert data["reasons"][1]["user_id"] == 2


def test_llm_json_retries_then_recovers(monkeypatch):
    calls = {"count": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"choices": [{"message": {"content": "not valid json"}}]}
            return {
                "choices": [
                    {"message": {"content": '{"scores": [{"user_id": 1, "skill": 0.5, "reason": "x"}]}'}}
                ]
            }

    def fake_post(url, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    data = recommend_module.llm_json("test", 5)
    assert data["scores"][0]["user_id"] == 1
    assert calls["count"] == 2


def test_llm_json_raises_after_two_parse_failures(monkeypatch):
    calls = {"count": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            calls["count"] += 1
            return {"choices": [{"message": {"content": "not valid json"}}]}

    def fake_post(url, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    with pytest.raises(json.JSONDecodeError):
        recommend_module.llm_json("test", 5)
    assert calls["count"] == 2


def test_complete_json_sends_response_format(monkeypatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}

    def fake_post(url, **kwargs):
        calls["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    client = LLMClient(AgentConfig(base_url="https://example.com", api_key="secret", model="m"))
    assert client.complete_json([{"role": "user", "content": "hi"}]) == {"ok": True}
    assert calls["kwargs"]["json"]["response_format"] == {"type": "json_object"}