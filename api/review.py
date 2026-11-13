"""Reviewer decisions on held items.

Approve -> the absence goes through the SAME safe payroll writer as the
pipeline (look-up first, no duplicates, audit). Reject -> nothing is written.
Every decision is recorded in review_action, so the next pipeline run keeps it.
"""
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

from sqlalchemy import text

from pipeline.callouts import Absence
from pipeline.clients.payroll import PayrollClient
from pipeline.payroll_writer import PayrollWriter


def apply_decision(engine, item: dict, action: str, code: str | None, note: str) -> dict:
    """Approve (write to payroll) or reject one held item, and record the decision."""
    result = {"absence_id": item["absence_id"], "action": action}
    if action == "approve":
        if not item.get("payroll_id") or not item.get("date"):
            return {**result, "error": "item has no resolved employee/date; fix the source record first"}
        code = code or item.get("code")
        rep = NS(report=NS(source=item["source"], source_ref=item["source_ref"]), ref=f"{item['source']}:{item['source_ref']}")
        day = item["date"] if isinstance(item["date"], date) else date.fromisoformat(str(item["date"])[:10])
        a = Absence(employee=NS(hr_id=item["hr_id"]), day=day, shift=item.get("shift") or "day",
                    building=item.get("building"), category=item.get("category") or "", reports=[rep], received_at=None)
        a.code, a.payroll_id = code, int(item["payroll_id"])
        writer = PayrollWriter(PayrollClient(os.getenv("PAYROLL_URL", "http://localhost:8081"), os.getenv("PAYROLL_API_KEY", "")),
                               dry_run=os.getenv("DRY_RUN", "true").lower() == "true", poll_timeout=30)
        writer.write(a)
        writer.collect_final_status([a])
        result.update(decision=a.decision, submission_id=a.submission_id, final_status=a.final_status,
                      issues=a.review, audit=writer.audit)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS review_action (absence_id text, action text, code text, "
                          "note text, at timestamptz, outcome text)"))
        conn.execute(text("INSERT INTO review_action VALUES (:a, :act, :c, :n, :t, :o)"),
                     {"a": item["absence_id"], "act": action, "c": code, "n": note,
                      "t": datetime.now(timezone.utc), "o": str(result.get("final_status") or result.get("error") or "")})
        if action == "reject" or result.get("decision") == "written":
            conn.execute(text("DELETE FROM review_queue WHERE absence_id = :a"), {"a": item["absence_id"]})
    return result
