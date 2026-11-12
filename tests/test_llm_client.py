"""LLM client, offline: the model's HTTP replies are faked with `responses`."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import responses
from pydantic import BaseModel

from pipeline.clients import http
from pipeline.ingest.callout_sources import CalloutReport
from pipeline.llm import client
from pipeline.llm.callout_extractor import CalloutExtraction, extract_callout

TZ = ZoneInfo("America/New_York")


class Small(BaseModel):
    name: str
    count: int


def _reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setattr(http, "sleep", lambda s: None)


@responses.activate
def test_valid_json_comes_back_as_the_schema_object():
    responses.post(client.URL, json=_reply('{"name": "Ana", "count": 2}'))
    assert client.extract("t", "r1", "sys", [{"type": "text", "text": "hi"}], Small) == Small(name="Ana", count=2)
    body = json.loads(responses.calls[0].request.body)
    assert body["response_format"] == {"type": "json_object"}
    assert '"count"' in body["messages"][0]["content"]                       # schema is in the system prompt
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-key"


@responses.activate
def test_bad_json_gets_one_fix_attempt_then_raises():
    responses.post(client.URL, json=_reply('{"name": "Ana"}'))              # missing count
    responses.post(client.URL, json=_reply('{"name": "Ana", "count": 3}'))
    assert client.extract("t", "r1", "sys", [{"type": "text", "text": "a"}], Small).count == 3
    assert "does not match the schema" in json.loads(responses.calls[1].request.body)["messages"][-1]["content"]

    responses.post(client.URL, json=_reply("not json"))
    responses.post(client.URL, json=_reply("still not json"))
    with pytest.raises(client.LLMUnavailable):
        client.extract("t", "r2", "sys", [{"type": "text", "text": "b"}], Small)


@responses.activate
def test_rate_limit_is_retried_and_a_cached_reply_is_reused():
    responses.post(client.URL, status=429, headers={"Retry-After": "1"})
    responses.post(client.URL, json=_reply('{"name": "A", "count": 1}'))
    content = [{"type": "text", "text": "x"}]
    assert client.extract("t", "r1", "sys", content, Small).count == 1
    assert client.extract("t", "r1", "sys", content, Small).count == 1        # second time: from the cache
    assert len(responses.calls) == 2                                          # 429 + one success, no third call


def test_off_and_replay_modes(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY")
    assert client.mode() == "off"
    with pytest.raises(client.LLMUnavailable):
        client.extract("t", "r1", "sys", [], Small)
    (tmp_path / "fx" / "t").mkdir(parents=True)
    (tmp_path / "fx" / "t" / "reception_log-14.json").write_text('{"name": "B", "count": 5}')
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("LLM_FIXTURES_DIR", str(tmp_path / "fx"))
    assert client.extract("t", "reception_log-14", "sys", [], Small).count == 5


@responses.activate
def test_callout_message_is_sent_with_its_source_and_times():
    answer = {"is_absence": True, "is_followup": False, "employee_name": "Besnik", "building_text": "Corlett",
              "shift": "night", "days": [{"kind": "relative", "value": "today", "text": "sot"}],
              "category": "sick", "bereavement_relation": None, "stated_received_time": None,
              "language": "sq", "english_summary": "Besnik is sick tonight.", "confidence": 0.9}
    responses.post(client.URL, json=_reply(json.dumps(answer)))
    report = CalloutReport("reception_log", "14", datetime(2026, 10, 9, 13, 40, tzinfo=TZ), "Voicemail",
                           datetime(2026, 10, 9, 13, 36, tzinfo=TZ), "Besnik", "jam i semurë sot", "Rita")
    out = extract_callout(report)
    assert isinstance(out, CalloutExtraction) and out.category == "sick"
    sent = json.loads(responses.calls[0].request.body)["messages"][1]["content"]
    assert "Voicemail received: Friday 10/09/2026 13:36" in sent and "jam i semurë sot" in sent
