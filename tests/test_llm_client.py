"""LLM client, offline: the Anthropic SDK is replaced by a fake that records each request."""
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import anthropic
import pytest
from pydantic import BaseModel

from pipeline.ingest.callout_sources import CalloutReport
from pipeline.llm import client
from pipeline.llm.callout_extractor import CalloutExtraction, extract_callout

TZ = ZoneInfo("America/New_York")


class Small(BaseModel):
    name: str
    count: int


class FakeClaude:
    """Stands in for anthropic.Anthropic: returns the queued replies and keeps every request."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = NS(messages=NS(parse=self._parse))

    def __call__(self, **kwargs):
        return self

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def ok(obj):
    return NS(stop_reason="end_turn", parsed_output=obj)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LLM_MODE", raising=False)


def fake(monkeypatch, *replies):
    f = FakeClaude(replies)
    monkeypatch.setattr(anthropic, "Anthropic", f)
    return f


def test_structured_output_comes_back_and_is_cached(monkeypatch):
    f = fake(monkeypatch, ok(Small(name="Ana", count=2)))
    content = [{"type": "text", "text": "hi"}]
    assert client.extract("t", "r1", "sys", content, Small) == Small(name="Ana", count=2)
    assert client.extract("t", "r1", "sys", content, Small).count == 2      # second time: from the cache
    assert len(f.calls) == 1
    assert f.calls[0]["output_format"] is Small and f.calls[0]["model"] == client.MODEL


def test_refusal_or_missing_output_is_held_not_guessed(monkeypatch):
    fake(monkeypatch, NS(stop_reason="refusal", parsed_output=None), NS(stop_reason="max_tokens", parsed_output=None))
    with pytest.raises(client.LLMUnavailable, match="declined"):
        client.extract("t", "r1", "sys", [{"type": "text", "text": "a"}], Small)
    with pytest.raises(client.LLMUnavailable, match="no structured output"):
        client.extract("t", "r2", "sys", [{"type": "text", "text": "b"}], Small)


def test_off_and_replay_modes(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client.mode() == "off"
    with pytest.raises(client.LLMUnavailable):
        client.extract("t", "r1", "sys", [], Small)
    (tmp_path / "fx" / "t").mkdir(parents=True)
    (tmp_path / "fx" / "t" / "reception_log-14.json").write_text('{"name": "B", "count": 5}')
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path / "fx"))
    assert client.extract("t", "reception_log-14", "sys", [], Small).count == 5


def test_callout_message_is_sent_with_its_source_and_times(monkeypatch):
    answer = CalloutExtraction(is_absence=True, is_followup=False, employee_name="Besnik", building_text="Corlett",
                               shift="night", days=[], category="sick", bereavement_relation=None,
                               stated_received_time=None, language="sq", english_summary="", confidence=0.9)
    f = fake(monkeypatch, ok(answer))
    report = CalloutReport("reception_log", "14", datetime(2026, 10, 9, 13, 40, tzinfo=TZ), "Voicemail",
                           datetime(2026, 10, 9, 13, 36, tzinfo=TZ), "Besnik", "jam i semurë sot", "Rita")
    assert extract_callout(report).category == "sick"
    sent = f.calls[0]["messages"][0]["content"][0]["text"]
    assert "Voicemail received: Friday 10/09/2026 13:36" in sent and "jam i semurë sot" in sent
