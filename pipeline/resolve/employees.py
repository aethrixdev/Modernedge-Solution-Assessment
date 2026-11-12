"""One record per employee, linked to every system's identifier.

Identifiers we link:
- HR id "E10012" (the master record)
- time clock badge "0010012" / "10012" (same digits as the HR id)
- payroll employee_id 10012 (checked against the payroll name, not assumed)
- dispatch worker "W-8059" via its hand-typed hr_employee_no (checked
  against the display name, because it is typed by hand and can be wrong)
- free-text names from call-outs and postings ("Brown, Angela", "Angela B.",
  "F. Berisha", "Jelena"), matched with building/shift/date as tie-breakers

Home building on a date: the HR roster, changed by a Personnel Action Form
only if all three approvals are signed.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from ..common import Findings, norm
from ..ingest.hr import Employee


# ---- names -----------------------------------------------------------------------


@dataclass
class NamePattern:
    """A name split into first and last parts; either may be just an initial or missing."""
    first: str | None        # full first name, or a single initial
    last: str | None         # full last name, or a single initial


def parse_person_name(text: str) -> NamePattern:
    """Turn the many ways names are written into first/last parts.
    'Brown, Angela' / 'Angela Brown' / 'Angela B.' / 'F. Berisha' / 'Jelena'"""
    t = (text or "").strip()
    if "," in t:
        last, first = [p.strip() for p in t.split(",", 1)]
        return NamePattern(norm(first).split()[0] if norm(first) else None, norm(last) or None)
    words = norm(t).split()
    if not words:
        return NamePattern(None, None)
    if len(words) == 1:
        return NamePattern(words[0], None)
    return NamePattern(words[0], " ".join(words[1:]))


def _part_matches(pattern: str | None, value: str) -> float:
    """1.0 exact, 0.9 initial matches, 0 otherwise. None means 'not given'."""
    if pattern is None:
        return 0.8
    v = norm(value)
    if pattern == v or pattern in v.split():
        return 1.0
    if len(pattern) == 1 and v.startswith(pattern):
        return 0.9
    return 0.0


def name_score(text: str, e: Employee) -> float:
    """How well a written name fits an employee: 1.0 exact, 0.9 with initials, 0 if a part conflicts."""
    p = parse_person_name(text)
    if p.first is None and p.last is None:
        return 0.0
    f = _part_matches(p.first, e.first_name)
    l = _part_matches(p.last, e.last_name)
    if f == 0 or l == 0:
        # maybe written "Last First" without a comma
        if p.first and p.last and _part_matches(p.last, e.first_name) == 1.0 and _part_matches(p.first, e.last_name) == 1.0:
            return 0.85
        return 0.0
    return round(min(f, l) if p.last else f * 0.95, 2)


# ---- directory ---------------------------------------------------------------------


@dataclass
class Transfer:
    """A home-location change from an approved Personnel Action Form."""
    effective: date
    from_location: str | None
    to_location: str
    source: str


@dataclass
class Match:
    """The result of matching a name: the employee (or None), confidence, reason and candidates."""
    employee: Employee | None
    confidence: float
    reason: str
    candidates: list[Employee] = field(default_factory=list)


class EmployeeDirectory:
    """Every HR employee, linked to their payroll ID, dispatch worker IDs and home building over time."""
    def __init__(self, employees: list[Employee]):
        """Index the HR roster by HR id and by number; links to other systems are added later."""
        self.employees = employees
        self.by_hr = {e.hr_id: e for e in employees}
        self.by_number = {e.number: e for e in employees}
        self.transfers: dict[str, list[Transfer]] = {}
        self.payroll_id: dict[str, int] = {}          # hr_id -> payroll employee_id
        self.payroll_status: dict[int, str] = {}
        self.worker: dict[str, Employee] = {}         # dispatch worker_id -> employee
        self.location_building: dict[str, str] = {}   # HR location code -> Schedule B name

    # -- home building on a date
    def home_location(self, e: Employee, day: date) -> str:
        """The HR location code that applies on `day`, after approved transfers."""
        loc = e.home_location
        history = sorted(self.transfers.get(e.hr_id, []), key=lambda t: t.effective)
        if history:
            loc = history[0].from_location or e.home_location   # before the first transfer
            for t in history:
                if t.effective <= day:
                    loc = t.to_location
        return loc

    def home_building(self, e: Employee, day: date) -> str | None:
        """The Schedule B building of the employee's home location on `day`."""
        return self.location_building.get(self.home_location(e, day))

    # -- linking other systems
    def link_payroll(self, payroll_employees: list[dict], findings: Findings) -> None:
        """Link each HR employee to a payroll ID: same digits if the payroll name agrees, else by name; log the rest."""
        by_id = {p["employee_id"]: p for p in payroll_employees}
        for p in payroll_employees:
            self.payroll_status[p["employee_id"]] = p.get("status", "")
        for e in self.employees:
            p = by_id.get(e.number)
            if p and name_score(p["name"], e) >= 0.9:
                self.payroll_id[e.hr_id] = e.number
                continue
            hits = [q for q in payroll_employees if name_score(q["name"], e) >= 0.95]
            if len(hits) == 1:
                self.payroll_id[e.hr_id] = hits[0]["employee_id"]
                findings.exception("payroll /v1/employees", hits[0]["employee_id"],
                                   f"payroll id does not follow HR number for {e.hr_id}", "linked by name", 0.85)
            else:
                findings.exception("payroll /v1/employees", e.hr_id, "employee not found in payroll",
                                   "absences for this employee will be held for review", 0.0)
        hr_numbers = {self.payroll_id[h] for h in self.payroll_id}
        for p in payroll_employees:
            if p["employee_id"] not in hr_numbers:
                findings.exception("payroll /v1/employees", p["employee_id"], f"payroll employee {p['name']} not in HR roster",
                                   "ignored", 0.5)

    def link_dispatch_workers(self, workers: list[dict], findings: Findings) -> None:
        """Link dispatch workers to HR: trust the typed hr_employee_no only if the name agrees, else match by name."""
        for w in workers:
            typed = (w.get("hr_employee_no") or "").strip().upper()
            e = self.by_hr.get(typed)
            if e and name_score(w["display_name"], e) >= 0.85:
                self.worker[w["worker_id"]] = e
                continue
            hits = [x for x in self.employees if name_score(w["display_name"], x) >= 0.85]
            if len(hits) == 1:
                self.worker[w["worker_id"]] = hits[0]
                findings.exception("dispatch /v2/workers", w["worker_id"],
                                   f"hr_employee_no '{typed}' does not match '{w['display_name']}'",
                                   f"linked by name to {hits[0].hr_id}", 0.85)
                findings.conflict(f"Dispatch worker {w['worker_id']} ({w['display_name']}) has hr_employee_no {typed or 'blank'}, "
                                  f"which {'does not exist in HR' if not e else 'belongs to ' + e.full_name}. "
                                  f"Linked by name to {hits[0].hr_id} {hits[0].full_name}.")
            else:
                findings.exception("dispatch /v2/workers", w["worker_id"],
                                   f"worker '{w['display_name']}' ({typed}) not matched to HR "
                                   f"({len(hits)} name candidates)", "coverage by this worker cannot be costed", 0.0)

    # -- free-text names
    def match_name(self, text: str, day: date | None = None, building: str | None = None,
                   shift: str | None = None, min_score: float = 0.75) -> Match:
        """Find the employee a free-text name refers to. Building, shift and date
        narrow down candidates when a name fits more than one person."""
        scored = [(name_score(text, e), e) for e in self.employees]
        cands = [(s, e) for s, e in scored if s >= min_score]
        if day:
            cands = [(s, e) for s, e in cands if e.employed_on(day)] or cands
        if not cands:
            return Match(None, 0.0, f"no employee matches name '{text}'")
        best = max(s for s, _ in cands)
        top = [(s, e) for s, e in cands if s == best]
        notes = []
        if len(top) > 1 and building and day:
            narrowed = [(s, e) for s, e in top if self.home_building(e, day) == building]
            if narrowed:
                top, notes = narrowed, notes + ["building"]
        if len(top) > 1 and shift:
            narrowed = [(s, e) for s, e in top if e.shift == shift.upper()]
            if narrowed:
                top, notes = narrowed, notes + ["shift"]
        if len(top) > 1:
            return Match(None, 0.0, f"name '{text}' fits {len(top)} employees: "
                         + ", ".join(f"{e.hr_id} {e.full_name}" for _, e in top), [e for _, e in top])
        score, e = top[0]
        conf = score * (0.95 if notes else 1.0)
        reason = f"name match ({score})" + (f", narrowed by {' and '.join(notes)}" if notes else "")
        if building and day and self.home_building(e, day) not in (None, building) and e.employment_type == "PERMANENT":
            conf *= 0.8
            reason += f"; stated building {building} differs from home building {self.home_building(e, day)}"
        return Match(e, round(conf, 2), reason, [e])


HR_ID_RE = re.compile(r"\b[A-Z]\d{4,}\b")
