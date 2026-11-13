"""Run the whole pipeline once:  python -m pipeline.run

 1. Pull the client systems (payroll, dispatch, finance) through their APIs.
 2. Read the client files (HR, PAFs, time clock, contracts, call-outs, budget).
 3. Resolve buildings, departments, clients and employees across all sources.
 4. Call-outs -> absences -> payroll codes -> payroll writes (or review queue).
 5. Dispatch coverage -> invoice matching -> unbilled coverage -> backup notes.
 6. KPIs and AR aging, with a fact row behind every figure.
 7. Write ./output.
"""
import calendar
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

from . import attendance, callouts, coverage, kpi, outputs
from .clients.dispatch import DispatchClient
from .clients.finance import FinanceClient, ReportError
from .clients.payroll import PayrollClient
from .common import Findings, fmt_money, norm
from .config import load_settings
from .ingest import callout_sources, contracts, finance_reports, hr, time_attendance
from .payroll_writer import PayrollWriter
from .resolve.buildings import BuildingRegistry
from .resolve.employees import EmployeeDirectory
from .resolve.paf import apply_pafs
from .rules import billing

log = logging.getLogger("pipeline")
STANDING_DECISIONS = Path(__file__).resolve().parent.parent / "docs" / "decisions_log.md"


def _find(folder: Path, pattern: str) -> Path | None:
    """The first file matching `pattern` anywhere under `folder` (so file names can change)."""
    hits = sorted(folder.rglob(pattern))
    return hits[0] if hits else None


def derive_period(punches) -> tuple[date, date, str]:
    """The reporting month: PERIOD env (YYYY-MM) if set, else the month most
    time-clock punches fall in. Nothing about the month is hard-coded."""
    label = os.getenv("PERIOD")
    if not label:
        label = Counter(p.at.strftime("%Y-%m") for p in punches).most_common(1)[0][0]
    y, m = map(int, label.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]), label


def main() -> None:
    """Run every step once, from reading the systems to writing ./output."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    F = Findings()
    data = s.data_dir
    out = s.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # ---- 1. client systems -------------------------------------------------------
    log.info("reading the client systems")
    payroll = PayrollClient(s.payroll_url, s.payroll_api_key)
    dispatch = DispatchClient(s.dispatch_url, s.dispatch_client_id, s.dispatch_client_secret)
    finance = FinanceClient(s.finance_url, s.finance_user, s.finance_password)
    payroll_employees = payroll.employees()
    payroll_codes = {c["code"] for c in payroll.codes()}
    d_sites, d_workers, d_postings = dispatch.sites(), dispatch.workers(), dispatch.postings()
    entities, customers, f_sites = finance.entities(), finance.customers(), finance.sites()

    tz_name = os.getenv("TIMEZONE") or Counter(x["timezone"] for x in d_sites if x.get("timezone")).most_common(1)[0][0]
    tz = ZoneInfo(tz_name)

    # ---- 2. client files -----------------------------------------------------------
    log.info("reading the client files in %s", data)
    contract_list, post_changes, wages = contracts.load_contracts(data / "contracts", F)
    roster = hr.load_roster(_find(data, "*roster*.csv"), F)
    punches = time_attendance.load_punches(_find(data, "*punches*.csv").parent, tz, F)
    start, end, period_label = derive_period(punches)
    log.info("period %s (%s to %s), timezone %s", period_label, start, end, tz_name)

    reports = []
    log_file = _find(data / "callouts", "*.xlsx")
    if log_file:
        reports += callout_sources.load_reception_log(log_file, tz, F)
    chat_file = _find(data / "callouts", "*.txt")
    if chat_file:
        reports += callout_sources.load_chat(chat_file, tz, F)

    inv_lines, ar_items, gl_lines = [], [], []
    for ent in entities:
        code = ent["entity_code"]
        try:
            inv_lines += finance_reports.parse_invoice_register(finance.run_report("INV_REG", code, period_label), code, F)
            items, as_of = finance_reports.parse_ar_open(finance.run_report("AR_OPEN", code, period_label), code, F)
            ar_items += items
            if as_of and as_of != end:
                F.exception(f"AR_OPEN {code}", "footer", f"report is as of {as_of}, not {end}", "aged as of period end", 0.6)
            gl_lines += finance_reports.parse_gl_labor(finance.run_report("GL_LABOR", code, period_label), code, F)
        except ReportError as exc:
            F.exception(f"finance {code}", period_label, str(exc), "entity's reports missing from KPIs", 0.0)

    # ---- 3. resolve -------------------------------------------------------------------
    terms = {b.name: (b, c) for c in contract_list for b in c.buildings}
    building_names = [b.name for c in contract_list for b in c.buildings]
    reg = BuildingRegistry(building_names)

    site_building = {}            # dispatch site_id -> building
    for site in d_sites:
        b = reg.register("dispatch site", site["name"], F if site.get("active") else None)
        if b:
            site_building[site["site_id"]] = b
        if not site.get("active"):
            F.exception("dispatch /v2/sites", site["site_id"], f"inactive site '{site['name']}'",
                        f"mapped to {b}" if b else "ignored", 0.9)
    for p in d_postings:
        if p["site_id"] not in site_building:
            F.exception("dispatch /v2/postings", p["posting_id"], f"posting at unmapped site {p['site_id']}", "not attributed", 0.0)

    fin_site_building = {}        # finance site name -> building
    for fs in f_sites:
        b, conf, method = reg.match_name(fs["site_name"])
        if b is None and fs.get("description"):          # e.g. "Meridian 1" described as "Meridian One"
            b, conf, method = reg.match_name(fs["description"])
            method = f"by description '{fs['description']}': {method}"
        reg.register("finance site", fs["site_name"], building=b, method=method, confidence=conf)
        if b:
            fin_site_building[fs["site_name"]] = b
    for ln in inv_lines:
        if ln.site not in fin_site_building:
            b = reg.register("finance site", ln.site, F)
            if b:
                fin_site_building[ln.site] = b

    def punch_building(p):
        """The building a punch's site name maps to (remembered as an alias)."""
        return reg.register("time clock site", p.site_text, F)

    directory = EmployeeDirectory(roster)

    # HR location codes (BLD-03) carry no name: map each to where its people punch.
    votes = defaultdict(Counter)
    loc_of = {e.number: e.home_location for e in roster}
    for p in punches:
        if p.badge in loc_of:
            b = punch_building(p)
            if b:
                votes[loc_of[p.badge]][b] += 1
    for loc in sorted({e.home_location for e in roster}):
        if all(e.employment_type != "PERMANENT" for e in roster if e.home_location == loc):
            continue   # float pool: not a building
        b = reg.register_by_evidence("hr location", loc, votes[loc], F, "employees' punches")
        if b:
            directory.location_building[loc] = b

    # Ledger departments: finance's site->department list first, then HR's location->department.
    dept_building = {}
    for fs in f_sites:
        if fs["site_name"] in fin_site_building:
            dept_building[fs["department"]] = fin_site_building[fs["site_name"]]
            reg.register("ledger department", fs["department"], building=fin_site_building[fs["site_name"]],
                         method=f"finance site list ({fs['site_name']})", confidence=1.0)
    dept_votes = defaultdict(Counter)
    for e in roster:
        if e.home_location in directory.location_building:
            dept_votes[e.department][directory.location_building[e.home_location]] += 1
    for dept, v in dept_votes.items():
        if dept not in dept_building:
            b = reg.register_by_evidence("ledger department", dept, v, F, "HR roster employees")
            dept_building[dept] = b
            F.conflict(f"Ledger department {dept} is not in finance's site list; mapped to {b} because HR charges "
                       f"that building's employees to it.")
        elif v.most_common(1)[0][0] != dept_building[dept]:
            F.conflict(f"Department {dept}: finance maps it to {dept_building[dept]}, HR employees suggest "
                       f"{v.most_common(1)[0][0]}. Finance's mapping used.")
    for g in {g.dept for g in gl_lines} - set(dept_building):
        F.exception("GL_LABOR", f"dept {g}", "department has labor but no building", "not in any building's labor cost", 0.0)

    paf_dir = next((p for p in data.rglob("*") if p.is_dir() and "personnel" in p.name.lower()), None)
    paf_records = apply_pafs(paf_dir, directory, F) if paf_dir else []
    directory.link_payroll(payroll_employees, F)
    directory.link_dispatch_workers(d_workers, F)

    sessions = attendance.build_sessions(punches, punch_building)
    for sess in sessions:
        e = directory.by_number.get(sess.badge)
        if e is None:
            F.exception("time_attendance", sess.refs, f"badge {sess.badge} not in HR roster", "punch ignored", 0.0)
        elif e.termination_date and sess.day > e.termination_date:
            F.exception("time_attendance", sess.refs, f"{e.hr_id} {e.full_name} punched on {sess.day}, after "
                        f"termination on {e.termination_date}", "not counted as a scheduled shift", 0.9)
            F.conflict(f"{e.hr_id} {e.full_name} has punches after the termination date {e.termination_date} (last on {sess.day}).")
        if sess.clock_in is None or sess.clock_out is None:
            F.exception("time_attendance", sess.refs, f"badge {sess.badge} {sess.day} {sess.shift}: missing "
                        f"{'clock-in' if sess.clock_in is None else 'clock-out'}", "shift kept with a missing time", 0.8)
    sessions_by_key = defaultdict(list)
    for sess in sessions:
        sessions_by_key[(sess.badge, sess.building, sess.day, sess.shift)].append(sess)

    # ---- 4. call-outs -> payroll -----------------------------------------------------
    log.info("reading %d call-out reports", len(reports))
    absences, unresolved = callouts.build_absences(reports, directory, reg, d_postings, site_building, (start, end), F)
    for a in absences:
        if a.code and a.code not in payroll_codes:
            a.review.append(f"code {a.code} not accepted by payroll")
    writer = PayrollWriter(payroll, s.dry_run)
    for a in absences:
        writer.write(a)
    writer.collect_final_status(absences)
    for a in absences:
        if a.decision == "held_for_review":
            F.exception(a.primary.report.source, a.primary.report.source_ref,
                        f"{a.employee.hr_id} {a.day} {a.code or 'uncoded'}: " + "; ".join(dict.fromkeys(a.review)),
                        "held for review (not written to payroll)", a.confidence)
        elif a.final_status == "REJECTED":
            F.exception("payroll", a.submission_id, f"payroll rejected {a.employee.hr_id} {a.day} {a.code}",
                        "needs follow-up", 0.5)

    # ---- 5. coverage, billing, backup notes -----------------------------------------
    shifts = coverage.build_coverage(d_postings, site_building, directory, wages, terms, (start, end), F)
    coverage.match_invoices(shifts, inv_lines, reg, period_label, F)
    notes = {}
    for sh in shifts:
        coverage.time_on_site(sh, sessions_by_key)
        if sh.billable:
            b_terms, contract = terms[sh.building]
            if contract.backup_template:
                notes[sh.posting_id] = coverage.backup_note(sh, contract.backup_template, contract.client, F)
            else:
                F.exception(contract.source_file, sh.building, "coverage billable but no Exhibit C template",
                            "no backup note written", 0.3)

    # ---- 6. KPIs ------------------------------------------------------------------
    kpi_rows, facts = kpi.compute(building_names, terms, post_changes, directory, absences, shifts, inv_lines,
                                  gl_lines, dept_building, fin_site_building, (start, end), period_label, F)
    ar_totals, ar_detail = kpi.ar_aging(ar_items, end, F)

    # cross-check: the old labor budget (low trust, never used as a source of truth)
    budget_rows = labor_budget_check(data, directory, wages, end, F)

    # ---- 7. outputs -----------------------------------------------------------------
    sub_rows = outputs.submission_rows(absences, unresolved)
    outputs.write_kpis(out, kpi_rows)
    outputs.write_ar_aging(out, ar_totals)
    outputs.write_payroll_submissions(out, sub_rows)
    outputs.write_backup_notes(out, notes)
    outputs.write_exceptions(out, F)
    summary = [
        f"Period {period_label}; {len(building_names)} buildings; {len(roster)} employees on the roster.",
        f"{len(reports)} call-out reports read -> {len(absences)} distinct absences; "
        f"{sum(a.decision == 'written' for a in absences)} written to payroll, "
        f"{sum(a.decision == 'held_for_review' for a in absences) + len(unresolved)} held for review"
        f"{' (DRY RUN: nothing sent)' if s.dry_run else ''}.",
        f"{len(shifts)} dispatch postings; {sum(sh.billable for sh in shifts)} billable coverage shifts; "
        f"{sum(sh.billable and not sh.invoice_ref for sh in shifts)} unbilled.",
        f"{len(F.exceptions)} exceptions.",
    ]
    standing = STANDING_DECISIONS.read_text(encoding="utf-8") if STANDING_DECISIONS.exists() else ""
    outputs.write_decisions_log(out, standing, F, list(reg.aliases.values()), summary)
    (out / "payroll_audit.json").write_text(json.dumps(writer.audit, indent=1, default=str), encoding="utf-8")

    for line in summary:
        log.info(line)


def labor_budget_check(data: Path, directory: EmployeeDirectory, wages, on: date, F: Findings) -> list[dict]:
    """The FY26 labor budget is unowned and stale. We only compare it with the
    roster and the wage schedule and report differences; nothing uses it."""
    path = _find(data, "*budget*.xlsx")
    if not path:
        return []
    rows = []
    ws = openpyxl.load_workbook(path, data_only=False).worksheets[0]
    header = None
    for n, r in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = ["" if v is None else str(v).strip() for v in r]
        if header is None:
            if "Employee" in cells and "Rate" in cells:
                header = {c: i for i, c in enumerate(cells) if c}
            continue
        name = cells[header["Employee"]]
        if not name:
            continue
        rate = cells[header["Rate"]]
        rows.append({"row": n, "building": cells[header["Building"]], "employee": name, "status": cells[header.get("Status", 0)],
                     "rate": rate})
        m = directory.match_name(name)
        if m.employee is None:
            F.exception(path.name, f"row {n}", f"budget lists '{name}', not matched to one roster employee", "ignored (budget is low trust)", 0.5)
            continue
        e = m.employee
        current = billing.base_rate(e, wages, on)
        if current is not None and rate and abs(float(rate) - float(current)) > 0.005:
            rows[-1]["note"] = f"rate {rate} vs current {current}"
        if e.employment_type != "PERMANENT" and "perm" in cells[header.get("Status", 0)].lower():
            F.exception(path.name, f"row {n}", f"budget lists {e.full_name} as permanent; roster says {e.employment_type}",
                        "roster used", 0.9)
        if e.termination_date and e.termination_date <= on:
            F.exception(path.name, f"row {n}", f"budget still lists {e.full_name}, terminated {e.termination_date}",
                        "roster used", 0.9)
    stale = sum(1 for r in rows if r.get("note"))
    if stale:
        F.conflict(f"Labor budget ({path.name}): {stale} of {len(rows)} rates differ from the current wage schedule "
                   "(it appears to use the previous year's rates). Not used for any figure.")
    return rows



if __name__ == "__main__":
    main()
