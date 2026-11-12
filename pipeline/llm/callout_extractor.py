"""Turn one call-out message (reception log row or chat message) into a
structured event, using an LLM with a strict schema.

The LLM only READS: who is calling, about which building and shift, which
day(s), and what kind of absence it is. It does NOT decide dates from
relative words, notice, entitlement or payroll codes. That is deterministic
code in pipeline/rules/.
"""
from typing import Literal

from pydantic import BaseModel, Field

from ..ingest.callout_sources import CalloutReport
from .client import extract

Category = Literal[
    "sick",               # the employee is ill
    "family_emergency",   # a family member is ill (incl. a sick child), other urgent family matter
    "clinic",             # scheduled medical appointment / "using my clinic day"
    "bereavement",        # death in the family
    "jury_duty",
    "personal",           # personal day
    "vacation",           # planned vacation (not a call-out)
    "other_absence",      # absent, reason unclear
    "not_absence",        # late arrival, admin question, confirmation of an earlier call ...
]


class DayReference(BaseModel):
    """One day the message mentions, as the words say it (not yet a date)."""
    kind: Literal["relative", "weekday", "date"] = Field(
        description="relative = today/tonight/tomorrow; weekday = a named day; date = an explicit calendar date")
    value: str = Field(description="relative: 'today' or 'tomorrow' ('tonight' counts as 'today'). "
                                   "weekday: lowercase English day name, e.g. 'friday'. date: 'MM/DD' as written.")
    text: str = Field(description="the words in the message this came from, e.g. 'nesër', 'esta noche', 'Friday'")


class CalloutExtraction(BaseModel):
    """Everything the LLM reads from one call-out message."""
    is_absence: bool = Field(description="true if the message reports that an employee will miss (or missed) a shift")
    is_followup: bool = Field(description="true if the message only confirms or repeats an earlier call-out "
                                          "(e.g. 'called back to confirm', 'said they called the front desk too')")
    employee_name: str | None = Field(description="the absent employee's name exactly as written (not the manager who relayed it)")
    building_text: str | None = Field(description="the building as written, or null if not stated ('the building' = null)")
    shift: Literal["day", "night"] | None = Field(description="day or night if stated or clearly implied ('tonight' = night); else null")
    days: list[DayReference] = Field(description="every day the absence covers, in order. Empty if none stated.")
    category: Category
    bereavement_relation: str | None = Field(description="for bereavement: the relative who died, in English (e.g. 'father')")
    stated_received_time: str | None = Field(
        description="if the message says WHEN the call-out was actually received or made (e.g. 'texted me this morning "
                    "around 10:32', 'it's about 11 in the morning'), that time as 24h 'HH:MM'; else null.")
    language: str = Field(description="language of the message, e.g. 'en', 'es', 'sq'")
    english_summary: str = Field(description="one short English sentence: who, when, why")
    confidence: float = Field(ge=0, le=1, description="how sure you are about the fields above")


SYSTEM = """You read attendance messages for a commercial cleaning company and extract facts into the given schema.
Messages come from a reception call log (phone calls and transcribed voicemails, often in Spanish or Albanian)
and from area managers' group chat (managers relaying texts from employees, using first names or initials).

Rules:
- Extract only what the message says. Do not guess names, dates or buildings that are not there.
- For relative days, report the words; do not convert them to dates. 'tonight'/'esta noche'/'sonte' -> relative 'today'
  with shift night. 'hoy'/'sot' -> 'today'. 'mañana'/'nesër' -> 'tomorrow'.
- A child or family member being sick is family_emergency, not sick.
- 'Using my clinic day' or a medical appointment is clinic.
- Running late, payroll/admin questions and confirmations of an earlier call are not_absence
  (set is_followup=true for confirmations or 'called the front desk too').
- In chat, the employee is the person the manager talks about, not the sender.
- confidence below 0.7 if the name, day or reason is unclear."""


def extract_callout(report: CalloutReport) -> CalloutExtraction:
    """Send one message, with its source/time header, to the LLM and get back a validated CalloutExtraction."""
    header = (f"Source: {report.source}\nRecorded: {report.logged_at:%A %m/%d/%Y %H:%M}\n"
              f"Channel: {report.channel}\n"
              + (f"Voicemail received: {report.vm_received_at:%A %m/%d/%Y %H:%M}\n" if report.vm_received_at else "")
              + (f"Caller column: {report.caller}\n" if report.source == "reception_log" else f"Chat sender: {report.caller}\n"))
    content = [{"type": "text", "text": header + f"Message:\n{report.message}"}]
    return extract("callout", f"{report.source}-{report.source_ref}", SYSTEM, content, CalloutExtraction)
