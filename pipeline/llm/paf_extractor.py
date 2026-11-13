"""Read scanned Personnel Action Forms (image PDFs) with Claude's PDF input
and a strict schema. Whether a form takes effect is decided in code: it needs
all three approvals (HR, Operations, Payroll) signed.
"""
import base64
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .client import extract


class Approval(BaseModel):
    """One approval row on the form: who, whether it is actually signed, and when."""
    role: str = Field(description="approval row label, e.g. 'Human Resources', 'Operations', 'Payroll'")
    signed: bool = Field(description="true only if a signature is present on that row")
    signer: str | None
    date: str | None = Field(description="MM/DD/YYYY as written, or null")


class PafExtraction(BaseModel):
    """Everything Claude reads from one scanned Personnel Action Form."""
    action: Literal["new_hire", "transfer", "termination", "rate_change", "leave_of_absence", "other"]
    employee_id: str | None = Field(description="as written, e.g. 'E10044'")
    employee_name: str | None
    job_class: str | None
    effective_date: str | None = Field(description="MM/DD/YYYY as written")
    from_location: str | None = Field(description="transfer 'From' field as written, e.g. 'BLD-05 (Bramwell House), Night, Dept 450'")
    to_location: str | None = Field(description="transfer 'To' field, or the 'Location' field for hires/terminations")
    last_day_worked: str | None
    remarks: str | None
    approvals: list[Approval]
    confidence: float = Field(ge=0, le=1)


SYSTEM = """You transcribe scanned HR Personnel Action Forms into the given schema.
Copy values exactly as written. Mark an approval as signed only if a signature is visible on that row;
an empty signature cell is not signed. Use null for blank fields."""


def extract_paf(path: Path) -> PafExtraction:
    """Send the scanned PDF itself to Claude (no OCR step) and get back a validated PafExtraction."""
    data = base64.standard_b64encode(path.read_bytes()).decode()
    content = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
        {"type": "text", "text": "Transcribe this Personnel Action Form."},
    ]
    return extract("paf", path.stem, SYSTEM, content, PafExtraction)
