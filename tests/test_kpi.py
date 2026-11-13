"""KPIs by building and AR aging."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

from pipeline.common import Findings
from pipeline.coverage import CoverageShift
from pipeline.ingest.contracts import BuildingTerms
from pipeline.ingest.finance_reports import GlLine, InvoiceLine, parse_ar_open
from pipeline.ingest.hr import Employee
from pipeline.kpi import ar_aging, bucket, compute
from pipeline.resolve.employees import EmployeeDirectory

B = "Corlett Building"
AR = """ENTITY|CUST_NO|CUST_NAME|DOC_TYPE|DOC_NO|APPLY_TO|DOC_DATE|DUE_DATE|AMOUNT
X|C1|Client|INV|I-1||30-SEP-26|30-OCT-26|1000.00
X|C1|Client|PMT|P-1|I-1|27-OCT-26||600.00-
X|C1|Client|CM|CM-1||27-OCT-26||100.00-
*** END OF REPORT *** 3 RECORDS AS OF 31-OCT-26
"""


def test_ar_aging_nets_payments_and_ages_credit_memos_from_document_date():
    f = Findings()
    items, _ = parse_ar_open(AR, "X", f)
    totals, detail = ar_aging(items, date(2026, 10, 31), f)
    assert totals["1_30"] == Decimal("400.00") - Decimal("100.00")   # 400 open 1 day late; CM 4 days old
    assert bucket(0) == "current" and bucket(1) == "1_30" and bucket(31) == "31_60" and bucket(91) == "over_90"


def shift(pid, ot=False, billed=None):
    s = CoverageShift(pid, "S", B, date(2026, 10, 5), "night", "CALLOUT", "FILLED", "x",
                      worker_id="W", fill_type="OT" if ot else "STANDARD")
    s.covering, s.rate, s.burden, s.markup = NS(full_name="Miguel Martinez"), Decimal("25.35"), Decimal("0.32"), Decimal("0.12")
    s.billable, s.billable_amount, s.invoice_ref = True, Decimal("300.00"), billed
    s.ot_dollars = Decimal("304.20") if ot else None
    return s


def test_building_kpis_add_up_from_their_source_records():
    e = Employee("E10033", 10033, "REXHEPI", "Besnik", "", "Cleaner", True, "BLD-04", "430", "NIGHT", "PERMANENT",
                 date(2020, 1, 1), None, "ACTIVE", 5, False, "Albanian", 2)
    d = EmployeeDirectory([e])
    d.location_building = {"BLD-04": B}
    terms = {B: (BuildingTerms(B, 3, 7, "cost_plus", None, Decimal("0.12"), True), NS(source_file="MSA.pdf"))}
    inv = InvoiceLine("NFS", "NFS-1", date(2026, 10, 31), "C", "C", "2026-10", "Corlett Bldg", 1, "CONTRACT",
                      None, "Fee", Decimal("1000.00"))
    gl = GlLine("NFS", "430", "5100", "WAGES", date(2026, 10, 4), Decimal("800.00"), 7)
    rows, facts = compute([B], terms, [], d, [], [shift("P-1", ot=True, billed="NFS-1/2"), shift("P-2")], [inv], [gl],
                          {"430": B}, {"Corlett Bldg": B}, (date(2026, 10, 1), date(2026, 10, 31)), "2026-10", Findings())
    [r] = rows
    assert (r["contracted_headcount"], r["active_headcount"], r["scheduled_shifts"]) == (10, 1, 22)
    assert (r["overtime_hours"], r["overtime_dollars"]) == (8, Decimal("304.20"))
    assert (r["revenue"], r["labor_cost"], r["gross_margin_pct"]) == (Decimal("1000.00"), Decimal("800.00"), Decimal("0.2"))
    assert (r["unbilled_coverage_count"], r["unbilled_coverage_dollars"]) == (1, Decimal("300.00"))   # P-2 not billed
    assert [f.source_ref for f in facts if f.kpi == "unbilled_coverage_count"] == ["P-2"]            # traceable
