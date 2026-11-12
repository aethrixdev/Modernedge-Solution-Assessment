"""Matching buildings and people across sources."""
from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline.common import Findings, norm
from pipeline.ingest.hr import Employee, load_roster
from pipeline.ingest.time_attendance import load_punches
from pipeline.resolve.buildings import BuildingRegistry
from pipeline.resolve.employees import EmployeeDirectory, Transfer, name_score

BUILDINGS = ["Halcyon Tower", "Meridian One", "Meridian Plaza", "Corlett Building", "Bramwell House", "Bramwell Annex"]
DATA = Path("../ModernEdge_AI_Solutions_Engineer_Assessment/data")


def test_norm_removes_accents_and_punctuation():
    assert norm("Përshëndetje, WALKER Brian!") == "PERSHENDETJE WALKER BRIAN"
    assert norm(None) == ""


# ---- buildings -------------------------------------------------------------------
def test_building_spellings():
    r = BuildingRegistry(BUILDINGS)
    for text, expected in [("HALCYON TWR", "Halcyon Tower"), ("MERIDIAN 1", "Meridian One"),
                           ("Meridian One Bldg", "Meridian One"), ("Meridian Plz", "Meridian Plaza"),
                           ("CORLETT BLDG", "Corlett Building"), ("Corlett", "Corlett Building"),
                           ("BRAMWELL HSE", "Bramwell House"), ("BRAMWELL ANX", "Bramwell Annex"),
                           ("Halcyon", "Halcyon Tower")]:
        assert r.match_name(text)[0] == expected, text


def test_ambiguous_and_unknown_buildings_are_not_guessed():
    r = BuildingRegistry(BUILDINGS)
    assert r.match_name("Bramwell")[0] is None          # two Bramwell buildings
    assert r.match_name("TRAINING ROOM - HQ")[0] is None


def test_code_without_a_name_is_mapped_by_evidence():
    r, f = BuildingRegistry(BUILDINGS), Findings()
    assert r.register_by_evidence("hr", "BLD-04", Counter({"Corlett Building": 9, "Halcyon Tower": 1}), f,
                                  "punches") == "Corlett Building"
    assert r.aliases[("hr", "BLD-04")].confidence == 0.9 and not f.exceptions
    r.register_by_evidence("hr", "BLD-09", Counter({"Corlett Building": 1, "Halcyon Tower": 1}), f, "punches")
    assert "split" in f.exceptions[0].issue                                   # weak evidence is flagged


@pytest.mark.skipif(not DATA.exists(), reason="sample data not found")
def test_every_time_clock_site_resolves_to_one_building():
    sites = {p.site_text for p in load_punches(DATA / "time_attendance", ZoneInfo("America/New_York"), Findings())}
    r = BuildingRegistry(BUILDINGS)
    assert {s: r.match_name(s)[0] for s in sites if r.match_name(s)[0] is None} == {}


# ---- employees -------------------------------------------------------------------
def emp(hr_id, first, last, loc="BLD-01", shift="NIGHT", etype="PERMANENT"):
    return Employee(hr_id, int(hr_id[1:]), last.upper(), first, "", "Cleaner", True, loc, "410", shift, etype,
                    date(2020, 1, 1), None, "ACTIVE", 5, False, "English", 2)


def test_name_formats():
    e = emp("E10049", "Angela", "Brown")
    for text in ["Angela Brown", "Brown, Angela", "Angela B.", "Angela B", "A. Brown", "BROWN, ANGELA J"]:
        assert name_score(text, e) >= 0.85, text
    assert name_score("Angela Moore", e) == 0
    assert name_score("Kevin B.", e) == 0


def test_same_name_is_split_by_building_or_left_ambiguous():
    d = EmployeeDirectory([emp("E10006", "Maria", "Gonzalez", "BLD-01"), emp("E10036", "Maria", "Gonzalez", "BLD-04")])
    d.location_building = {"BLD-01": "Halcyon Tower", "BLD-04": "Corlett Building"}
    day = date(2026, 10, 21)
    assert d.match_name("Maria Gonzalez", day, "Corlett Building").employee.hr_id == "E10036"
    m = d.match_name("Maria Gonzalez", day)
    assert m.employee is None and len(m.candidates) == 2


def test_approved_transfer_changes_home_building_from_its_date():
    e = emp("E10028", "Mimoza", "Leka", "BLD-03")
    d = EmployeeDirectory([e])
    d.location_building = {"BLD-02": "Meridian One", "BLD-03": "Meridian Plaza"}
    d.transfers["E10028"] = [Transfer(date(2026, 10, 19), "BLD-03", "BLD-02", "PAF")]
    assert d.home_building(e, date(2026, 10, 16)) == "Meridian Plaza"
    assert d.home_building(e, date(2026, 10, 19)) == "Meridian One"


def test_mistyped_dispatch_hr_number_is_linked_by_name():
    d = EmployeeDirectory([emp("E10057", "Miguel", "Martinez", "FLOAT", "FLEX", "FLOATER")])
    f = Findings()
    d.link_dispatch_workers([{"worker_id": "W-3190", "display_name": "Miguel Martinez", "hr_employee_no": "E10075"}], f)
    assert d.worker["W-3190"].hr_id == "E10057"
    assert f.exceptions and f.conflicts


def test_payroll_id_is_checked_against_the_payroll_name():
    d = EmployeeDirectory([emp("E10001", "Ana", "Lopez"), emp("E10002", "Ben", "Cole")])
    f = Findings()
    d.link_payroll([{"employee_id": 10001, "name": "Lopez, Ana", "status": "ACTIVE"},
                    {"employee_id": 20002, "name": "Cole, Ben", "status": "PENDING_SETUP"}], f)
    assert d.payroll_id == {"E10001": 10001, "E10002": 20002}           # 20002: linked by name, not digits
    assert d.payroll_status[20002] == "PENDING_SETUP" and "does not follow HR number" in f.exceptions[0].issue


@pytest.mark.skipif(not DATA.exists(), reason="sample data not found")
def test_every_time_clock_badge_belongs_to_an_hr_employee():
    d = EmployeeDirectory(load_roster(DATA / "hr" / "hr_roster.csv", Findings()))
    badges = {p.badge for p in load_punches(DATA / "time_attendance", ZoneInfo("America/New_York"), Findings())}
    assert badges - set(d.by_number) == set()
