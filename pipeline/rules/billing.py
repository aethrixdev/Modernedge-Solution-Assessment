"""Pay rates, overtime and the billable amount of one coverage shift.

Billable amount (KPI definitions, Exhibit C):
    8 h x base rate x (1.5 if overtime) x (1 + burden) x (1 + markup), rounded to the cent.
Base rate and burden belong to the COVERING employee:
- union member: the CBA wage schedule rate for their job class on that date
- non-union / temp: the HR rate card for their job class
- burden: 32% union, 18% non-union (from the same document)
"""
from datetime import date
from decimal import Decimal

from ..common import money
from ..ingest.contracts import WageSchedule
from ..ingest.hr import Employee
from .shifts import SHIFT_HOURS

OT_MULTIPLIER = Decimal("1.5")   # CBA Appendix B


def base_rate(e: Employee, wages: WageSchedule, on: date) -> Decimal | None:
    """Hourly rate of the covering employee: union wage schedule by date, or the non-union rate card."""
    if e.union_member:
        return wages.union_rate(e.job_class, on)
    return wages.nonunion_rates.get(e.job_class)


def burden(e: Employee, wages: WageSchedule) -> Decimal:
    """Labour burden: 32% union, 18% non-union (from the wage schedule)."""
    return wages.union_burden if e.union_member else wages.nonunion_burden


def overtime_dollars(rate: Decimal) -> Decimal:
    """OT KPI: 8 hours x base rate x 1.5, wages only (no burden)."""
    return money(SHIFT_HOURS * rate * OT_MULTIPLIER)


def billable_amount(rate: Decimal, overtime: bool, burden_pct: Decimal, markup: Decimal) -> Decimal:
    """8h x rate (x1.5 on overtime) x (1 + burden) x (1 + markup), rounded to the cent."""
    amount = Decimal(SHIFT_HOURS) * rate
    if overtime:
        amount *= OT_MULTIPLIER
    return money(amount * (1 + burden_pct) * (1 + markup))
