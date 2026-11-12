"""Call-outs -> absences: received time, merging duplicates, sick-day order, holds.

The LLM is replaced by fixed readings, so these tests check only our own logic.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pipeline import callouts
from pipeline.common import Findings
from pipeline.ingest.callout_sources import CHAT, RECEPTION, CalloutReport
from pipeline.ingest.hr import Employee
from pipeline.llm.callout_extractor import CalloutExtraction, DayReference
from pipeline.resolve.buildings import BuildingRegistry
from pipeline.resolve.employees import EmployeeDirectory

TZ = ZoneInfo("America/New_York")
OCT = (date(2026, 10, 1), date(2026, 10, 31))


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def reading(day="today", category="sick", shift="night", stated=None, name="Ana Lopez"):
    return CalloutExtraction(is_absence=True, is_followup=False, employee_name=name, building_text=None, shift=shift,
                             days=[DayReference(kind="relative", value=day, text=day)], category=category,
                             bereavement_relation=None, stated_received_time=stated, language="en",
                             english_summary="", confidence=0.95)


def report(source, ref, logged, channel="Phone", vm=None, caller="Ana Lopez"):
    return CalloutReport(source, ref, at(logged), channel, at(vm) if vm else None, caller, "msg", "Rita")


def directory(sick=5, status="ACTIVE"):
    e = Employee("E10001", 10001, "LOPEZ", "Ana", "", "Cleaner", True, "BLD-01", "410", "NIGHT", "PERMANENT",
                 date(2020, 1, 1), None, "ACTIVE", sick, False, "English", 2)
    d = EmployeeDirectory([e])
    d.payroll_id, d.payroll_status = {"E10001": 10001}, {10001: status}
    return d


def run(reports_and_readings, monkeypatch, **dir_kwargs):
    answers = {r.source_ref: x for r, x in reports_and_readings}
    monkeypatch.setattr(callouts, "extract_callout", lambda r: answers[r.source_ref])
    f = Findings()
    absences, _ = callouts.build_absences([r for r, _ in reports_and_readings], directory(**dir_kwargs),
                                          BuildingRegistry(["Halcyon Tower"]), [], {}, OCT, f)
    return absences, f


# ---- when was it received ----------------------------------------------------------
def test_voicemail_uses_the_phone_system_time_not_the_transcription_time():
    r = report(RECEPTION, "5", "2026-10-07T08:49", "Voicemail", vm="2026-10-07T04:34")
    assert callouts.received_time(r, reading())[0] == at("2026-10-07T04:34")


def test_voicemail_without_system_time_is_flagged():
    r = report(RECEPTION, "5", "2026-10-19T08:49", "Voicemail")
    when, _, review = callouts.received_time(r, reading(stated="06:00"))
    assert when == at("2026-10-19T06:00") and "no system timestamp" in review[0]


def test_chat_uses_the_time_the_manager_says_the_text_arrived():
    r = report(CHAT, "10/15/26, 1:20:00 PM", "2026-10-15T13:20", "Chat", caller="Gloria M")
    assert callouts.received_time(r, reading(stated="10:32"))[0] == at("2026-10-15T10:32")


# ---- the whole flow -------------------------------------------------------------
def test_same_absence_reported_twice_is_merged_and_notice_uses_the_earliest(monkeypatch):
    first = report(RECEPTION, "2", "2026-10-07T10:00")
    second = report(CHAT, "10/07/26, 2:00:00 PM", "2026-10-07T14:00", "Chat", caller="Gloria M")
    [a], f = run([(first, reading()), (second, reading())], monkeypatch)
    assert len(a.reports) == 2 and a.notice_hours == 6.0 and a.code == "SKP"
    assert "merged" in f.exceptions[0].action_taken


def test_sick_days_are_used_up_in_date_order(monkeypatch):
    mon = report(RECEPTION, "2", "2026-10-05T09:00")
    tue = report(RECEPTION, "3", "2026-10-06T09:00")
    absences, _ = run([(tue, reading()), (mon, reading())], monkeypatch, sick=1)
    assert [(a.day.day, a.code) for a in absences] == [(5, "SKP"), (6, "SKU")]   # only one paid day left


def test_short_notice_gives_ltn(monkeypatch):
    [a], _ = run([(report(RECEPTION, "2", "2026-10-09T13:36"), reading())], monkeypatch)
    assert a.notice_hours == pytest.approx(2.4) and a.code == "LTN"


def test_payroll_status_not_active_is_held_for_review(monkeypatch):
    [a], _ = run([(report(RECEPTION, "2", "2026-10-07T10:00"), reading())], monkeypatch, status="PENDING_SETUP")
    assert any("PENDING_SETUP" in r for r in a.review)
