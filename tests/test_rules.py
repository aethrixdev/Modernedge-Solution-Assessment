"""Deterministic rules: payroll coding, dates, notice, billing."""
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from pipeline.common import money
from pipeline.rules.billing import billable_amount, overtime_dollars
from pipeline.rules.coding import CodingInput, code_absence
from pipeline.rules.dates import resolve_days
from pipeline.rules.shifts import notice_hours, shift_end, weekdays

TZ = ZoneInfo("America/New_York")


def inp(category="sick", union=True, days=1000, notice=10.0, sick=5, clinic=True):
    return CodingInput(category, union, days, notice, sick, clinic)


# ---- coding table: one test per row, plus the order of the checks ----------------
@pytest.mark.parametrize("kwargs, code", [
    (dict(), "SKP"),                                   # eligible, notice, balance
    (dict(union=False), "SKU"),                        # not in bargaining unit
    (dict(days=89), "SKU"),                            # probation
    (dict(days=90), "SKP"),                            # probation complete on day 90
    (dict(notice=3.99), "LTN"),                        # would be paid, short notice
    (dict(notice=4.0), "SKP"),                         # exactly 4 hours is enough
    (dict(sick=0), "SKU"),                             # no balance
    (dict(sick=0, notice=1), "LTN"),                   # order: notice is checked before balance
    (dict(union=False, notice=1), "SKU"),              # order: eligibility before notice
    (dict(category="clinic"), "CLN"),
    (dict(category="clinic", clinic=False), "PER"),    # clinic day already used
    (dict(category="clinic", union=False), "PER"),     # not eligible -> personal
    (dict(category="clinic", notice=2), "LTN"),
    (dict(category="clinic", days=10), "CLN"),         # probation is for sick leave only
    (dict(category="bereavement", union=False), "BRV"),  # applies to all employees
    (dict(category="bereavement", notice=1), "LTN"),
    (dict(category="jury_duty", notice=-5), "JRY"),    # no notice rule
    (dict(category="family_emergency", notice=0), "FEM"),
    (dict(category="personal"), "PER"),
])
def test_coding_table(kwargs, code):
    assert code_absence(inp(**kwargs)).code == code


def test_only_skp_uses_a_sick_day_and_only_cln_uses_the_clinic_day():
    assert code_absence(inp()).uses_sick_day
    assert not code_absence(inp(notice=1)).uses_sick_day
    assert code_absence(inp(category="clinic")).uses_clinic_day
    assert not code_absence(inp(category="clinic", notice=1)).uses_clinic_day


def test_unknown_notice_is_not_guessed():
    assert code_absence(inp(notice=None)).code is None
    assert code_absence(inp(category="jury_duty", notice=None)).code == "JRY"


def ref(kind, value):
    return NS(kind=kind, value=value)


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


# ---- dates --------------------------------------------------------------------------
def test_tonight_and_tomorrow():
    assert resolve_days([ref("relative", "today")], at("2026-10-07T10:07"))[0].day == date(2026, 10, 7)
    assert resolve_days([ref("relative", "tomorrow")], at("2026-10-05T22:00"))[0].day == date(2026, 10, 6)


def test_tomorrow_after_midnight_means_the_coming_shift():
    r = resolve_days([ref("relative", "tomorrow")], at("2026-10-29T00:25"))[0]
    assert r.day == date(2026, 10, 29) and r.confidence < 1


def test_weekday_is_the_next_one():
    # said Friday 10/09 -> Monday 10/12 ; said Thursday 10/22 -> Tuesday 10/27
    assert resolve_days([ref("weekday", "monday")], at("2026-10-09T11:20"))[0].day == date(2026, 10, 12)
    assert resolve_days([ref("weekday", "tuesday")], at("2026-10-22T13:48"))[0].day == date(2026, 10, 27)


def test_multi_day_absence():
    days = resolve_days([ref("relative", "today"), ref("weekday", "friday")], at("2026-10-22T08:36"))
    assert [d.day for d in days] == [date(2026, 10, 22), date(2026, 10, 23)]


# ---- shifts and notice --------------------------------------------------------------
def test_notice_from_voicemail_time_not_transcription():
    # voicemail 04:34, transcribed 08:49; day shift 07:00 -> 2.43h
    assert round(notice_hours(at("2026-10-07T04:34"), date(2026, 10, 7), "day"), 2) == 2.43
    assert notice_hours(at("2026-10-15T10:32"), date(2026, 10, 15), "night") > 4


def test_night_shift_ends_at_midnight_on_the_next_day():
    assert shift_end(date(2026, 10, 9), "night", TZ) == at("2026-10-10T00:00")


def test_weekdays_in_october_2026():
    assert len(weekdays(date(2026, 10, 1), date(2026, 10, 31))) == 22


# ---- billing: checked against amounts on the client's own invoices -----------------
def test_money_rounds_half_up_like_accounting():
    assert money("2.675") == Decimal("2.68") and round(2.675, 2) == 2.67   # float rounding gets it wrong


@pytest.mark.parametrize("rate, ot, burden, markup, expected", [
    ("25.35", True, "0.32", "0.15", "461.78"),   # union porter on OT, Meridian (NBS-40314)
    ("24.10", False, "0.32", "0.15", "292.67"),  # union floater cleaner, Meridian (NBS-40315)
    ("21.00", False, "0.18", "0.15", "227.98"),  # temp cleaner, Meridian (NBS-40316)
    ("21.00", False, "0.18", "0.12", "222.03"),  # temp cleaner, Corlett (NFS-7122)
    ("29.80", True, "0.32", "0.12", "528.68"),   # handyperson on OT, Corlett (NFS-7123)
])
def test_billable_amount_matches_invoices(rate, ot, burden, markup, expected):
    assert billable_amount(Decimal(rate), ot, Decimal(burden), Decimal(markup)) == Decimal(expected)


def test_overtime_dollars_exclude_burden():
    assert overtime_dollars(Decimal("25.35")) == Decimal("304.20")
