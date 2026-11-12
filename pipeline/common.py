"""Small helpers shared by every step: text clean-up, money, and the running
list of exceptions and data findings that every step can add to.
"""
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal


def digits(text) -> str:
    """Keep only the digits: 'E10033' -> '10033'."""
    return re.sub(r"\D", "", str(text or ""))


def parse_amount(text) -> Decimal:
    """'72,400.00' -> 72400.00 ; '(1,250.00)' -> -1250.00 ; '1250.00-' -> -1250.00"""
    s = str(text).strip().replace(",", "").replace("$", "")
    negative = s.startswith("(") and s.endswith(")") or s.endswith("-") or s.startswith("-")
    s = s.strip("()-")
    value = Decimal(s) if s else Decimal("0")
    return -value if negative else value


@dataclass
class ExceptionRow:
    """One row of exceptions.csv."""
    source: str          # which file/system the record came from
    record_ref: str      # row number, id, timestamp ... enough to find it again
    issue: str           # what is wrong or uncertain
    action_taken: str    # what the pipeline did about it
    confidence: float    # how sure we are about the action taken (0..1)


@dataclass
class Findings:
    """Collected while the pipeline runs, written out at the end."""
    exceptions: list[ExceptionRow] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)   # go into the decision log
    tieouts: list[str] = field(default_factory=list)     # control totals checked

    def exception(self, source, record_ref, issue, action_taken, confidence=0.5):
        """Record a problem with one record and what we did about it."""
        self.exceptions.append(ExceptionRow(source, str(record_ref), issue, action_taken, round(confidence, 2)))

    def conflict(self, text: str):
        """Record a conflict between sources once (duplicates are ignored)."""
        if text not in self.conflicts:
            self.conflicts.append(text)

    def exception_dicts(self) -> list[dict]:
        """Exceptions as plain dicts, ready for a CSV writer."""
        return [asdict(e) for e in self.exceptions]
