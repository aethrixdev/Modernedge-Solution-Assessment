"""Tests for reading contracts and finance reports."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.common import Findings, parse_amount
from pipeline.ingest.contracts import BuildingTerms, PostChange, WageSchedule, load_contracts, posts_in_force
from pipeline.ingest.finance_reports import parse_ar_open, parse_gl_labor, parse_invoice_register

INV = """ACME LLC
INVOICE REGISTER     PERIOD: 2026-10

Invoice,Inv Date,Cust No,Customer,Svc Period,Site,Ln,Type,Svc Date,Description,Amount
A-1,10/31/26,C1,Client,2026-10,Site 1,1,CONTRACT,,"Fee","1,000.00"
CM-1,10/27/26,C1,Client,2026-10,Site 1,1,CREDIT,10/14/26,Credit,"(250.00)"
,,,,,,,,,REPORT TOTAL,"750.00"
"""

AR = """ENTITY|CUST_NO|CUST_NAME|DOC_TYPE|DOC_NO|APPLY_TO|DOC_DATE|DUE_DATE|AMOUNT
X|C1|Client|INV|I-1||30-SEP-26|30-OCT-26|1000.00
X|C1|Client|PMT|P-1|I-1|27-OCT-26||600.00-
X|C1|Client|CM|CM-1||27-OCT-26||100.00-
*** END OF REPORT *** 3 RECORDS AS OF 31-OCT-26
"""

GL = """
                   ACME LLC                    PAGE   1
PERIOD 2026-10
DEPT  ACCT  ACCOUNT NAME                        WK ENDING           AMOUNT
----  ----  ----------------------------------  ----------  --------------
410   5100  WAGES - STRAIGHT TIME               10/04/26          4,491.20
410   5200  PAYROLL TAXES AND UNION BENEFITS    10/04/26          1,437.18
            DEPT 410 TOTAL                                        5,928.38
\f
            ENTITY TOTAL                                          5,928.38
"""

CONTRACTS = Path("../ModernEdge_AI_Solutions_Engineer_Assessment/data/contracts")


# ---- finance reports -------------------------------------------------------------
def test_amount_formats():
    assert parse_amount("72,400.00") == Decimal("72400.00")
    assert parse_amount("(1,250.00)") == Decimal("-1250.00")
    assert parse_amount("72757.58-") == Decimal("-72757.58")


def test_invoice_register_skips_banner_and_ties_to_total():
    f = Findings()
    lines = parse_invoice_register(INV, "X", f)
    assert [l.amount for l in lines] == [Decimal("1000.00"), Decimal("-250.00")]
    assert f.tieouts and not f.exceptions


def test_ar_and_gl_tie_outs():
    f = Findings()
    items, as_of = parse_ar_open(AR, "X", f)
    assert len(items) == 3 and as_of == date(2026, 10, 31)
    rows = parse_gl_labor(GL, "X", f)
    assert sum(r.amount for r in rows) == Decimal("5928.38") and not f.exceptions


# ---- contracts -------------------------------------------------------------------
def test_wage_rate_starts_first_full_payroll_week():
    ws = WageSchedule(union_rates={"Porter": [(date(2025, 1, 1), Decimal("24.46")), (date(2026, 1, 1), Decimal("25.35"))]})
    assert ws.union_rate("Porter", date(2026, 1, 2)) == Decimal("24.46")   # Thu 1 Jan 2026: week not full yet
    assert ws.union_rate("Porter", date(2026, 1, 5)) == Decimal("25.35")   # Monday 5 Jan


def test_amendment_changes_posts_from_its_effective_date():
    terms = BuildingTerms("Corlett Building", 3, 7, "cost_plus", None, Decimal("0.12"), True)
    change = PostChange("amendment.pdf", date(2026, 9, 1), "Corlett Building", "night", 7, 8)
    assert posts_in_force(terms, [change], date(2026, 8, 31)) == (3, 7)
    assert posts_in_force(terms, [change], date(2026, 10, 1)) == (3, 8)


@pytest.mark.skipif(not CONTRACTS.exists(), reason="sample data not found")
def test_sample_contracts_are_read_without_guessing():
    f = Findings()
    contracts, changes, wages = load_contracts(CONTRACTS, f)
    buildings = {b.name: b for c in contracts for b in c.buildings}
    assert len(contracts) == 4 and len(buildings) == 6
    assert buildings["Corlett Building"].pricing == "cost_plus" and buildings["Corlett Building"].markup == Decimal("0.12")
    assert [(ch.building, ch.new_posts) for ch in changes] == [("Corlett Building", 8)]
    assert wages.union_burden == Decimal("0.32") and not f.exceptions
