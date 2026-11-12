"""Compare the LIVE LLM's readings with the hand-checked golden answers.

Skipped unless GROQ_API_KEY is set (in the environment or .env). About 70 small
requests; replies are cached under cache/llm, so a re-run is free. Run with:
    python -m pytest tests/test_llm_golden.py -v
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv

from pipeline.common import Findings
from pipeline.ingest.callout_sources import load_chat, load_reception_log
from tests.fixtures.golden_llm import CHAT, RECEPTION

load_dotenv()
DATA = Path(os.getenv("DATA_DIR", "../ModernEdge_AI_Solutions_Engineer_Assessment/data"))
pytestmark = pytest.mark.skipif(not os.getenv("GROQ_API_KEY") or not DATA.exists(),
                                reason="needs GROQ_API_KEY and the sample data")


def _reports():
    """Every call-out report in the sample data, from both sources."""
    tz, f = ZoneInfo("America/New_York"), Findings()
    return (load_reception_log(next((DATA / "callouts").glob("*.xlsx")), tz, f)
            + load_chat(next((DATA / "callouts").glob("*.txt")), tz, f))


def test_live_extraction_agrees_with_golden_set(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    from pipeline.llm.callout_extractor import extract_callout
    misses = []
    for r in _reports():
        gold = RECEPTION.get(int(r.source_ref)) if r.source == "reception_log" else CHAT.get(r.source_ref)
        x = extract_callout(r)
        absence = x.is_absence and x.category not in ("not_absence", "vacation")
        if absence != (gold["is_absence"] and gold["category"] != "not_absence"):
            misses.append(f"{r.source_ref}: absence {absence} vs {gold['is_absence']}")
            continue
        if not absence:
            continue
        if x.category != gold["category"]:
            misses.append(f"{r.source_ref}: category {x.category} vs {gold['category']}")
        if [(d.kind, d.value) for d in x.days] != [(d["kind"], d["value"]) for d in gold["days"]]:
            misses.append(f"{r.source_ref}: days {x.days} vs {gold['days']}")
        if (x.stated_received_time or None) != gold["stated_received_time"]:
            misses.append(f"{r.source_ref}: stated time {x.stated_received_time} vs {gold['stated_received_time']}")
    assert not misses, "\n".join(misses)
