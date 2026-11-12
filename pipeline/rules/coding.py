"""Payroll absence coding - Article 14 and the Employer Payroll Coding Guide.

Deterministic and pure: the same inputs always give the same code. The
order is the one the guide prescribes:
    1. eligibility (union membership, 14.10; 90-day probation, 14.3)
    2. notice (at least 4 hours before shift start, 14.2 / 14.9)
    3. balance (sick days left, after earlier absences in date order)

| category          | result                                                                 |
|-------------------|------------------------------------------------------------------------|
| sick              | not eligible -> SKU; short notice -> LTN; no days left -> SKU; else SKP |
| clinic            | not eligible or already used -> PER; short notice -> LTN; else CLN     |
| bereavement       | (all employees) short notice -> LTN; else BRV                          |
| jury_duty         | JRY (no notice rule, 14.6)                                             |
| family_emergency  | FEM                                                                    |
| personal          | PER                                                                    |
"""
from dataclasses import dataclass

from .shifts import NOTICE_HOURS

PROBATION_DAYS = 90   # 14.3


@dataclass(frozen=True)
class CodingInput:
    """Everything the coding rules need about one absence day."""
    category: str
    union_member: bool
    days_employed: int            # calendar days from hire date to the absence date
    notice_hours: float | None    # None = we don't know when it was received
    sick_days_left: int           # after earlier absences this month
    clinic_day_available: bool    # not used before this month, and not earlier this month


@dataclass(frozen=True)
class CodingResult:
    """The payroll code (None = needs review), the reason, and which balance it uses."""
    code: str | None              # None = cannot be coded automatically
    reason: str
    uses_sick_day: bool = False
    uses_clinic_day: bool = False


def code_absence(i: CodingInput) -> CodingResult:
    """Apply Article 14 in the guide's order: eligibility, then notice, then balance."""
    notice_ok = None if i.notice_hours is None else i.notice_hours >= NOTICE_HOURS
    notice_txt = "notice unknown" if i.notice_hours is None else f"notice {i.notice_hours:.1f}h"

    if i.category == "sick":
        if not i.union_member:
            return CodingResult("SKU", "sick; not in bargaining unit (14.10), no paid sick leave")
        if i.days_employed < PROBATION_DAYS:
            return CodingResult("SKU", f"sick; only {i.days_employed} days employed, under the 90-day probation (14.3)")
        if notice_ok is None:
            return CodingResult(None, "sick; would be paid but notice cannot be measured")
        if not notice_ok:
            return CodingResult("LTN", f"sick; eligible but {notice_txt} < {NOTICE_HOURS}h (14.9)")
        if i.sick_days_left <= 0:
            return CodingResult("SKU", f"sick; eligible, {notice_txt}, but no paid sick days left")
        return CodingResult("SKP", f"sick; eligible, {notice_txt}, {i.sick_days_left} sick day(s) left before this one",
                            uses_sick_day=True)

    if i.category == "clinic":
        if not i.union_member:
            return CodingResult("PER", "clinic visit; not in bargaining unit, no clinic day (14.10)")
        if not i.clinic_day_available:
            return CodingResult("PER", "clinic visit; annual clinic day already used")
        if notice_ok is None:
            return CodingResult(None, "clinic day; notice cannot be measured")
        if not notice_ok:
            return CodingResult("LTN", f"clinic day; eligible but {notice_txt} < {NOTICE_HOURS}h (14.9)")
        return CodingResult("CLN", f"clinic day; eligible, {notice_txt}", uses_clinic_day=True)

    if i.category == "bereavement":
        if notice_ok is None:
            return CodingResult(None, "bereavement; notice cannot be measured")
        if not notice_ok:
            return CodingResult("LTN", f"bereavement; {notice_txt} < {NOTICE_HOURS}h (14.9)")
        return CodingResult("BRV", f"bereavement; {notice_txt}")

    if i.category == "jury_duty":
        return CodingResult("JRY", "jury duty; no notice requirement (14.6)")
    if i.category == "family_emergency":
        return CodingResult("FEM", "family emergency; unpaid, no balance used (14.7)")
    if i.category == "personal":
        return CodingResult("PER", "personal day; unpaid (14.8)")
    return CodingResult(None, f"absence type '{i.category}' has no automatic code")
