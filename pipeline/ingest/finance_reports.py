"""Parse the three finance reports exactly as the ERP prints them, and check
each against its own control totals (the report footer).

- INV_REG  (invoice register): CSV with banner lines above the header and a
  REPORT TOTAL line at the bottom.
- AR_OPEN  (open receivables): pipe-delimited, footer "N RECORDS AS OF dd-MON-yy".
  Amounts use a trailing minus for credits/payments ("1250.00-").
- GL_LABOR (labor cost detail): fixed-width print layout with page headers,
  DEPT totals and an ENTITY TOTAL.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..common import Findings, parse_amount


@dataclass
class InvoiceLine:
    """One line of the invoice register."""
    entity: str
    invoice: str
    invoice_date: date
    customer_no: str
    customer: str
    service_period: str
    site: str
    line_no: int
    line_type: str          # CONTRACT, COVERAGE, SUNDRY, CREDIT ...
    service_date: date | None
    description: str
    amount: Decimal

    @property
    def ref(self) -> str:
        """The reference used to cite this line: invoice number and line number."""
        return f"{self.invoice}/{self.line_no}"


@dataclass
class ArItem:
    """One open item in the AR report (invoice, payment or credit memo)."""
    entity: str
    customer_no: str
    customer: str
    doc_type: str           # INV, PMT, CM ...
    doc_no: str
    apply_to: str
    doc_date: date
    due_date: date | None
    amount: Decimal         # signed


@dataclass
class GlLine:
    """One labour line from the general ledger, by department, account and week."""
    entity: str
    dept: str
    account: str
    account_name: str
    week_ending: date
    amount: Decimal
    line_no: int


def _mdy(s: str) -> date | None:
    """Parse 'MM/DD/YY' (or empty -> None)."""
    s = s.strip()
    if not s:
        return None
    return datetime.strptime(s, "%m/%d/%y" if len(s) == 8 else "%m/%d/%Y").date()


def parse_invoice_register(text: str, entity: str, findings: Findings) -> list[InvoiceLine]:
    """Read the invoice register CSV (skipping the banner) and check it ties to REPORT TOTAL."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Invoice,")), None)
    if start is None:
        findings.exception(f"INV_REG {entity}", "header", "column header not found", "report skipped", 0.0)
        return []
    out, footer_total = [], None
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        if not row.get("Invoice"):
            if "REPORT TOTAL" in (row.get("Description") or ""):
                footer_total = parse_amount(row["Amount"])
            continue
        out.append(InvoiceLine(
            entity=entity, invoice=row["Invoice"].strip(), invoice_date=_mdy(row["Inv Date"]),
            customer_no=row["Cust No"].strip(), customer=row["Customer"].strip(),
            service_period=row["Svc Period"].strip(), site=row["Site"].strip(), line_no=int(row["Ln"]),
            line_type=row["Type"].strip().upper(), service_date=_mdy(row["Svc Date"]),
            description=row["Description"].strip(), amount=parse_amount(row["Amount"]),
        ))
    total = sum((x.amount for x in out), Decimal("0"))
    if footer_total is None:
        findings.exception(f"INV_REG {entity}", "footer", "REPORT TOTAL line missing", "lines used without tie-out", 0.5)
    elif total != footer_total:
        findings.exception(f"INV_REG {entity}", "footer", f"lines sum {total} but REPORT TOTAL is {footer_total}",
                           "lines used as parsed; difference flagged", 0.3)
    else:
        findings.tieouts.append(f"Invoice register {entity}: {len(out)} lines sum to {total}, equal to REPORT TOTAL.")
    return out


def _dmy(s: str) -> date | None:
    """Parse 'DD-MON-YY', e.g. '30-SEP-26' (or empty -> None)."""
    s = s.strip()
    return datetime.strptime(s.title(), "%d-%b-%y").date() if s else None


def parse_ar_open(text: str, entity: str, findings: Findings) -> tuple[list[ArItem], date | None]:
    """Read the pipe-separated AR report; check the record count and return the as-of date."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    items, as_of, expected = [], None, None
    header = None
    for ln in lines:
        if ln.startswith("***"):
            m = re.search(r"(\d+) RECORDS AS OF (\d{2}-[A-Z]{3}-\d{2})", ln)
            if m:
                expected, as_of = int(m.group(1)), _dmy(m.group(2))
            continue
        cells = [c.strip() for c in ln.split("|")]
        if header is None:
            header = cells
            continue
        r = dict(zip(header, cells))
        items.append(ArItem(entity, r["CUST_NO"], r["CUST_NAME"], r["DOC_TYPE"].upper(), r["DOC_NO"],
                            r.get("APPLY_TO", ""), _dmy(r["DOC_DATE"]), _dmy(r.get("DUE_DATE", "")),
                            parse_amount(r["AMOUNT"])))
    if expected is None:
        findings.exception(f"AR_OPEN {entity}", "footer", "record count footer missing", "items used without tie-out", 0.5)
    elif expected != len(items):
        findings.exception(f"AR_OPEN {entity}", "footer", f"{len(items)} records read but footer says {expected}",
                           "items used as parsed; difference flagged", 0.3)
    else:
        findings.tieouts.append(f"Open receivables {entity}: {len(items)} records, equal to the report footer.")
    return items, as_of


GL_ROW = re.compile(r"^(\d{3,})\s+(\d{4})\s+(.+?)\s+(\d{2}/\d{2}/\d{2})\s+([\d,().-]+)\s*$")
GL_DEPT_TOTAL = re.compile(r"DEPT\s+(\S+)\s+TOTAL\s+([\d,().-]+)")
GL_ENTITY_TOTAL = re.compile(r"ENTITY TOTAL\s+([\d,().-]+)")


def parse_gl_labor(text: str, entity: str, findings: Findings) -> list[GlLine]:
    """Read the fixed-width GL labour report and check department and entity totals."""
    rows, dept_totals, entity_total = [], {}, None
    for n, raw in enumerate(text.splitlines(), start=1):
        ln = raw.strip("\f\r")
        m = GL_ROW.match(ln.strip())
        if m:
            rows.append(GlLine(entity, m.group(1), m.group(2), m.group(3).strip(), _mdy(m.group(4)),
                               parse_amount(m.group(5)), n))
            continue
        if (m := GL_DEPT_TOTAL.search(ln)):
            dept_totals[m.group(1)] = parse_amount(m.group(2))
        elif (m := GL_ENTITY_TOTAL.search(ln)):
            entity_total = parse_amount(m.group(1))
    # tie-out: each department and the entity against the printed totals
    ok = True
    for dept, printed in dept_totals.items():
        s = sum((r.amount for r in rows if r.dept == dept), Decimal("0"))
        if s != printed:
            ok = False
            findings.exception(f"GL_LABOR {entity}", f"dept {dept}", f"lines sum {s} but DEPT TOTAL is {printed}",
                               "lines used as parsed; difference flagged", 0.3)
    total = sum((r.amount for r in rows), Decimal("0"))
    if entity_total is not None and total != entity_total:
        ok = False
        findings.exception(f"GL_LABOR {entity}", "entity", f"lines sum {total} but ENTITY TOTAL is {entity_total}",
                           "lines used as parsed; difference flagged", 0.3)
    if ok and entity_total is not None:
        findings.tieouts.append(f"GL labor {entity}: {len(rows)} lines tie to {len(dept_totals)} department totals "
                                f"and the entity total {entity_total}.")
    return rows
