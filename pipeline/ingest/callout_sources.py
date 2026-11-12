"""Read the two places call-outs arrive: the reception spreadsheet and the
area managers' chat export. This step only reads text and timestamps; the
LLM interprets the words later.

Each report keeps:
- source / source_ref: how to find it again (spreadsheet row number, or the
  chat message timestamp exactly as exported)
- logged_at: when reception typed it / when the chat message was sent
- vm_received_at: the phone system's voicemail time, when there is one
"""
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

from ..common import Findings

RECEPTION = "reception_log"
CHAT = "area_manager_chat"


@dataclass
class CalloutReport:
    """One call-out message, from either the reception log or the chat."""
    source: str
    source_ref: str
    logged_at: datetime
    channel: str                    # Phone, Voicemail, Chat ...
    vm_received_at: datetime | None
    caller: str                     # reception "Caller" column, or the chat sender
    message: str
    taken_by: str


def _cell_text(v) -> str:
    """Cell value as trimmed text; an empty cell gives ''."""
    return "" if v is None else str(v).strip()


def _parse_dt(date_v, time_v, tz: ZoneInfo) -> datetime:
    """Combine the Date and Time cells into one timezone-aware datetime."""
    d = date_v if isinstance(date_v, datetime) else datetime.strptime(_cell_text(date_v), "%m/%d/%Y")
    if isinstance(time_v, datetime):
        t = time_v.time()
    elif hasattr(time_v, "hour"):
        t = time_v
    else:
        t = datetime.strptime(_cell_text(time_v).upper(), "%I:%M %p").time()
    return datetime.combine(d.date(), t, tzinfo=tz)


def _parse_vm(value, logged: datetime) -> datetime | None:
    """VM Received is 'MM/DD HH:MM' with no year: take the year of the log
    entry, or the year before if that would put the voicemail after the log."""
    text = _cell_text(value)
    if not text:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=logged.tzinfo)
    vm = datetime.strptime(f"{logged.year}/{text}", "%Y/%m/%d %H:%M").replace(tzinfo=logged.tzinfo)
    if vm > logged:
        vm = vm.replace(year=logged.year - 1)
    return vm


def load_reception_log(path: Path, tz: ZoneInfo, findings: Findings) -> list[CalloutReport]:
    """Read the reception spreadsheet; the header row is found by its column names, not its position."""
    wb = openpyxl.load_workbook(path, data_only=True)
    reports = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = None
        for row_no, row in enumerate(rows, start=1):
            cells = [_cell_text(v) for v in row]
            if header is None:
                if "Date" in cells and "Message" in cells:
                    header = {name: i for i, name in enumerate(cells) if name}
                continue
            if not any(cells):
                continue
            get = lambda col: row[header[col]] if col in header else None  # noqa: E731
            ref = str(row_no) if len(wb.worksheets) == 1 else f"{ws.title}!{row_no}"
            try:
                logged = _parse_dt(get("Date"), get("Time"), tz)
                reports.append(CalloutReport(
                    source=RECEPTION, source_ref=ref, logged_at=logged,
                    channel=_cell_text(get("Channel")),
                    vm_received_at=_parse_vm(get("VM Received"), logged),
                    caller=_cell_text(get("Caller")), message=_cell_text(get("Message")),
                    taken_by=_cell_text(get("Taken By")),
                ))
            except ValueError as exc:
                findings.exception(path.name, f"row {ref}", f"unreadable date/time: {exc}", "row skipped", 0.0)
    return reports


CHAT_LINE = re.compile(r"^\[(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)\] ([^:]+?): (.*)$")


def load_chat(path: Path, tz: ZoneInfo, findings: Findings) -> list[CalloutReport]:
    """WhatsApp-style export: '[10/07/26, 9:47:00 AM] Name: text'. Lines without
    a 'Name:' part are system lines (group created, encryption notice)."""
    reports = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = CHAT_LINE.match(line.strip())
        if not m:
            if line.strip() and not line.startswith("["):
                # continuation of a multi-line message
                if reports:
                    reports[-1].message += "\n" + line.strip()
            continue
        date_s, time_s, sender, text = m.groups()
        fmt_d = "%m/%d/%y" if len(date_s.split("/")[-1]) == 2 else "%m/%d/%Y"
        fmt_t = "%I:%M:%S %p" if time_s.count(":") == 2 else "%I:%M %p"
        sent = datetime.strptime(f"{date_s} {time_s.strip()}", f"{fmt_d} {fmt_t}").replace(tzinfo=tz)
        ref = f"{date_s}, {time_s.strip()}"   # timestamp as exported
        reports.append(CalloutReport(CHAT, ref, sent, "Chat", None, sender.strip(), text.strip(), sender.strip()))
    return reports
