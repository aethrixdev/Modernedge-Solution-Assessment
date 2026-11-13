"""KPIs by building, exactly as the package's docs/kpi_definitions.md defines them.

Every number is built from "facts": one row per contributing source record
(an employee-day, a posting, an invoice line, a GL line). The facts are
stored too, so every figure in the UI can be clicked through to its sources.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .callouts import Absence
from .common import Findings
from .coverage import CoverageShift
from .ingest.contracts import PostChange, posts_in_force
from .ingest.finance_reports import ArItem, GlLine, InvoiceLine
from .resolve.employees import EmployeeDirectory
from .rules.shifts import SHIFT_HOURS, weekdays

COLUMNS = ["building", "contracted_headcount", "active_headcount", "headcount_variance", "scheduled_shifts",
           "callout_shifts", "callout_rate", "fill_rate", "overtime_hours", "overtime_dollars", "revenue",
           "labor_cost", "gross_margin", "gross_margin_pct", "unbilled_coverage_count", "unbilled_coverage_dollars"]


@dataclass
class Fact:
    """One source record's contribution to one KPI, so every figure can be traced back."""
    building: str
    kpi: str
    source: str
    source_ref: str
    value: Decimal
    detail: str


def compute(buildings: list[str], terms: dict, changes: list[PostChange], directory: EmployeeDirectory,
            absences: list[Absence], coverage: list[CoverageShift], invoice_lines: list[InvoiceLine],
            gl_lines: list[GlLine], dept_building: dict[str, str], site_building: dict[str, str],
            period: tuple[date, date], period_label: str, findings: Findings) -> tuple[list[dict], list[Fact]]:
    """Build every KPI fact for every building, then add them up into one row per building."""
    start, end = period
    facts: list[Fact] = []
    add = lambda b, k, src, ref, v, d="": facts.append(Fact(b, k, src, str(ref), Decimal(str(v)), d))  # noqa: E731

    for b in buildings:
        day, night = posts_in_force(terms[b][0], changes, end)
        add(b, "contracted_headcount", terms[b][1].source_file, f"Schedule B {b}", day + night,
            f"{day} day + {night} night posts in force on {end}")

    permanent = [e for e in directory.employees if e.employment_type == "PERMANENT"]
    for e in permanent:
        home_end = directory.home_building(e, end)
        if home_end and e.hire_date <= end and (e.termination_date is None or e.termination_date > end) \
                and e.status != "TERMINATED":
            add(home_end, "active_headcount", "hr_roster", e.hr_id, 1, e.full_name)
        for d in weekdays(max(start, e.hire_date), min(end, e.termination_date or end)):
            b = directory.home_building(e, d)
            if b:
                add(b, "scheduled_shifts", "hr_roster", f"{e.hr_id} {d}", 1, e.full_name)
            else:
                findings.exception("hr_roster", e.hr_id, f"home location {directory.home_location(e, d)} on {d} "
                                   "is not a contracted building", "shift not counted", 0.3)

    for a in absences:
        if a.building:
            add(a.building, "callout_shifts", a.primary.report.source, a.primary.report.source_ref, 1,
                f"{a.employee.hr_id} {a.employee.full_name} {a.day} {a.category}")
        else:
            findings.exception(a.primary.report.source, a.primary.report.source_ref,
                               f"{a.employee.hr_id} has no home building on {a.day} (floater/temp)",
                               "call-out not attributed to a building", 0.5)

    for s in coverage:
        if not s.building:
            continue
        if s.status != "CANCELLED":
            add(s.building, "postings", "dispatch", s.posting_id, 1, s.status)
            if s.filled:
                add(s.building, "filled_postings", "dispatch", s.posting_id, 1, s.worker_id)
        if s.filled and s.overtime:
            add(s.building, "overtime_hours", "dispatch", s.posting_id, SHIFT_HOURS,
                s.covering.full_name if s.covering else s.worker_id)
            if s.ot_dollars is not None:
                add(s.building, "overtime_dollars", "dispatch", s.posting_id, s.ot_dollars,
                    f"8h x {s.rate} x 1.5")
        if s.billable and s.invoice_ref is None:
            add(s.building, "unbilled_coverage_count", "dispatch", s.posting_id, 1, "")
            add(s.building, "unbilled_coverage_dollars", "dispatch", s.posting_id, s.billable_amount,
                f"8h x {s.rate}{' x 1.5' if s.overtime else ''} x {1 + s.burden} x {1 + s.markup}")

    for ln in invoice_lines:
        if ln.service_period != period_label:
            continue
        b = site_building.get(ln.site)
        if b:
            add(b, "revenue", f"INV_REG {ln.entity}", ln.ref, ln.amount, f"{ln.line_type} {ln.description}")
        else:
            findings.exception(f"INV_REG {ln.entity}", ln.ref, f"site '{ln.site}' not mapped to a building",
                               "revenue not attributed", 0.0)

    for g in gl_lines:
        b = dept_building.get(g.dept)
        if b:
            add(b, "labor_cost", f"GL_LABOR {g.entity}", f"line {g.line_no}", g.amount,
                f"dept {g.dept} {g.account} {g.account_name} w/e {g.week_ending}")
        else:
            findings.exception(f"GL_LABOR {g.entity}", f"line {g.line_no}", f"dept {g.dept} not mapped to a building",
                               "labor not attributed", 0.0)

    total = defaultdict(lambda: defaultdict(Decimal))
    for f in facts:
        total[f.building][f.kpi] += f.value

    rows = []
    for b in buildings:
        t = total[b]
        revenue, labor = t["revenue"], t["labor_cost"]
        gm = revenue - labor
        rows.append({
            "building": b,
            "contracted_headcount": int(t["contracted_headcount"]),
            "active_headcount": int(t["active_headcount"]),
            "headcount_variance": int(t["active_headcount"] - t["contracted_headcount"]),
            "scheduled_shifts": int(t["scheduled_shifts"]),
            "callout_shifts": int(t["callout_shifts"]),
            "callout_rate": (t["callout_shifts"] / t["scheduled_shifts"]) if t["scheduled_shifts"] else None,
            "fill_rate": (t["filled_postings"] / t["postings"]) if t["postings"] else None,
            "overtime_hours": int(t["overtime_hours"]),
            "overtime_dollars": t["overtime_dollars"],
            "revenue": revenue,
            "labor_cost": labor,
            "gross_margin": gm,
            "gross_margin_pct": (gm / revenue) if revenue else None,
            "unbilled_coverage_count": int(t["unbilled_coverage_count"]),
            "unbilled_coverage_dollars": t["unbilled_coverage_dollars"],
        })
    return rows, facts


BUCKETS = ["current", "1_30", "31_60", "61_90", "over_90"]


def bucket(days_past_due: int) -> str:
    """Which aging bucket a number of days past due falls in."""
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1_30"
    if days_past_due <= 60:
        return "31_60"
    if days_past_due <= 90:
        return "61_90"
    return "over_90"


def ar_aging(items: list[ArItem], as_of: date, findings: Findings) -> tuple[dict[str, Decimal], list[dict]]:
    """Open = invoice amount less payments applied to it. A credit memo (or an
    unapplied payment) is open at its own amount, aged from its document date."""
    applied = defaultdict(Decimal)
    for it in items:
        if it.doc_type != "INV" and it.apply_to:
            applied[(it.entity, it.apply_to)] += it.amount
    detail, totals = [], {b: Decimal("0") for b in BUCKETS}
    invoices = {(it.entity, it.doc_no) for it in items if it.doc_type == "INV"}
    for (entity, doc), amt in applied.items():
        if (entity, doc) not in invoices:
            findings.exception(f"AR_OPEN {entity}", doc, "payment applied to a document not in the report",
                               "payment ignored", 0.3)
    for it in items:
        if it.doc_type == "INV":
            open_amt = it.amount + applied[(it.entity, it.doc_no)]
            aged_from = it.due_date or it.doc_date
        elif not it.apply_to:
            open_amt, aged_from = it.amount, it.doc_date     # credit memo / unapplied cash
        else:
            continue
        if open_amt == 0:
            continue
        if open_amt < 0 and it.doc_type == "INV":
            findings.exception(f"AR_OPEN {it.entity}", it.doc_no, f"overpaid by {-open_amt}", "kept as a credit", 0.6)
        days = (as_of - aged_from).days
        b = bucket(days)
        totals[b] += open_amt
        detail.append({"entity": it.entity, "doc_no": it.doc_no, "doc_type": it.doc_type, "customer": it.customer,
                       "open_amount": open_amt, "aged_from": aged_from, "days_past_due": days, "bucket": b})
    return totals, detail
