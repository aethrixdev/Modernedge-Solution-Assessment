"""Chatbot, offline: Claude is replaced by a fake that asks for one query, then answers."""
import json
from types import SimpleNamespace as NS

import anthropic

from api import chat


def test_only_a_single_select_is_run():
    for bad in ("DELETE FROM fact_kpi", "SELECT 1; DROP TABLE fact_kpi", "UPDATE x SET y = 1"):
        assert "error" in json.loads(chat.run_sql(None, bad))     # refused before touching the database


def test_only_citations_the_queries_returned_are_linked():
    seen = {}
    chat._remember(seen, json.dumps({"rows": [{"source": "dispatch", "source_ref": "P-1020-040", "value": 429.33}]}))
    text, cites = chat._finish("Unbilled $429.33 [dispatch | P-1020-040], also [dispatch | P-9999-999].", seen)
    assert cites == [{"source": "dispatch", "ref": "P-1020-040"}]          # the made-up ref is not linked
    assert "[dispatch | P-9999-999]" in text


def test_without_a_key_the_chatbot_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "ANTHROPIC_API_KEY" in chat.answer(None, "hi", [])["answer"]


class FakeClaude:
    """First reply asks for a query; second reply answers with a citation."""
    def __init__(self):
        self.replies = [
            NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", input={"sql": "SELECT 1"})]),
            NS(stop_reason="end_turn", content=[NS(type="text", text="2 shifts unbilled [x | P-1020-040].")]),
        ]
        self.beta = NS(messages=NS(create=lambda **kw: self.replies.pop(0)))

    def __call__(self, **kwargs):
        return self


def test_answer_runs_the_query_and_checks_the_citation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClaude())
    monkeypatch.setattr(chat, "_schema", lambda engine: "")
    monkeypatch.setattr(chat, "run_sql", lambda engine, sql: json.dumps(
        {"rows": [{"source": "dispatch", "source_ref": "P-1020-040"}]}))
    got = chat.answer(None, "How much unbilled coverage at Corlett?", [])
    assert got["answer"] == "2 shifts unbilled [dispatch | P-1020-040]."   # source name corrected from the real row
    assert got["citations"] == [{"source": "dispatch", "ref": "P-1020-040"}]
    assert got["queries"][0]["sql"] == "SELECT 1"
