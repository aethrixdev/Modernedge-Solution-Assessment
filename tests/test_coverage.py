"""Coverage: final assignment, cost, invoice matching and backup notes."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

from pipeline.common import Findings
from pipeline.coverage import CoverageShift, backup_note, final_assignment, match_invoices, time_on_site
from pipeline.ingest.finance_reports import InvoiceLine
from pipeline.resolve.buildings import BuildingRegistry


def test_last_assignment_wins():
    events = [{"type": "ASSIGNED", "worker_id": "W1", "fill_type": "STANDARD"},
              {"type": "UNASSIGNED", "worker_id": "W1"},
              {"type": "ASSIGNED", "worker_id": "W2", "fill_type": "OT"}]
    assert final_assignment(events) == ("W2", "OT")
    assert final_assignment(events[:2]) == (None, None)


def cov(pid, amount, name, ot=False):
    s = CoverageShift(pid, "S", "Corlett Building", date(2026, 10, 26), "night", "CALLOUT", "FILLED", "x",
                      worker_id="W", fill_type="OT" if ot else "STANDARD")
    first, last = name.split()
    s.covering = NS(number=10057, first_name=first, last_name=last.upper(), full_name=name)
    s.absent = NS(job_class="Cleaner")
    s.billable, s.billable_amount = True, Decimal(amount)
    return s


def test_invoice_line_with_a_dispatch_reference_is_matched_by_it():
    a = cov("P-1026-050", "299.82", "Miguel Martinez")
    line = InvoiceLine("NFS", "NFS-7124", date(2026, 11, 2), "2207", "C", "2026-10", "Corlett Bldg", 1, "COVERAGE",
                       date(2026, 10, 26), "Coverage (ref P-1026-050)", Decimal("299.82"))
    match_invoices([a], [line], BuildingRegistry(["Corlett Building"]), "2026-10", Findings())
    assert a.invoice_ref == "NFS-7124/1" and "reference" in a.invoice_match


def test_invoice_line_without_reference_matched_by_amount():
    reg = BuildingRegistry(["Corlett Building"])
    reg.register("finance site", "Corlett Bldg")
    a, b = cov("P-1", "299.82", "Miguel Martinez"), cov("P-2", "222.03", "David Greene")
    line = InvoiceLine("NFS", "NFS-7124", date(2026, 11, 2), "2207", "C", "2026-10", "Corlett Bldg", 1, "COVERAGE",
                       date(2026, 10, 26), "Coverage 10/26 Night", Decimal("222.03"))
    match_invoices([a, b], [line], reg, "2026-10", Findings())
    assert b.invoice_ref == "NFS-7124/1" and a.invoice_ref is None   # P-1 is unbilled


def test_template_a_names_the_replacement_with_times_and_b_names_nobody():
    s = cov("P-1", "558.84", "Miguel Martinez", ot=True)
    time_on_site(s, {(10057, "Corlett Building", date(2026, 10, 26), "night"):
                     [NS(clock_in=NS(strftime=lambda f: "15:52"), clock_out=NS(strftime=lambda f: "00:04"))]})
    a = backup_note(s, "A", "Corlett Asset Management", Findings())
    assert "Replacement:       Miguel Martinez" in a and "On site:           15:52 to 00:04" in a
    assert "Coverage type:     Overtime" in a and "Billable amount:   $558.84" in a
    b = backup_note(s, "B", "Corlett Asset Management", Findings())
    assert "Martinez" not in b and "Replacement provided" in b
