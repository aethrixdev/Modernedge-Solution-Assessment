"""Read the time clock exports.

The clock firmware changed mid-month, so there are two layouts:
- old: badge_no "0010041", "LAST, FIRST", site NAME, IN/OUT/BREAK_*, local time "MM/DD/YYYY HH:MM" (no zone)
- new: EmpBadge "10041", "First Last", SiteCode "S-105", CLOCK_IN/OUT/MEAL_*, ISO time with offset
Both are turned into one shape: badge number, site text, event (IN, OUT,
BREAK_START, BREAK_END) and a timezone-aware timestamp.

The layout is detected from the header, not the file name, so files can be
renamed or split differently in another dataset.
"""
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..common import Findings, digits

EVENT_MAP = {
    "IN": "IN", "CLOCK_IN": "IN",
    "OUT": "OUT", "CLOCK_OUT": "OUT",
    "BREAK_OUT": "BREAK_START", "MEAL_START": "BREAK_START",
    "BREAK_IN": "BREAK_END", "MEAL_END": "BREAK_END",
}


@dataclass
class Punch:
    """One time clock punch, in the same shape whichever layout it came from."""
    badge: int
    name_text: str
    site_text: str          # site name, or the code translated to its name
    event: str
    at: datetime            # timezone aware
    source_file: str
    source_row: int


def load_site_codes(path: Path) -> dict[str, str]:
    """Read the site code list: 'S-101' -> 'HALCYON TWR'."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["SiteCode"].strip(): r["SiteName"].strip() for r in csv.DictReader(f)}


def load_punches(ta_dir: Path, local_tz: ZoneInfo, findings: Findings) -> list[Punch]:
    """Read every punch file in the folder, detecting the old or new layout from its header."""
    codes_file = next(iter(sorted(ta_dir.glob("*site_code*.csv"))), None)
    codes = load_site_codes(codes_file) if codes_file else {}
    punches: list[Punch] = []
    for path in sorted(ta_dir.glob("*.csv")):
        if path == codes_file:
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = set(reader.fieldnames or [])
            for row_no, row in enumerate(reader, start=2):
                try:
                    if {"badge_no", "punch_time"} <= cols:          # old layout
                        badge, name, site = row["badge_no"], row["employee_name"], row["site"]
                        event = row["punch_type"]
                        at = datetime.strptime(row["punch_time"].strip(), "%m/%d/%Y %H:%M").replace(tzinfo=local_tz)
                    elif {"EmpBadge", "Timestamp"} <= cols:         # new layout
                        badge, name = row["EmpBadge"], row["Name"]
                        code = row["SiteCode"].strip()
                        site = codes.get(code)
                        if site is None:
                            findings.exception(path.name, f"row {row_no}", f"unknown site code {code}", "punch kept with code as site", 0.3)
                            site = code
                        event = row["Event"]
                        at = datetime.fromisoformat(row["Timestamp"].strip()).astimezone(local_tz)
                    else:
                        findings.exception(path.name, "header", f"unrecognised time clock layout {sorted(cols)}", "file skipped", 0.0)
                        break
                    ev = EVENT_MAP.get(event.strip().upper())
                    if ev is None:
                        findings.exception(path.name, f"row {row_no}", f"unknown punch type {event}", "punch skipped", 0.0)
                        continue
                    punches.append(Punch(int(digits(badge)), name.strip(), site.strip(), ev, at, path.name, row_no))
                except (ValueError, KeyError) as exc:
                    findings.exception(path.name, f"row {row_no}", f"unreadable punch: {exc}", "punch skipped", 0.0)
    return punches
