"""Personnel Action Forms: only fully approved forms change anything."""
from datetime import date
from pathlib import Path

import pytest

from pipeline.common import Findings
from pipeline.ingest.hr import Employee, load_roster
from pipeline.llm.paf_extractor import Approval, PafExtraction
from pipeline.resolve import paf
from pipeline.resolve.employees import EmployeeDirectory

HR = Path("../ModernEdge_AI_Solutions_Engineer_Assessment/data/hr")
ALL = ["Human Resources", "Operations", "Payroll"]


def emp():
    return Employee("E10028", 10028, "LEKA", "Mimoza", "", "Cleaner", True, "BLD-03", "430", "NIGHT", "PERMANENT",
                    date(2020, 1, 1), None, "ACTIVE", 5, False, "Albanian", 2)


def form(signed, action="transfer"):
    return PafExtraction(action=action, employee_id="E10028", employee_name="Mimoza Leka", job_class="Cleaner",
                         effective_date="10/19/2026", from_location="BLD-03 (Meridian Plaza), Night",
                         to_location="BLD-02 (Meridian One), Night", last_day_worked=None, remarks=None,
                         approvals=[Approval(role=r, signed=r in signed, signer=None, date=None) for r in ALL],
                         confidence=0.95)


def apply(monkeypatch, tmp_path, extraction):
    (tmp_path / "PAF_scan_1.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(paf, "extract_paf", lambda path: extraction)
    d, f = EmployeeDirectory([emp()]), Findings()
    return paf.apply_pafs(tmp_path, d, f), d, f


def test_fully_approved_transfer_changes_home_location_from_its_date(monkeypatch, tmp_path):
    [rec], d, _ = apply(monkeypatch, tmp_path, form(ALL))
    assert rec["applied"]
    e = d.by_hr["E10028"]
    assert d.home_location(e, date(2026, 10, 16)) == "BLD-03" and d.home_location(e, date(2026, 10, 19)) == "BLD-02"


def test_form_missing_an_approval_has_no_effect(monkeypatch, tmp_path):
    [rec], d, f = apply(monkeypatch, tmp_path, form(["Human Resources"]))
    assert not rec["applied"] and d.transfers == {}
    assert "Operations, Payroll" in rec["note"] and f.conflicts


@pytest.mark.skipif(not HR.exists(), reason="sample data not found")
def test_sample_forms_with_saved_readings(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("LLM_FIXTURES_DIR", "tests/fixtures/llm")
    d = EmployeeDirectory(load_roster(HR / "hr_roster.csv", Findings()))
    applied = {r["employee_id"]: r["applied"] for r in paf.apply_pafs(HR / "personnel_action_forms", d, Findings())}
    assert applied == {"E10044": False, "E10010": True, "E10061": True, "E10028": True}   # E10044: only HR signed
    assert d.by_hr["E10010"].termination_date == date(2026, 10, 9)
