"""Apply Personnel Action Forms to the employee directory.

A form takes effect only when all three approvals are signed: Human
Resources, Operations and Payroll (printed on the form itself, and in the
KPI definitions). Forms that take effect:
- transfer     -> home building changes from the effective date
- termination  -> termination date (if the roster disagrees, the form wins)
- new hire     -> checked against the roster
Forms missing an approval change nothing, and are logged.
"""
import re
from datetime import date, datetime
from pathlib import Path

from ..common import Findings, norm
from ..llm.client import LLMUnavailable
from ..llm.paf_extractor import PafExtraction, extract_paf
from .employees import EmployeeDirectory, Transfer

REQUIRED = {"HUMAN RESOURCES", "OPERATIONS", "PAYROLL"}
LOCATION_RE = re.compile(r"\b([A-Z]{2,5}-\d{1,3}|FLOAT)\b")


def _date(s: str | None) -> date | None:
    """Parse 'MM/DD/YYYY' as written on the form (or None)."""
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _location(s: str | None) -> str | None:
    """Pull the HR location code out of text like 'BLD-05 (Bramwell House), Night'."""
    m = LOCATION_RE.search(s or "")
    return m.group(1) if m else None


def apply_pafs(paf_dir: Path, directory: EmployeeDirectory, findings: Findings) -> list[dict]:
    """Read every form and apply only the fully approved ones to the directory; return one record per form."""
    records = []
    for path in sorted(paf_dir.glob("*.pdf")):
        try:
            x: PafExtraction = extract_paf(path)
        except LLMUnavailable as exc:
            findings.exception("personnel_action_forms", path.name, f"form not read: {exc}",
                               "roster used without this form", 0.0)
            continue
        signed = {norm(a.role) for a in x.approvals if a.signed}
        missing = sorted(REQUIRED - signed)
        effective = _date(x.effective_date)
        e = directory.by_hr.get((x.employee_id or "").strip().upper())
        if e is None and x.employee_name:
            e = directory.match_name(x.employee_name).employee
        rec = {"file": path.name, "action": x.action, "employee_id": x.employee_id, "employee_name": x.employee_name,
               "effective_date": effective, "from": x.from_location, "to": x.to_location,
               "approvals": ", ".join(sorted(signed)) or "none", "applied": False, "note": ""}
        records.append(rec)

        if e is None:
            findings.exception("personnel_action_forms", path.name, f"employee {x.employee_id} {x.employee_name} not in roster",
                               "form not applied", 0.0)
            rec["note"] = "employee not found"
            continue
        if missing:
            rec["note"] = f"missing approval: {', '.join(m.title() for m in missing)}"
            findings.exception("personnel_action_forms", path.name,
                               f"{x.action} for {e.hr_id} effective {x.effective_date} lacks {', '.join(m.title() for m in missing)} approval",
                               "no effect (a form needs all three approvals)", 0.95)
            findings.conflict(f"PAF {path.name}: {x.action} of {e.hr_id} {e.full_name} effective {x.effective_date} "
                              f"is missing {', '.join(m.title() for m in missing)} approval, so it has no effect; "
                              f"the roster's home building {e.home_location} stands.")
            continue
        if x.confidence < 0.7 or effective is None:
            rec["note"] = "unclear scan"
            findings.exception("personnel_action_forms", path.name, "form could not be read reliably",
                               "not applied; needs manual review", x.confidence)
            continue

        rec["applied"] = True
        if x.action == "transfer":
            src, dst = _location(x.from_location), _location(x.to_location)
            if not dst:
                rec["applied"], rec["note"] = False, "no destination location"
                findings.exception("personnel_action_forms", path.name, "transfer has no readable destination", "not applied", 0.3)
                continue
            directory.transfers.setdefault(e.hr_id, []).append(Transfer(effective, src or e.home_location, dst, path.name))
            if e.home_location == src:
                findings.conflict(f"PAF {path.name}: {e.hr_id} {e.full_name} transferred {src} -> {dst} effective "
                                  f"{effective}, fully approved, but the roster (exported at month end) still shows {src}. "
                                  f"Home building is {src} before {effective} and {dst} from then on.")
            rec["note"] = f"home {src} -> {dst} from {effective}"
        elif x.action == "termination":
            last = _date(x.last_day_worked) or effective
            if e.termination_date != last:
                findings.conflict(f"PAF {path.name}: termination of {e.hr_id} on {last}; roster says "
                                  f"{e.termination_date or 'not terminated'}. The approved form is used.")
                e.termination_date = last
            rec["note"] = f"terminated {last}"
        elif x.action == "new_hire":
            if e.hire_date != effective:
                findings.conflict(f"PAF {path.name}: new hire {e.hr_id} effective {effective}; roster hire date "
                                  f"{e.hire_date}. Roster kept; difference logged.")
            rec["note"] = f"hire confirmed {effective}"
        else:
            rec["applied"] = False
            rec["note"] = f"{x.action} forms are not applied automatically"
            findings.exception("personnel_action_forms", path.name, f"{x.action} form", "logged only", 0.5)
    return records
