"""HTTP API for the interface: building view, KPI panel (with drill-down to
source records), review queue and chatbot. Reads the tables the pipeline loaded into
Postgres.

Run locally:  uvicorn api.main:app --reload --port 8000
"""
import csv
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from . import chat

app = FastAPI(title="ModernEdge operations API")
engine = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://modernedge:modernedge@localhost:5432/modernedge"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))


def rows(sql: str, **params) -> list[dict]:
    """Run a query and return plain dicts; an empty list if the table isn't there yet."""
    try:
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), params)]
    except ProgrammingError:   # table not created yet (pipeline has not run, or it had no rows)
        return []


@app.get("/api/health")
def health():
    """Is the API up, and how many tables has the pipeline loaded."""
    return {"status": "ok", "tables": len(rows("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}


# ---- KPI panel ------------------------------------------------------------------
@app.get("/api/kpis")
def kpis():
    """KPIs per building, AR aging totals and the open AR items behind them."""
    return {"buildings": rows('SELECT * FROM out_kpis_by_building'),
            "ar_aging": rows('SELECT * FROM out_ar_aging'),
            "ar_items": rows('SELECT * FROM fact_ar_open ORDER BY days_past_due DESC')}


# KPIs that are ratios or differences: show the facts behind their parts
DRILL = {
    "callout_rate": ["callout_shifts", "scheduled_shifts"],
    "fill_rate": ["filled_postings", "postings"],
    "headcount_variance": ["active_headcount", "contracted_headcount"],
    "gross_margin": ["revenue", "labor_cost"],
    "gross_margin_pct": ["revenue", "labor_cost"],
}


@app.get("/api/kpis/{building}/{kpi}")
def kpi_facts(building: str, kpi: str):
    """Every source record behind one figure."""
    parts = DRILL.get(kpi, [kpi])
    return rows("SELECT * FROM fact_kpi WHERE building = :b AND kpi = ANY(:k) ORDER BY kpi, source, source_ref",
                b=building, k=parts)


# ---- building view ----------------------------------------------------------------
@app.get("/api/buildings")
def buildings():
    """All buildings from Schedule B."""
    return rows("SELECT * FROM dim_building")


@app.get("/api/building-view")
def building_view(building: str, day: date):
    """Who was meant to work, who called out and who covered, for one building on one day."""
    return {"posts": rows("SELECT * FROM dim_building WHERE building = :b", b=building),
            "people": rows("SELECT * FROM fact_building_view WHERE building = :b AND date = :d "
                           "ORDER BY shift, role, name", b=building, d=day),
            "dates": [r["date"] for r in rows("SELECT DISTINCT date FROM fact_building_view ORDER BY date")]}


# ---- review queue -----------------------------------------------------------------
@app.get("/api/review-queue")
def review_queue():
    """Held items, with the original message so the reviewer can see what was said."""
    return rows("SELECT q.*, r.message, r.logged_at, r.vm_received_at, r.channel FROM review_queue q "
                "LEFT JOIN raw_callout_report r ON r.source = q.source AND r.source_ref = q.source_ref "
                "ORDER BY q.date NULLS LAST")


class ReviewDecision(BaseModel):
    action: str                 # "approve" or "reject"
    code: str | None = None     # code to write when approving (defaults to the proposed one)
    reviewer_note: str = ""


@app.post("/api/review-queue/{absence_id}")
def decide(absence_id: str, body: ReviewDecision):
    """A reviewer approves (writes to payroll, same safe writer) or rejects an item."""
    from .review import apply_decision
    if body.action not in ("approve", "reject"):
        raise HTTPException(422, "action must be approve or reject")
    item = next(iter(rows("SELECT * FROM review_queue WHERE absence_id = :a", a=absence_id)), None)
    if item is None:
        raise HTTPException(404, "not in the review queue")
    return apply_decision(engine, item, body.action, body.code, body.reviewer_note)


# ---- source records (traceability) -------------------------------------------------
@app.get("/api/source")
def source(source: str, ref: str):
    """Show the original record behind any figure."""
    if source in ("reception_log", "area_manager_chat"):
        return rows("SELECT * FROM raw_callout_report WHERE source = :s AND source_ref = :r", s=source, r=ref)
    if source.startswith("INV_REG"):
        return rows("SELECT * FROM raw_invoice_line WHERE ref = :r", r=ref)
    if source.startswith("GL_LABOR"):
        entity, line = source.split()[-1], int(ref.split()[-1])
        return rows("SELECT * FROM raw_gl_line WHERE entity = :e AND line_no = :n", e=entity, n=line)
    if source.startswith("dispatch"):
        return rows("SELECT * FROM fact_coverage WHERE posting_id = :r", r=ref)
    if source == "hr_roster":
        return rows("SELECT * FROM dim_employee WHERE hr_id = :r", r=ref.split()[0])
    if source.endswith(".pdf") or source.startswith("MSA") or source.startswith("CBA"):
        return rows("SELECT * FROM dim_building WHERE source = :s", s=source)
    if ".csv#" in ref:      # time clock rows: "file.csv#123; file.csv#130"
        out = []
        for part in ref.split(";"):
            name, _, row_no = part.strip().partition("#")
            path = next(DATA_DIR.rglob(name), None)
            if path:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    for n, r in enumerate(csv.DictReader(f), start=2):
                        if n == int(row_no):
                            out.append({"file": name, "row": n, **r})
        return out
    return []


@app.get("/api/exceptions")
def exceptions():
    """exceptions.csv as a table."""
    return rows("SELECT * FROM out_exceptions")


@app.get("/api/payroll")
def payroll():
    """What we sent to payroll, and the audit trail of every call."""
    return {"submissions": rows("SELECT * FROM out_payroll_submissions"),
            "audit": rows("SELECT * FROM payroll_audit ORDER BY at")}


# ---- chatbot -------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str
    history: list[dict] = []


@app.post("/api/chat")
def ask(body: ChatRequest):
    """Answer a question from the data only, with citations."""
    return chat.answer(engine, body.question, body.history)
