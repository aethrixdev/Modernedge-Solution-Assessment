"""Read the client agreements, amendments and the wage schedule (text PDFs).

The PDFs share one template, so plain text parsing is reliable and
repeatable (no LLM needed). Anything we cannot parse is reported as an
exception instead of being guessed.

What we take from each Master Services Agreement (MSA):
- client name and contractor (legal entity)
- Schedule B: each building's day posts, night posts, pricing basis
  (fixed monthly fee, or cost plus N%) and whether coverage is billable
- Exhibit C: which backup-note template the client needs (A = with names
  and times, B = no names)
From an amendment: dated changes to the number of posts.
From the wage schedule: union rates by class and effective date, the
non-union rate card, and the labor burden percentages.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from ..common import Findings

MONTH_DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"


def pdf_text(path: Path) -> str:
    """All the text of a PDF, page by page."""
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _flat(text: str) -> str:
    """Join all whitespace so sentences broken over lines can be matched."""
    return re.sub(r"\s+", " ", text)


@dataclass
class BuildingTerms:
    """One building's terms from Schedule B: posts, pricing and whether coverage is billable."""
    name: str                    # exactly as written in Schedule B
    day_posts: int
    night_posts: int
    pricing: str                 # "fixed" or "cost_plus"
    monthly_fee: Decimal | None
    markup: Decimal | None       # 0.15 for "cost plus 15%"
    coverage_billable: bool


@dataclass
class Contract:
    """One client agreement (MSA)."""
    source_file: str
    client: str
    contractor: str
    effective_date: date
    buildings: list[BuildingTerms]
    backup_template: str | None  # "A", "B" or None (no Exhibit C)


@dataclass
class PostChange:
    """A dated change to a building's number of posts, from an amendment."""
    source_file: str
    effective_date: date
    building: str
    shift: str                   # "day" or "night"
    old_posts: int
    new_posts: int


@dataclass
class WageSchedule:
    """Union and non-union pay rates and labour burden from the wage schedule."""
    union_rates: dict[str, list[tuple[date, Decimal]]] = field(default_factory=dict)  # class -> [(effective, rate)]
    nonunion_rates: dict[str, Decimal] = field(default_factory=dict)
    union_burden: Decimal = Decimal("0")
    nonunion_burden: Decimal = Decimal("0")

    def union_rate(self, job_class: str, on: date) -> Decimal | None:
        """The rate in effect on a date. A new rate starts on the first day of
        the first full payroll week on or after the date shown (we take weeks
        as Monday-Sunday, matching the GL's Sunday week-ending dates)."""
        best = None
        for shown, rate in sorted(self.union_rates.get(job_class, [])):
            starts = shown + timedelta(days=(7 - shown.weekday()) % 7)  # next Monday (or same day)
            if starts <= on:
                best = rate
        return best


# ---- MSAs -----------------------------------------------------------------------


def parse_msa(path: Path, findings: Findings) -> Contract | None:
    """Read one MSA; return None (and log it) if the parties or Schedule B can't be found."""
    text = pdf_text(path)
    flat = _flat(text)
    m = re.search(r'entered into as of ' + MONTH_DATE + r'.*?by and between (.+?) \("Client"\) and (.+?) \("Contractor"\)', flat)
    if not m:
        findings.exception(path.name, "header", "could not read parties/date of agreement", "contract skipped", 0.0)
        return None
    effective = datetime.strptime(m.group(1), "%B %d, %Y").date()
    client, contractor = m.group(2).strip(), m.group(3).strip()

    # Schedule B table: one cell per line. A row starts with a name line
    # followed by two whole numbers (day posts, night posts).
    sched = text.split("SCHEDULE B", 1)[1].split("Posts are staffed", 1)[0]
    lines = [ln.strip() for ln in sched.splitlines() if ln.strip()]
    starts = [i for i in range(len(lines) - 2)
              if lines[i + 1].isdigit() and lines[i + 2].isdigit() and not lines[i].isdigit()]
    buildings = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        terms_text = " ".join(lines[i + 3:end])
        fee = re.search(r"Fixed monthly fee of \$([\d,]+\.\d{2})", terms_text)
        plus = re.search(r"Cost plus (\d+(?:\.\d+)?)%", terms_text, re.I)
        billable = not re.search(r"not separately billable", terms_text, re.I) and bool(re.search(r"billable", terms_text, re.I))
        if not fee and not plus:
            findings.exception(path.name, lines[i], f"pricing basis not recognised: {terms_text!r}", "building kept, pricing unknown", 0.3)
        buildings.append(BuildingTerms(
            name=lines[i], day_posts=int(lines[i + 1]), night_posts=int(lines[i + 2]),
            pricing="cost_plus" if plus else "fixed",
            monthly_fee=Decimal(fee.group(1).replace(",", "")) if fee else None,
            markup=(Decimal(plus.group(1)) / 100) if plus else None,
            coverage_billable=billable,
        ))

    template = None
    if "EXHIBIT C" in text:
        exhibit = _flat(text.split("EXHIBIT C", 1)[1]).lower()
        if re.search(r"(not require|shall not include)[^.]*names", exhibit):
            template = "B"   # client forbids personnel names
        elif "full name" in exhibit or "time on site" in exhibit:
            template = "A"   # client requires names and actual times
        else:
            findings.exception(path.name, "Exhibit C", "backup requirements not recognised", "no backup notes for this client", 0.3)
    return Contract(path.name, client, contractor, effective, buildings, template)


def parse_amendment(path: Path, building_names: list[str], findings: Findings) -> list[PostChange]:
    """Find 'Effective <date>, the number of <day|night> posts at <building> ... from X to Y'."""
    flat = _flat(pdf_text(path))
    changes = []
    pattern = (r"Effective " + MONTH_DATE + r", the number of (day|night) posts at (.+?) stated in Schedule B is "
               r"(?:increased|decreased|changed) from (\d+) to (\d+)")
    for m in re.finditer(pattern, flat, re.I):
        building = m.group(3).strip()
        if building not in building_names:
            findings.exception(path.name, building, "amendment names a building not in any Schedule B", "change ignored", 0.2)
            continue
        changes.append(PostChange(path.name, datetime.strptime(m.group(1), "%B %d, %Y").date(), building,
                                  m.group(2).lower(), int(m.group(4)), int(m.group(5))))
    if not changes:
        findings.exception(path.name, "whole document", "no post-count change recognised in amendment",
                           "amendment not applied; needs manual review", 0.3)
    return changes


# ---- wage schedule --------------------------------------------------------------


def parse_wage_schedule(path: Path, findings: Findings) -> WageSchedule:
    """Read union rates by class and date, the non-union rate card and the burden percentages."""
    text = pdf_text(path)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ws = WageSchedule()

    # Appendix A: header "Effective MM/DD/YYYY" x N, then rows "Class, $r1..$rN"
    eff = [datetime.strptime(m, "%m/%d/%Y").date() for m in re.findall(r"Effective (\d{2}/\d{2}/\d{4})", text)]
    # The rate card section starts at its own heading line (the page title
    # "... and Rate Card" also contains the words, so match the exact heading).
    rate_card_at = next((i for i, ln in enumerate(lines) if ln.upper() == "RATE CARD"), len(lines))
    i = 0
    while i < len(lines):
        ln = lines[i]
        rates = []
        j = i + 1
        while j < len(lines) and re.fullmatch(r"\$[\d,]+\.\d{2}", lines[j]):
            rates.append(Decimal(lines[j][1:].replace(",", "")))
            j += 1
        if rates and re.fullmatch(r"[A-Za-z ]+", ln):
            if i < rate_card_at and len(rates) == len(eff):
                ws.union_rates[ln] = list(zip(eff, rates))
            elif i > rate_card_at and len(rates) == 1:
                ws.nonunion_rates[ln] = rates[0]
            i = j
        else:
            i += 1

    m = re.search(r"(\d+(?:\.\d+)?)% for bargaining unit employees and (\d+(?:\.\d+)?)% for non-union", _flat(text))
    if m:
        ws.union_burden = Decimal(m.group(1)) / 100
        ws.nonunion_burden = Decimal(m.group(2)) / 100
    else:
        findings.exception(path.name, "burden", "labor burden percentages not found", "burden taken as 0", 0.0)
    if not ws.union_rates or not ws.nonunion_rates:
        findings.exception(path.name, "rates", "wage schedule or rate card not parsed", "rates missing", 0.0)
    return ws


# ---- all contract documents ----------------------------------------------------


def load_contracts(contracts_dir: Path, findings: Findings):
    """Sort each PDF by what it is (wage schedule, amendment, agreement)."""
    contracts, amendment_files, wages = [], [], None
    for path in sorted(contracts_dir.glob("*.pdf")):
        head = _flat(pdf_text(path))[:600].upper()
        if "WAGE SCHEDULE" in head:
            wages = parse_wage_schedule(path, findings)
        elif "AMENDMENT" in head:
            amendment_files.append(path)
        elif "MASTER SERVICES AGREEMENT" in head:
            c = parse_msa(path, findings)
            if c:
                contracts.append(c)
        else:
            findings.exception(path.name, "whole document", "unrecognised contract document", "ignored", 0.2)
    names = [b.name for c in contracts for b in c.buildings]
    changes = [ch for p in amendment_files for ch in parse_amendment(p, names, findings)]
    if wages is None:
        findings.exception("contracts", "-", "no wage schedule found", "pay rates unavailable", 0.0)
        wages = WageSchedule()
    return contracts, changes, wages


def posts_in_force(contract_terms: BuildingTerms, changes: list[PostChange], on: date) -> tuple[int, int]:
    """Day and night posts for a building on a date, with amendments applied
    in effective-date order (an amendment overrides from its effective date)."""
    day, night = contract_terms.day_posts, contract_terms.night_posts
    for ch in sorted(changes, key=lambda c: c.effective_date):
        if ch.building == contract_terms.name and ch.effective_date <= on:
            if ch.shift == "day":
                day = ch.new_posts
            else:
                night = ch.new_posts
    return day, night
