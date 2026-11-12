"""Matching buildings and people across sources."""
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline.common import Findings, norm
from pipeline.ingest.time_attendance import load_punches
from pipeline.resolve.buildings import BuildingRegistry

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
