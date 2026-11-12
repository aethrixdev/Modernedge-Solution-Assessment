"""Shift times and calendar helpers, from Article 14.1 and the KPI definitions.

Day shift 07:00-15:00, night shift 16:00-00:00, Monday-Friday, 8 paid hours.
A night shift belongs to the date it starts.
"""
import os
from datetime import date, datetime, time, timedelta

SHIFT_HOURS = 8
NOTICE_HOURS = 4                      # Article 14.2
DAY_START = time.fromisoformat(os.getenv("DAY_SHIFT_START", "07:00"))
NIGHT_START = time.fromisoformat(os.getenv("NIGHT_SHIFT_START", "16:00"))
DAY_END = time.fromisoformat(os.getenv("DAY_SHIFT_END", "15:00"))
NIGHT_END = time.fromisoformat(os.getenv("NIGHT_SHIFT_END", "00:00"))   # midnight, next calendar day


def shift_start(day: date, shift: str, tz) -> datetime:
    """When a day or night shift starts on `day`."""
    return datetime.combine(day, DAY_START if shift == "day" else NIGHT_START, tzinfo=tz)


def shift_end(day: date, shift: str, tz) -> datetime:
    """When it ends; a night shift ending at midnight ends on the next calendar day."""
    end = datetime.combine(day, DAY_END if shift == "day" else NIGHT_END, tzinfo=tz)
    return end if end > shift_start(day, shift, tz) else end + timedelta(days=1)


def shift_window(day: date, shift: str) -> tuple[str, str]:
    """('07:00', '15:00') for backup notes."""
    return (DAY_START if shift == "day" else NIGHT_START).strftime("%H:%M"), \
           (DAY_END if shift == "day" else NIGHT_END).strftime("%H:%M")


def is_workday(day: date) -> bool:
    """Monday to Friday."""
    return day.weekday() < 5


def weekdays(first: date, last: date) -> list[date]:
    """Every workday from `first` to `last`, inclusive."""
    out, d = [], first
    while d <= last:
        if is_workday(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def notice_hours(received_at: datetime, day: date, shift: str) -> float:
    """Hours between when the call-out was received and the shift start."""
    return (shift_start(day, shift, received_at.tzinfo) - received_at).total_seconds() / 3600
