"""Read the HR roster (one row per employee, exported at month end)."""
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..common import Findings, digits


@dataclass
class Employee:
    """One employee from the HR roster."""
    hr_id: str              # "E10012"
    number: int             # 10012 - the digits, shared by badges and payroll IDs
    last_name: str
    first_name: str
    middle_initial: str
    job_class: str
    union_member: bool
    home_location: str      # HR code, e.g. "BLD-02" or "FLOAT"
    department: str
    shift: str              # DAY, NIGHT or FLEX
    employment_type: str    # PERMANENT, FLOATER, TEMP
    hire_date: date
    termination_date: date | None
    status: str
    sick_days_start: int    # paid sick days left at the start of the period
    clinic_day_used: bool
    primary_language: str
    source_row: int         # line number in the CSV (header = 1)

    @property
    def full_name(self) -> str:
        """'Miguel Gonzalez' - first and last name in title case."""
        return f"{self.first_name.title()} {self.last_name.title()}"

    def employed_on(self, day: date) -> bool:
        """True if the employee was hired on or before `day` and not yet terminated."""
        return self.hire_date <= day and (self.termination_date is None or day <= self.termination_date)


def _date(value: str) -> date | None:
    """Parse 'YYYY-MM-DD'; an empty cell gives None."""
    return date.fromisoformat(value) if value and value.strip() else None


def _column(fields: list[str], prefix: str, findings: Findings, path: Path) -> str | None:
    """Columns like 'sick_days_remaining_2026_10_01' carry a date in their name;
    find them by prefix so another month's export works too."""
    hits = [f for f in fields if f.lower().startswith(prefix)]
    if not hits:
        findings.exception(path.name, "header", f"no column starting with '{prefix}'", "value taken as 0/No", 0.3)
    return hits[0] if hits else None


def load_roster(path: Path, findings: Findings) -> list[Employee]:
    """Read every roster row into an Employee; log unreadable rows and status/date conflicts."""
    employees = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        sick_col = _column(reader.fieldnames or [], "sick_days_remaining", findings, path)
        clinic_col = _column(reader.fieldnames or [], "clinic_day_used", findings, path)
        for row_no, row in enumerate(reader, start=2):
            try:
                employees.append(Employee(
                    hr_id=row["employee_id"].strip(),
                    number=int(digits(row["employee_id"])),
                    last_name=row["last_name"].strip(),
                    first_name=row["first_name"].strip(),
                    middle_initial=row.get("middle_initial", "").strip(),
                    job_class=row["job_class"].strip(),
                    union_member=row["union_member"].strip().upper() == "Y",
                    home_location=row["home_location"].strip(),
                    department=row["department"].strip(),
                    shift=row["shift"].strip().upper(),
                    employment_type=row["employment_type"].strip().upper(),
                    hire_date=_date(row["hire_date"]),
                    termination_date=_date(row.get("termination_date", "")),
                    status=row["status"].strip().upper(),
                    sick_days_start=int(row.get(sick_col) or 0) if sick_col else 0,
                    clinic_day_used=(row.get(clinic_col) or "").strip().upper() == "Y" if clinic_col else False,
                    primary_language=row.get("primary_language", "").strip(),
                    source_row=row_no,
                ))
            except (KeyError, ValueError) as exc:
                findings.exception(path.name, f"row {row_no}", f"unreadable roster row: {exc}", "row skipped", 0.0)
    for e in employees:
        if e.status == "TERMINATED" and e.termination_date is None:
            findings.exception(path.name, e.hr_id, "status TERMINATED but no termination date", "treated as employed", 0.4)
        if e.termination_date and e.status != "TERMINATED":
            findings.exception(path.name, e.hr_id, "termination date set but status not TERMINATED", "termination date used", 0.7)
    return employees
