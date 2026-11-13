"""Dispatch coverage, billing and backup notes (Part B step 7).

For every dispatch posting:
- the building (Schedule B name) and the FINAL assignment: events are read in
  order; ASSIGNED sets the worker, UNASSIGNED clears it, so the last
  assignment standing wins
- the covering employee, their base rate and burden, overtime or not
- at buildings whose agreement makes coverage billable: the billable amount,
  and whether an invoice coverage line already bills it

Invoice coverage lines are matched to postings:
1. by the dispatch reference in the description ("ref P-1007-006"), if present
2. otherwise by building + service date + shift, then the replacement's name
   if the line has one, then the amount as the last tie-breaker
A billable shift with no matching line is UNBILLED coverage.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .common import Findings, money
from .ingest.contracts import Contract, WageSchedule
from .ingest.finance_reports import InvoiceLine
from .ingest.hr import Employee
from .resolve.buildings import BuildingRegistry
from .resolve.employees import EmployeeDirectory, name_score
from .rules import billing
from .rules.shifts import shift_window

REF_RE = re.compile(r"\bP-\d{4}-\d{3}\b")


@dataclass
class CoverageShift:
    """One dispatch posting: who covered it, what it cost, whether it's billable and whether it was billed."""
    posting_id: str
    site_id: str
    building: str | None
    day: date
    shift: str                       # "day" / "night"
    reason: str                      # CALLOUT / VACATION
    status: str                      # OPEN / FILLED / EXPIRED / CANCELLED
    absent_text: str
    worker_id: str | None = None
    fill_type: str | None = None     # STANDARD / OT
    covering: Employee | None = None
    absent: Employee | None = None
    rate: Decimal | None = None
    burden: Decimal | None = None
    markup: Decimal | None = None
    billable: bool = False
    billable_amount: Decimal | None = None
    ot_dollars: Decimal | None = None
    on_site_in: str | None = None
    on_site_out: str | None = None
    invoice_ref: str | None = None
    invoice_amount: Decimal | None = None
    invoice_match: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        """Filled, and someone is still assigned after all the events."""
        return self.status == "FILLED" and self.worker_id is not None

    @property
    def overtime(self) -> bool:
        """Covered on overtime (paid x1.5)."""
        return self.fill_type == "OT"


def final_assignment(events: list[dict]) -> tuple[str | None, str | None]:
    """Replay the posting's events in order; the last assignment still standing wins."""
    worker, fill = None, None
    for ev in events:
        if ev.get("type") == "ASSIGNED":
            worker, fill = ev.get("worker_id"), ev.get("fill_type")
        elif ev.get("type") == "UNASSIGNED":
            worker, fill = None, None
    return worker, fill


def build_coverage(postings: list[dict], site_building: dict[str, str], directory: EmployeeDirectory,
                   wages: WageSchedule, terms: dict, period: tuple[date, date], findings: Findings) -> list[CoverageShift]:
    """terms: building -> (BuildingTerms, Contract)"""
    out = []
    for p in postings:
        day = date.fromisoformat(p["shift_date"])
        if not (period[0] <= day <= period[1]):
            continue
        cs = CoverageShift(p["posting_id"], p["site_id"], site_building.get(p["site_id"]), day,
                           "night" if p.get("shift") == "N" else "day", p.get("reason", ""), p.get("status", ""),
                           p.get("absent_employee_text", ""))
        if cs.building is None:
            findings.exception("dispatch /v2/postings", cs.posting_id, f"site {cs.site_id} not mapped to a building",
                               "posting left out of building KPIs", 0.0)
        cs.worker_id, cs.fill_type = final_assignment(p.get("events", []))
        if cs.status == "FILLED" and cs.worker_id is None:
            findings.exception("dispatch /v2/postings", cs.posting_id, "status FILLED but no assignment stands after its events",
                               "treated as not filled", 0.3)
        if sum(1 for e in p.get("events", []) if e.get("type") == "UNASSIGNED"):
            cs.notes.append("reassigned: the last assignment is used")
        if cs.worker_id:
            cs.covering = directory.worker.get(cs.worker_id)
            if cs.covering is None:
                findings.exception("dispatch /v2/postings", cs.posting_id, f"covering worker {cs.worker_id} not linked to HR",
                                   "cannot cost this coverage", 0.0)
        if cs.absent_text:
            m = directory.match_name(cs.absent_text, day, cs.building, cs.shift)
            cs.absent = m.employee
            if m.employee is None:
                findings.exception("dispatch /v2/postings", cs.posting_id, f"absent employee '{cs.absent_text}' not resolved: {m.reason}",
                                   "position covered unknown", 0.0)
        if cs.covering and cs.filled:
            cs.rate = billing.base_rate(cs.covering, wages, day)
            cs.burden = billing.burden(cs.covering, wages)
            if cs.rate is None:
                findings.exception("wage schedule", cs.covering.job_class, f"no rate for {cs.covering.hr_id}",
                                   "coverage not costed", 0.0)
            elif cs.overtime:
                cs.ot_dollars = billing.overtime_dollars(cs.rate)
        t = terms.get(cs.building)
        if t and t[0].coverage_billable and cs.filled and cs.rate is not None:
            cs.billable = True
            cs.markup = t[0].markup or Decimal("0")
            cs.billable_amount = billing.billable_amount(cs.rate, cs.overtime, cs.burden, cs.markup)
        out.append(cs)
    return out


# ---- invoice matching --------------------------------------------------------------


def _line_name(desc: str) -> str | None:
    """The replacement's name from an invoice description, e.g. 'Overtime David Greene (ref ...)'."""
    m = re.search(r"(?:overtime|replacement)\s+(.+?)(?:\s*\(ref|$)", desc, re.I)
    return m.group(1).strip() if m else None


def _line_shift(desc: str) -> str | None:
    """'day' or 'night' from an invoice description."""
    m = re.search(r"\b(Day|Night)\b", desc, re.I)
    return m.group(1).lower() if m else None


def match_invoices(shifts: list[CoverageShift], lines: list[InvoiceLine], registry: BuildingRegistry,
                   period_label: str, findings: Findings) -> None:
    """Match invoice coverage lines to postings; a billable posting with no line is unbilled."""
    by_id = {s.posting_id: s for s in shifts}
    cov_lines = [ln for ln in lines if ln.line_type == "COVERAGE" and ln.service_period == period_label]

    def link(s: CoverageShift, ln: InvoiceLine, how: str):
        s.invoice_ref, s.invoice_amount, s.invoice_match = ln.ref, ln.amount, how
        if s.billable_amount is not None and ln.amount != s.billable_amount:
            findings.exception(f"INV_REG {ln.entity}", ln.ref,
                               f"billed {ln.amount} for {s.posting_id} but the contract formula gives {s.billable_amount}",
                               "kept as billed; difference flagged", 0.6)

    pending = []
    for ln in cov_lines:
        ref = REF_RE.search(ln.description)
        if ref:
            s = by_id.get(ref.group(0))
            if s and s.invoice_ref is None:
                link(s, ln, "dispatch reference on the invoice line")
                continue
            findings.exception(f"INV_REG {ln.entity}", ln.ref, f"coverage line cites {ref.group(0)}, which is "
                               f"{'already billed' if s else 'not a dispatch posting'}", "line not matched", 0.3)
            continue
        pending.append(ln)

    for ln in pending:
        building = registry.lookup("finance site", ln.site)
        shift = _line_shift(ln.description)
        name = _line_name(ln.description)
        cands = [s for s in shifts if s.invoice_ref is None and s.filled and s.building == building
                 and s.day == ln.service_date and (shift is None or s.shift == shift)]
        how = "building + date + shift"
        if name and cands:
            named = [s for s in cands if s.covering and name_score(name, s.covering) >= 0.85]
            if named:
                cands, how = named, how + " + replacement name"
        if len(cands) > 1:
            same_amount = [s for s in cands if s.billable_amount == ln.amount]
            if same_amount:
                cands, how = same_amount, how + " + amount"
        if not cands:
            findings.exception(f"INV_REG {ln.entity}", ln.ref, f"coverage line '{ln.description}' matches no filled posting",
                               "line not matched", 0.3)
            continue
        if len(cands) > 1:
            findings.exception(f"INV_REG {ln.entity}", ln.ref, f"coverage line fits {len(cands)} postings "
                               f"({', '.join(s.posting_id for s in cands)})", f"matched to {cands[0].posting_id}", 0.5)
        link(cands[0], ln, how)


# ---- time on site and backup notes --------------------------------------------------


def time_on_site(s: CoverageShift, sessions_by_key: dict) -> None:
    """First clock-in and last clock-out of the covering employee at that
    building for that date and shift (from the paired punch sessions)."""
    if not s.covering:
        return
    found = sessions_by_key.get((s.covering.number, s.building, s.day, s.shift), [])
    ins = [x.clock_in for x in found if x.clock_in]
    outs = [x.clock_out for x in found if x.clock_out]
    if ins:
        s.on_site_in = min(ins).strftime("%H:%M")
    if outs:
        s.on_site_out = max(outs).strftime("%H:%M")


def backup_note(s: CoverageShift, template: str, client: str, findings: Findings) -> str:
    """Never names the absent employee or the reason for the absence."""
    position = s.absent.job_class if s.absent else "Not recorded"
    common_top = [
        "COVERAGE BACKUP NOTE",
        f"Client:            {client}",
        f"Building:          {s.building}",
        f"Date of service:   {s.day:%m/%d/%Y}",
    ]
    bottom = [
        "Billable hours:    8.00",
        f"Billable amount:   ${money(s.billable_amount):.2f}",
        f"Posting reference: {s.posting_id}",
    ]
    if template == "A":
        begin, finish = shift_window(s.day, s.shift)
        missing = [label for label, value in (("clock-in", s.on_site_in), ("clock-out", s.on_site_out)) if not value]
        if missing:
            who = s.covering.full_name if s.covering else "covering worker"
            findings.exception("time_attendance", s.posting_id, f"{' and '.join(missing)} missing for {who} on {s.day}",
                               "backup note says 'Not recorded'", 0.9)
        lines = common_top + [
            f"Shift:             {s.shift.title()}, {begin} to {finish}",
            f"Position covered:  {position}",
            f"Coverage type:     {'Overtime' if s.overtime else 'Replacement'}",
            f"Replacement:       {s.covering.full_name if s.covering else 'Not recorded'}",
            f"On site:           {s.on_site_in or 'Not recorded'} to {s.on_site_out or 'Not recorded'}",
        ] + bottom
    else:
        lines = common_top + [
            f"Shift:             {s.shift.title()}",
            f"Position covered:  {position}",
            "Coverage:          Replacement provided",
        ] + bottom
    return "\n".join(lines) + "\n"
