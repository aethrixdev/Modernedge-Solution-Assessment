"""Call-out flow (Part B, steps 5-6): messages -> absences -> payroll decisions.

1. Read every reception row and chat message with the LLM (what was said).
2. Work out when the call-out was received (voicemail time, call time, or the
   time a manager says the text arrived) and which date(s) it is about.
3. Match the employee (name + building + shift + date). If two people fit,
   a dispatch posting for one of them on that date breaks the tie.
4. Merge reports of the same absence (same employee, same date) - across the
   reception log and the chat. Notice is measured from the EARLIEST report.
5. Code each absence with the deterministic rules, in date order per
   employee so sick days are used up in order.
6. Decide: write to payroll, or hold for review with the reason.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from .common import Findings
from .ingest.callout_sources import RECEPTION, CalloutReport
from .ingest.hr import Employee
from .llm.callout_extractor import CalloutExtraction, extract_callout
from .llm.client import LLMUnavailable
from .resolve.buildings import BuildingRegistry
from .resolve.employees import EmployeeDirectory, name_score
from .rules.coding import CodingInput, code_absence
from .rules.dates import ResolvedDay, resolve_days
from .rules.shifts import is_workday, notice_hours

REVIEW_THRESHOLD = 0.75
IMMEDIATE_FAMILY = {"spouse", "wife", "husband", "partner", "domestic partner", "child", "son", "daughter",
                    "parent", "father", "mother", "dad", "mom", "sibling", "brother", "sister",
                    "grandparent", "grandfather", "grandmother", "grandpa", "grandma", "stepfather", "stepmother"}
BEREAVEMENT_MAX_DAYS = 3


@dataclass
class ReportEvent:
    """One call-out report after reading: who, when received, which days, and any review reasons."""
    report: CalloutReport
    extraction: CalloutExtraction | None
    received_at: datetime | None = None
    received_note: str = ""
    employee: Employee | None = None
    match_confidence: float = 0.0
    match_reason: str = ""
    shift: str | None = None
    days: list[ResolvedDay] = field(default_factory=list)
    review: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        """'reception_log:14' - where the report came from."""
        return f"{self.report.source}:{self.report.source_ref}"


@dataclass
class Absence:
    """One employee absent on one date: the merged reports, notice, payroll code and decision."""
    employee: Employee
    day: date
    shift: str
    building: str | None
    category: str
    reports: list[ReportEvent]
    received_at: datetime | None
    notice_hours: float | None = None
    code: str | None = None
    code_reason: str = ""
    confidence: float = 1.0
    review: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # how each fact was worked out (for audit)
    payroll_id: int | None = None
    decision: str = ""            # written | held_for_review (set after the payroll step)
    submission_id: str = ""
    final_status: str = ""

    @property
    def primary(self) -> ReportEvent:
        """The earliest report of this absence."""
        return self.reports[0]


# ---- step 2: when was it received --------------------------------------------------


def _stated_time(text: str | None, on: date, tz) -> datetime | None:
    """Turn the LLM's 'HH:MM' into a datetime on the given date (or None)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not m:
        return None
    return datetime.combine(on, time(int(m.group(1)), int(m.group(2))), tzinfo=tz)


def received_time(r: CalloutReport, x: CalloutExtraction) -> tuple[datetime | None, str, list[str]]:
    """Returns (received_at, how we know, review reasons)."""
    tz = r.logged_at.tzinfo
    stated = _stated_time(x.stated_received_time, r.logged_at.date(), tz)
    if r.source == RECEPTION:
        if r.channel.lower().startswith("voice"):
            if r.vm_received_at:
                return r.vm_received_at, "voicemail system timestamp", []
            if stated and stated <= r.logged_at:
                return stated, f"voicemail has no system timestamp; caller says ~{x.stated_received_time}", \
                    ["voicemail has no system timestamp; notice depends on the caller's own stated time"]
            return r.logged_at, "voicemail has no system timestamp; transcription time used", \
                ["voicemail has no system timestamp; notice measured from transcription time"]
        return r.logged_at, "phone call logged by reception", []
    # chat: the manager's message time, unless the manager says when the text actually arrived
    if stated and stated <= r.logged_at:
        return stated, f"area manager says the text arrived at {x.stated_received_time} (message sent {r.logged_at:%H:%M})", []
    return r.logged_at, "chat message time", []


# ---- step 3: who -------------------------------------------------------------------


def _dispatch_tiebreak(candidates: list[Employee], day: date, shift: str | None, postings: list[dict],
                       directory: EmployeeDirectory, site_building: dict[str, str]) -> Employee | None:
    """If two employees share a name, a call-out posting on that date at one
    candidate's building, naming that candidate, settles it."""
    hits = set()
    for p in postings:
        if p.get("reason") != "CALLOUT" or p.get("shift_date") != day.isoformat():
            continue
        if shift and p.get("shift") != shift[0].upper():
            continue
        for e in candidates:
            if name_score(p.get("absent_employee_text", ""), e) >= 0.85 and \
                    site_building.get(p["site_id"]) == directory.home_building(e, day):
                hits.add(e.hr_id)
    return directory.by_hr[hits.pop()] if len(hits) == 1 else None


# ---- the whole flow -------------------------------------------------------------


def build_absences(reports: list[CalloutReport], directory: EmployeeDirectory, buildings: BuildingRegistry,
                   postings: list[dict], site_building: dict[str, str], period: tuple[date, date],
                   findings: Findings) -> tuple[list[Absence], list[ReportEvent]]:
    """Steps 1-6: read every report, work out when/who/which days, merge duplicates, then code them."""
    events: list[ReportEvent] = []

    for r in reports:
        try:
            x = extract_callout(r)
        except LLMUnavailable as exc:
            if r.source == RECEPTION:
                findings.exception(r.source, r.source_ref, f"message not read: {exc}", "held for manual review", 0.0)
                events.append(ReportEvent(r, None, review=[f"message could not be read by the LLM: {exc}"]))
            else:
                findings.exception(r.source, r.source_ref, f"chat message not read: {exc}", "ignored", 0.0)
            continue

        if not x.is_absence or x.category in ("not_absence", "vacation"):
            if r.source == RECEPTION or x.category != "not_absence" or x.is_followup:
                what = "planned vacation, not a call-out" if x.category == "vacation" else "not an absence"
                findings.exception(r.source, r.source_ref, f"{what}: {x.english_summary}",
                                   "excluded from call-outs and payroll", x.confidence)
            continue

        ev = ReportEvent(r, x)
        ev.received_at, ev.received_note, reasons = received_time(r, x)
        ev.review += reasons
        spoken_at = ev.received_at or r.logged_at
        ev.days = resolve_days(x.days, spoken_at) if x.days else \
            [ResolvedDay(spoken_at.date(), 0.5, "no day stated; assumed the day it was received")]

        name = r.caller if r.source == RECEPTION and r.caller else (x.employee_name or "")
        building = buildings.match_name(x.building_text)[0] if x.building_text else None
        first_day = ev.days[0].day
        m = directory.match_name(name, first_day, building, x.shift)
        if m.employee is None and x.employee_name and x.employee_name != name:
            m = directory.match_name(x.employee_name, first_day, building, x.shift)
        if m.employee is None and len(m.candidates) > 1:
            pick = _dispatch_tiebreak(m.candidates, first_day, x.shift, postings, directory, site_building)
            if pick:
                m.employee, m.confidence = pick, 0.8
                m.reason += f"; resolved to {pick.hr_id} by the dispatch call-out posting that day"
                findings.conflict(f"{r.source} {r.source_ref}: '{name}' matches several employees; the dispatch "
                                  f"posting for {first_day} identifies {pick.hr_id} {pick.full_name}.")
        ev.employee, ev.match_confidence, ev.match_reason = m.employee, m.confidence, m.reason
        if m.employee is None:
            ev.review.append(m.reason)
            findings.exception(r.source, r.source_ref, m.reason, "held for review", 0.0)
        ev.shift = x.shift or ({"DAY": "day", "NIGHT": "night"}.get(m.employee.shift) if m.employee else None)
        if ev.shift is None:
            ev.review.append("shift not stated and employee has no fixed shift")
        events.append(ev)

    # ---- step 4: one absence per employee per date -------------------------------
    absences: dict[tuple[str, date], Absence] = {}
    unresolved: list[ReportEvent] = []
    for ev in sorted(events, key=lambda e: e.received_at or e.report.logged_at):
        if ev.employee is None or ev.extraction is None:
            unresolved.append(ev)
            continue
        for rd in ev.days:
            key = (ev.employee.hr_id, rd.day)
            if key in absences:
                a = absences[key]
                a.reports.append(ev)
                if a.category != ev.extraction.category:
                    a.review.append(f"reports disagree on the reason: {a.category} vs {ev.extraction.category}")
                findings.exception(ev.report.source, ev.report.source_ref,
                                   f"same absence as {a.primary.ref} ({ev.employee.hr_id} on {rd.day})",
                                   "merged; counted and written once", 0.95)
                continue
            a = Absence(employee=ev.employee, day=rd.day, shift=ev.shift or "day",
                        building=directory.home_building(ev.employee, rd.day), category=ev.extraction.category,
                        reports=[ev], received_at=ev.received_at)
            a.confidence = min(ev.extraction.confidence, ev.match_confidence, rd.confidence)
            a.review += ev.review
            a.notes += [f"date: {rd.note}", f"received: {ev.received_note}", f"employee: {ev.match_reason}"]
            absences[key] = a

    result = sorted(absences.values(), key=lambda a: (a.day, a.received_at.timestamp() if a.received_at else 0))
    start, end = period
    for a in result:
        if not is_workday(a.day):
            a.review.append(f"{a.day:%A} is not a scheduled workday")
        if not (start <= a.day <= end):
            a.review.append(f"{a.day} is outside the pay period {start}..{end}")
        if not a.employee.employed_on(a.day):
            a.review.append(f"{a.employee.hr_id} not employed on {a.day}")
        if a.received_at:
            a.notice_hours = round(notice_hours(a.received_at, a.day, a.shift), 2)
    _code_all(result, directory, findings)
    return result, unresolved


def _code_all(absences: list[Absence], directory: EmployeeDirectory, findings: Findings) -> None:
    """Step 5 + 6: code in date order per employee, tracking sick days, the
    clinic day and bereavement days as they are used."""
    sick_left = {e.hr_id: e.sick_days_start for e in directory.employees}
    clinic_used = {e.hr_id: e.clinic_day_used for e in directory.employees}
    bereavement_days: dict[str, int] = {}

    for a in sorted(absences, key=lambda a: (a.employee.hr_id, a.day)):
        e = a.employee
        x = a.primary.extraction
        if a.category == "bereavement":
            relation = (x.bereavement_relation or "").strip().lower()
            if relation and relation not in IMMEDIATE_FAMILY:
                a.review.append(f"bereavement for '{relation}' may not be immediate family (14.5)")
            bereavement_days[e.hr_id] = bereavement_days.get(e.hr_id, 0) + 1
            if bereavement_days[e.hr_id] > BEREAVEMENT_MAX_DAYS:
                a.review.append("more than 3 bereavement days (14.5)")
        result = code_absence(CodingInput(
            category=a.category, union_member=e.union_member,
            days_employed=(a.day - e.hire_date).days, notice_hours=a.notice_hours,
            sick_days_left=sick_left[e.hr_id], clinic_day_available=not clinic_used[e.hr_id],
        ))
        a.code, a.code_reason = result.code, result.reason
        # Held absences still use up the day they would use, so later absences
        # are coded as if the reviewer approves (logged in the decisions log).
        if result.uses_sick_day:
            sick_left[e.hr_id] -= 1
        if result.uses_clinic_day:
            clinic_used[e.hr_id] = True
        if result.code is None:
            a.review.append(result.reason)

        a.payroll_id = directory.payroll_id.get(e.hr_id)
        if a.payroll_id is None:
            a.review.append("employee not found in payroll")
        else:
            status = directory.payroll_status.get(a.payroll_id, "")
            if status != "ACTIVE":
                a.review.append(f"payroll status is {status or 'unknown'}; payroll would reject the entry")
        if a.confidence < REVIEW_THRESHOLD:
            a.review.append(f"low confidence ({a.confidence:.2f}) in reading or matching the call-out")
