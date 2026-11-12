"""Turn the day words the LLM found ('tonight', 'tomorrow', 'Friday',
'10/22') into calendar dates. Pure code, so it is predictable and tested.

The reference point is when the employee actually spoke: the voicemail
time, the phone call time, or the time a manager says the text arrived.
NOT when reception transcribed it the next morning.

One judgment call (see decisions log): a voicemail left after midnight but
before the morning that says "tomorrow" means the coming shift, i.e. the
same calendar date. People who call at 00:25 still think of it as the
previous evening. Confirmed by dispatch postings and punches in this data;
flagged with lower confidence so a reviewer can see it.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
EARLY_HOURS_END = 5   # 00:00-04:59 counts as "still last night"


@dataclass
class ResolvedDay:
    """A calendar date worked out from a day word, with confidence and a note saying how."""
    day: date
    confidence: float
    note: str


def resolve_days(refs: list, spoken_at: datetime) -> list[ResolvedDay]:
    """refs: DayReference-like objects with .kind and .value."""
    base = spoken_at.date()
    out: list[ResolvedDay] = []
    for ref in refs:
        kind, value = ref.kind, (ref.value or "").strip().lower()
        if kind == "relative":
            if value in ("today", "tonight"):
                out.append(ResolvedDay(base, 1.0, f"'{value}' said on {base:%a %m/%d}"))
            elif value == "tomorrow":
                if spoken_at.hour < EARLY_HOURS_END:
                    out.append(ResolvedDay(base, 0.8, f"'tomorrow' said at {spoken_at:%H:%M} (after midnight) "
                                                      f"read as the coming shift on {base:%a %m/%d}"))
                else:
                    out.append(ResolvedDay(base + timedelta(days=1), 1.0, f"'tomorrow' said on {base:%a %m/%d}"))
            else:
                out.append(ResolvedDay(base, 0.3, f"unrecognised relative day '{value}'"))
        elif kind == "weekday" and value in WEEKDAYS:
            ahead = (WEEKDAYS.index(value) - base.weekday()) % 7
            if ahead == 0:
                # "Monday" said on a Monday: tonight if said early, else next week - unclear
                out.append(ResolvedDay(base, 0.5, f"'{value}' said on the same weekday; taken as today"))
            else:
                out.append(ResolvedDay(base + timedelta(days=ahead), 1.0, f"next {value} after {base:%a %m/%d}"))
        elif kind == "date":
            try:
                m, d = [int(x) for x in value.replace("-", "/").split("/")[:2]]
                day = date(base.year, m, d)
                if day < base - timedelta(days=180):
                    day = date(base.year + 1, m, d)
                out.append(ResolvedDay(day, 1.0, f"explicit date {value}"))
            except ValueError:
                out.append(ResolvedDay(base, 0.3, f"unreadable date '{value}'"))
        else:
            out.append(ResolvedDay(base, 0.3, f"unrecognised day reference {kind}:{value}"))
    # same day mentioned twice ('tonight ... this evening') counts once
    seen, unique = set(), []
    for r in out:
        if r.day not in seen:
            seen.add(r.day)
            unique.append(r)
    return unique
