"""The payroll writer against a fake payroll that misbehaves the way the real
one can: 503s, and 503s AFTER it has already accepted the absence."""
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from pipeline.callouts import Absence
from pipeline.clients import http
from pipeline.payroll_writer import MARKER, PayrollWriter


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body or {}, headers or {}, str(body)

    def json(self):
        return self._body


class FakePayroll:
    """In-memory payroll. `script` decides what each POST does:
    'ok' accept, '503' refuse, 'lost' accept but answer 503, '422' validation error."""
    def __init__(self, script=("ok",)):
        self.script = list(script)
        self.rows = []
        self.posts = 0

    def list_absences(self, employee_id=None, date=None, status=None):
        return [r for r in self.rows if r["employee_id"] == employee_id and r["date"] == date]

    def create_absence(self, employee_id, date, code, hours, note=None):
        self.posts += 1
        action = self.script.pop(0) if self.script else "ok"
        if action == "422":
            return Resp(422, {"detail": {"error": "PERIOD_CLOSED"}})
        if action in ("ok", "lost"):
            sid = f"SUB-{len(self.rows) + 1}"
            self.rows.append({"submission_id": sid, "employee_id": employee_id, "date": date, "code": code,
                              "note": note, "status": "RECEIVED"})
            return Resp(202, {"submission_id": sid}) if action == "ok" else Resp(503)
        return Resp(503)

    def delete_absence(self, sid):
        next(r for r in self.rows if r["submission_id"] == sid)["status"] = "VOIDED"

    def get_absence(self, sid):
        r = next(r for r in self.rows if r["submission_id"] == sid)
        if r["status"] == "RECEIVED":
            r["status"] = "POSTED"
        return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http, "sleep", lambda s: None)


def absence(code="SKP", review=()):
    rep = NS(report=NS(source="reception_log", source_ref="7"), ref="reception_log:7")
    a = Absence(employee=NS(hr_id="E1"), day=date(2026, 10, 14), shift="night", building="B", category="sick",
                reports=[rep], received_at=datetime(2026, 10, 14, 9, 0))
    a.code, a.payroll_id, a.review = code, 10001, list(review)
    return a


def live(fake):
    return [r for r in fake.rows if r["status"] not in ("VOIDED", "REJECTED")]


def test_writes_once_and_collects_final_status():
    fake = FakePayroll()
    w = PayrollWriter(fake, dry_run=False, poll_interval=0)
    a = absence()
    w.write(a)
    w.collect_final_status([a])
    assert a.decision == "written" and a.final_status == "POSTED" and len(live(fake)) == 1


def test_lost_202_is_not_posted_twice():
    fake = FakePayroll(script=["lost"])   # accepted, but the client sees a 503
    a = absence()
    PayrollWriter(fake, dry_run=False).write(a)
    assert fake.posts == 1 and len(live(fake)) == 1 and a.submission_id == "SUB-1"


def test_retries_a_real_503_then_succeeds():
    fake = FakePayroll(script=["503", "503", "ok"])
    a = absence()
    PayrollWriter(fake, dry_run=False).write(a)
    assert fake.posts == 3 and len(live(fake)) == 1 and a.decision == "written"


def test_rerun_adopts_instead_of_posting():
    fake = FakePayroll()
    PayrollWriter(fake, dry_run=False).write(absence())
    a = absence()
    PayrollWriter(fake, dry_run=False).write(a)
    assert fake.posts == 1 and a.submission_id == "SUB-1"


def test_own_entry_with_wrong_code_is_replaced():
    fake = FakePayroll()
    PayrollWriter(fake, dry_run=False).write(absence(code="SKU"))
    a = absence(code="SKP")
    PayrollWriter(fake, dry_run=False).write(a)
    assert [r["code"] for r in live(fake)] == ["SKP"]


def test_someone_elses_entry_is_never_touched():
    fake = FakePayroll()
    fake.rows.append({"submission_id": "MANUAL-1", "employee_id": 10001, "date": "2026-10-14", "code": "PER",
                      "note": "entered by payroll clerk", "status": "POSTED"})
    a = absence(code="SKP")
    PayrollWriter(fake, dry_run=False).write(a)
    assert a.decision == "held_for_review" and fake.posts == 0 and fake.rows[0]["status"] == "POSTED"


def test_validation_error_goes_to_review():
    fake = FakePayroll(script=["422"])
    a = absence()
    PayrollWriter(fake, dry_run=False).write(a)
    assert a.decision == "held_for_review" and "PERIOD_CLOSED" in a.review[-1]


def test_dry_run_sends_nothing():
    fake = FakePayroll()
    w = PayrollWriter(fake, dry_run=True)
    a = absence()
    w.write(a)
    assert fake.posts == 0 and a.final_status == "DRY_RUN" and w.audit[-1]["action"] == "dry_run"


def test_items_needing_review_are_not_written():
    fake = FakePayroll()
    a = absence(review=["low confidence"])
    PayrollWriter(fake, dry_run=False).write(a)
    assert a.decision == "held_for_review" and fake.posts == 0


def test_note_tags_our_entries():
    assert PayrollWriter(FakePayroll(), dry_run=False).note(absence()).startswith(MARKER)
