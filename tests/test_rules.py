"""Deterministic rules: dates, shifts and notice."""
from datetime import date, datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from pipeline.rules.dates import resolve_days
from pipeline.rules.shifts import notice_hours, shift_end, weekdays

TZ = ZoneInfo("America/New_York")


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
