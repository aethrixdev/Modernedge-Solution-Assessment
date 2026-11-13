"""Write the scored files into OUTPUT_DIR, in the exact formats the package
README defines."""
import csv
from datetime import datetime, timezone
from pathlib import Path

from .callouts import Absence, ReportEvent
from .common import Findings, fmt_money, fmt_rate
from .kpi import BUCKETS, COLUMNS

MONEY_COLS = {"overtime_dollars", "revenue", "labor_cost", "gross_margin", "unbilled_coverage_dollars"}
RATE_COLS = {"callout_rate", "fill_rate", "gross_margin_pct"}


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    """Write rows to a CSV with exactly these columns, in this order."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_kpis(out: Path, rows: list[dict]) -> None:
    """kpis_by_building.csv: money with 2 decimals, rates as decimals."""
    formatted = []
    for r in rows:
        x = dict(r)
        for c in MONEY_COLS:
            x[c] = fmt_money(r[c])
        for c in RATE_COLS:
            x[c] = fmt_rate(r[c])
        formatted.append(x)
    _write_csv(out / "kpis_by_building.csv", COLUMNS, formatted)


def write_ar_aging(out: Path, totals: dict) -> None:
    """ar_aging.csv: the open amount in each aging bucket."""
    _write_csv(out / "ar_aging.csv", ["bucket", "open_amount"],
               [{"bucket": b, "open_amount": fmt_money(totals[b])} for b in BUCKETS])


def submission_rows(absences: list[Absence], unresolved: list[ReportEvent]) -> list[dict]:
    """One payroll_submissions.csv row per absence (and per report we couldn't resolve)."""
    rows = []
    for a in sorted(absences, key=lambda a: (a.day, a.employee.hr_id)):
        rows.append({
            "source": a.primary.report.source, "source_ref": a.primary.report.source_ref,
            "employee_id": a.payroll_id or "", "date": a.day.isoformat(), "code": a.code or "",
            "decision": a.decision, "submission_id": a.submission_id,
            "final_status": a.final_status if a.decision == "written" else "",
            "reason": a.code_reason if a.decision == "written" else "; ".join(dict.fromkeys(a.review)),
        })
    for ev in unresolved:
        day = ev.days[0].day.isoformat() if ev.days else ev.report.logged_at.date().isoformat()
        rows.append({
            "source": ev.report.source, "source_ref": ev.report.source_ref, "employee_id": "", "date": day,
            "code": "", "decision": "held_for_review", "submission_id": "", "final_status": "",
            "reason": "; ".join(dict.fromkeys(ev.review)) or "employee not identified",
        })
    return rows


def write_payroll_submissions(out: Path, rows: list[dict]) -> None:
    """payroll_submissions.csv, with the exact columns the package README asks for."""
    _write_csv(out / "payroll_submissions.csv",
               ["source", "source_ref", "employee_id", "date", "code", "decision", "submission_id", "final_status", "reason"],
               rows)


def write_backup_notes(out: Path, notes: dict[str, str]) -> None:
    """One backup_notes/<posting_id>.txt per billable coverage shift."""
    folder = out / "backup_notes"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.txt"):
        old.unlink()
    for posting_id, text in notes.items():
        (folder / f"{posting_id}.txt").write_text(text, encoding="utf-8", newline="\n")   # same bytes on any OS


def write_exceptions(out: Path, findings: Findings) -> None:
    """exceptions.csv: every problem found and what was done about it."""
    _write_csv(out / "exceptions.csv", ["source", "record_ref", "issue", "action_taken", "confidence"],
               findings.exception_dicts())


def write_decisions_log(out: Path, standing_decisions: str, findings: Findings, aliases: list, summary: list[str]) -> None:
    """decisions_log.md: my standing decisions plus the conflicts and mappings found in this dataset."""
    parts = [
        "# Decisions log",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by the pipeline._",
        "",
        "Part 1 is my decision log (docs/decisions_log.md). Part 2 lists the specific conflicts found in this "
        "run's data. Part 3 shows the control totals checked.",
        "",
        "## Part 1: My decisions",
        "",
        "\n".join(ln for ln in standing_decisions.strip().splitlines() if not ln.startswith("# ")).strip(),
        "",
        "## Part 2: Conflicts found in this run",
        "",
        *[f"- {c}" for c in findings.conflicts],
        "",
        "### Building names: how each source's name or code was mapped",
        "",
        "| Source | Name or code | Building (Schedule B) | Method | Confidence |",
        "|---|---|---|---|---|",
        *[f"| {a.source} | {a.text} | {a.building or '**unmapped**'} | {a.method} | {a.confidence:.2f} |" for a in aliases],
        "",
        "## Part 3: Control totals",
        "",
        *[f"- {t}" for t in findings.tieouts],
        "",
        "## Run summary",
        "",
        *[f"- {s}" for s in summary],
        "",
    ]
    (out / "decisions_log.md").write_text("\n".join(parts), encoding="utf-8")
