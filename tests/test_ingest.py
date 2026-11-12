"""Tests for reading the client files, using small made-up files in a temp folder."""
from datetime import datetime
from zoneinfo import ZoneInfo

import openpyxl

from pipeline.common import Findings
from pipeline.ingest.callout_sources import load_chat, load_reception_log
from pipeline.ingest.hr import load_roster
from pipeline.ingest.time_attendance import load_punches

TZ = ZoneInfo("America/New_York")
HEADER = ("employee_id,last_name,first_name,middle_initial,job_class,union_member,home_location,department,"
          "shift,employment_type,hire_date,status,termination_date,{sick},{clinic},primary_language\n")


# ---- HR roster -------------------------------------------------------------------
def test_roster_finds_dated_columns_by_prefix(tmp_path):
    f = tmp_path / "roster.csv"
    f.write_text(HEADER.format(sick="sick_days_remaining_2027_01_01", clinic="clinic_day_used_2027")
                 + "E10001,GONZALEZ,Miguel,T,Porter,Y,BLD-01,410,day,PERMANENT,2020-10-01,ACTIVE,,4,Y,Spanish\n",
                 encoding="utf-8")
    F = Findings()
    [e] = load_roster(f, F)
    assert (e.number, e.sick_days_start, e.clinic_day_used, e.shift) == (10001, 4, True, "DAY")
    assert e.full_name == "Miguel Gonzalez"
    assert F.exceptions == []


def test_roster_logs_status_and_date_conflicts(tmp_path):
    f = tmp_path / "roster.csv"
    f.write_text(HEADER.format(sick="sick_days_remaining_x", clinic="clinic_day_used_x")
                 + "E10002,PEREZ,Hector,L,Porter,Y,BLD-01,410,DAY,PERMANENT,2024-02-14,TERMINATED,,6,N,Spanish\n",
                 encoding="utf-8")
    F = Findings()
    load_roster(f, F)
    assert "no termination date" in F.exceptions[0].issue


# ---- time clock -------------------------------------------------------------------
def test_time_clock_reads_both_layouts_into_one_shape(tmp_path):
    (tmp_path / "ta_site_codes.csv").write_text("SiteCode,SiteName,Status\nS-102,CORLETT BLDG,ACTIVE\n")
    (tmp_path / "old.csv").write_text('badge_no,employee_name,site,punch_type,punch_time,device_id\n'
                                      '0010041,"BERISHA, FATMIRE",BRAMWELL HSE,IN,10/01/2026 06:46,T1\n')
    (tmp_path / "new.csv").write_text("EmpBadge,Name,SiteCode,Event,Timestamp,Terminal\n"
                                      "10012,Dragan Djordjevic,S-102,CLOCK_OUT,2026-10-19T15:00:00-04:00,T2\n"
                                      "10012,Dragan Djordjevic,S-999,CLOCK_IN,2026-10-20T07:00:00-04:00,T2\n")
    F = Findings()
    old, new, unknown = sorted(load_punches(tmp_path, TZ, F), key=lambda p: p.at)
    assert (old.badge, old.site_text, old.event) == (10041, "BRAMWELL HSE", "IN")
    assert old.at == datetime(2026, 10, 1, 6, 46, tzinfo=TZ)        # old layout: local time, zone added
    assert (new.badge, new.site_text, new.event) == (10012, "CORLETT BLDG", "OUT")   # code -> site name
    assert unknown.site_text == "S-999" and "unknown site code" in F.exceptions[0].issue


# ---- call-out sources ---------------------------------------------------------------
def test_reception_log_finds_header_and_gives_voicemail_a_year(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Reception call-out log"])                                  # banner above the header
    ws.append(["Date", "Time", "Channel", "VM Received", "Caller", "Message", "Taken By"])
    ws.append(["01/01/2027", "8:05 AM", "Voicemail", "12/31 23:50", "Ana", "sick tonight", "Rita"])
    f = tmp_path / "log.xlsx"
    wb.save(f)
    [r] = load_reception_log(f, TZ, Findings())
    assert r.source_ref == "3"                                             # spreadsheet row number
    assert r.logged_at == datetime(2027, 1, 1, 8, 5, tzinfo=TZ)
    assert r.vm_received_at == datetime(2026, 12, 31, 23, 50, tzinfo=TZ)   # year before, not after the log


def test_chat_joins_multiline_messages_and_skips_system_lines(tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text("[10/07/26, 9:46:00 AM] Messages are end-to-end encrypted.\n"
                 "[10/07/26, 9:47:00 AM] Gloria M: Ana texted me\n"
                 "she is sick tonight\n", encoding="utf-8")
    [r] = load_chat(f, TZ, Findings())
    assert (r.source_ref, r.caller) == ("10/07/26, 9:47:00 AM", "Gloria M")
    assert r.message == "Ana texted me\nshe is sick tonight"
