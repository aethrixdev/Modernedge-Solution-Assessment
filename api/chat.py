"""Chatbot that answers ONLY from the data.

Claude gets one tool, `run_sql`, which runs a read-only SELECT on the
pipeline's tables. It must cite the source records behind every number
([source | source_ref] tags, which the UI turns into links) and say so when
the data cannot answer. The database session is READ ONLY, so the model
cannot change anything even if asked to.
"""
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

MODEL = os.getenv("LLM_MODEL") or "claude-opus-5-5"
MAX_ROWS = 200
MAX_TURNS = 8

TOOL = {
    "name": "run_sql",
    "description": "Run one read-only PostgreSQL SELECT (or WITH ... SELECT) query against the operations database "
                   "and return up to 200 rows as JSON. Use it to look up every fact you state.",
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "a single SELECT statement"}},
        "required": ["sql"],
        "additionalProperties": False,
    },
    "strict": True,
}


def _schema(engine) -> str:
    """The tables and columns, plus the exact building/KPI names and the period, for the system prompt."""
    with engine.connect() as conn:
        cols = conn.execute(text("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                 "WHERE table_schema='public' ORDER BY table_name, ordinal_position")).all()
    tables = {}
    for t, c, d in cols:
        tables.setdefault(t, []).append(f"{c} {d}")
    out = "\n".join(f"- {t}({', '.join(cs)})" for t, cs in tables.items())
    # The exact values to filter on, read from this dataset (so it works on any data, not just the sample).
    with engine.connect() as conn:
        for label, sql in (("Building names (exact spelling)", "SELECT DISTINCT building FROM out_kpis_by_building ORDER BY 1"),
                           ("KPI names in fact_kpi.kpi", "SELECT DISTINCT kpi FROM fact_kpi ORDER BY 1"),
                           ("Reporting period (first and last absence date; a date without a year is in it)",
                            "SELECT unnest(ARRAY[MIN(date), MAX(date)]) FROM fact_absence")):
            try:
                vals = conn.execute(text(sql)).scalars().all()
                out += f"\n\n{label}: " + ", ".join(str(v) for v in vals)
            except Exception:
                conn.rollback()
    return out


GUIDE = """You answer questions about a building services company's operations and finances for the reporting month,
using ONLY the database below (built from HR, time clock, dispatch, payroll, finance reports, contracts and call-out logs).

Rules:
- Look everything up with run_sql before answering. Never use outside knowledge or guess a number.
- If the data cannot answer the question, say "The data does not show that" and explain what is missing.
- Filter on the exact building and KPI names listed at the end. A user saying "Corlett" means the building whose
  name contains Corlett. If a query returns no rows, check your filter values before concluding the answer is zero.
- For KPI questions (unbilled coverage, revenue, overtime, fill rate ...) use fact_kpi / out_kpis_by_building first:
  they hold the official figures.
- Cite the source records behind each figure as [source | source_ref], filled with the VALUES of the source and
  source_ref columns of the rows you used, e.g. [dispatch | P-1020-040] or [reception_log | 14]. Never cite a table
  or column name, and never a person's name. So always SELECT the source and source_ref columns together with the
  facts you report. Use plain square brackets. Cite at most 8.
- KPI figures are in out_kpis_by_building; the records behind each KPI are in fact_kpi (building, kpi, source,
  source_ref, value, detail). Absences/payroll decisions: fact_absence. Coverage/billing: fact_coverage.
  Who was in a building on a date/shift: fact_building_view. Open items held for review: review_queue.
- Keep answers short and plain. Money with 2 decimals.

Tables:
"""


def _jsonable(v):
    """Decimals and dates as plain JSON values."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run_sql(engine, sql: str) -> str:
    """Run one SELECT in a read-only transaction; return rows (or the error) as JSON for the model."""
    s = sql.strip().rstrip(";")
    if ";" in s or not re.match(r"(?is)^\s*(select|with)\b", s):
        return json.dumps({"error": "only a single SELECT statement is allowed"})
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            res = conn.execute(text(s))
            out = [{k: _jsonable(v) for k, v in r._mapping.items()} for r in res.fetchmany(MAX_ROWS)]
            conn.rollback()
        return json.dumps({"rows": out, "row_count": len(out), "truncated": len(out) == MAX_ROWS})
    except Exception as exc:   # report SQL errors back to the model so it can fix the query
        return json.dumps({"error": str(exc).splitlines()[0][:300]})


def _remember(seen: dict, out: str) -> None:
    """Note every (source_ref -> source) pair the queries actually returned."""
    try:
        rows = json.loads(out).get("rows", [])
    except (json.JSONDecodeError, AttributeError):
        return
    for r in rows:
        if r.get("source_ref") is not None and r.get("source"):
            seen[str(r["source_ref"])] = str(r["source"])


def _finish(final: str, seen: dict) -> tuple[str, list[dict]]:
    """Check the model's [source | ref] tags against the rows it actually looked at.
    A tag whose ref was returned by a query gets the real source name and becomes a citation;
    a tag we cannot verify stays as plain text and is not linked."""
    final = final.replace("【", "[").replace("】", "]")   # some models use these brackets
    cites = []

    def fix(m):
        ref = m.group(2).strip()
        if ref not in seen:
            return m.group(0)
        cite = {"source": seen[ref], "ref": ref}
        if cite not in cites:
            cites.append(cite)
        return f"[{seen[ref]} | {ref}]"

    final = re.sub(r"\[([^\]|]+)\|([^\]]+)\]", fix, final)
    return final, cites


def answer(engine, question: str, history: list[dict]) -> dict:
    """Let Claude query the data (up to MAX_TURNS tool calls) and return its answer, queries and checked citations."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"answer": "The chatbot needs ANTHROPIC_API_KEY to be set.", "queries": [], "citations": []}
    import anthropic

    client = anthropic.Anthropic()
    system = GUIDE + _schema(engine)
    messages = [m for m in history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    messages.append({"role": "user", "content": question})
    queries, seen = [], {}
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=8000, system=system, tools=[TOOL], messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return {"answer": "I can't help with that request.", "queries": queries, "citations": []}
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            final, cites = _finish("".join(b.text for b in resp.content if b.type == "text").strip(), seen)
            return {"answer": final or "The data does not show that.", "queries": queries, "citations": cites}
        results = []
        for u in uses:
            sql = (u.input or {}).get("sql", "")
            out = run_sql(engine, sql)
            _remember(seen, out)
            queries.append({"sql": sql, "result_preview": out[:500]})
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
        messages.append({"role": "user", "content": results})
    return {"answer": "I could not finish looking that up. Please ask a narrower question.", "queries": queries, "citations": []}

