"""Turn raw punches into worked shifts ("sessions").

For each badge, punches are read in time order: an IN opens a session, the
next OUT closes it (breaks in between are ignored). The session belongs to
the shift whose start time is closest to the clock-in, and to the date the
shift STARTS on (so a night shift ending at 00:10 stays on its start date).
A lone OUT, or an IN with no OUT, gives a session with a missing time.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .ingest.time_attendance import Punch
from .rules.shifts import DAY_START, NIGHT_START


@dataclass
class Session:
    """One worked shift from the time clock: who, where, which shift, in and out times."""
    badge: int
    building: str | None
    day: date
    shift: str
    clock_in: datetime | None
    clock_out: datetime | None
    refs: str          # source file rows, for drill-down


def _shift_for(t: datetime) -> tuple[date, str]:
    """Nearest shift start to time t (looking at yesterday's night too)."""
    options = []
    for d in (t.date() - timedelta(days=1), t.date()):
        for name, st in (("day", DAY_START), ("night", NIGHT_START)):
            start = datetime.combine(d, st, tzinfo=t.tzinfo)
            options.append((abs((t - start).total_seconds()), d, name))
    _, d, name = min(options)
    return d, name


def _shift_for_out(t: datetime) -> tuple[date, str]:
    """For an OUT with no IN: the shift that most recently ended."""
    d, name = _shift_for(t - timedelta(hours=8))
    return d, name


def build_sessions(punches: list[Punch], building_of) -> list[Session]:
    """Pair each badge's IN and OUT punches into sessions; a lone IN or OUT gives a session with a missing time."""
    by_badge = defaultdict(list)
    for p in punches:
        by_badge[p.badge].append(p)
    sessions = []
    for badge, ps in by_badge.items():
        ps.sort(key=lambda p: p.at)
        open_in: Punch | None = None
        for p in ps:
            if p.event == "IN":
                if open_in:   # IN without OUT
                    d, s = _shift_for(open_in.at)
                    sessions.append(Session(badge, building_of(open_in), d, s, open_in.at, None, _ref(open_in)))
                open_in = p
            elif p.event == "OUT":
                if open_in and (p.at - open_in.at) < timedelta(hours=16):
                    d, s = _shift_for(open_in.at)
                    sessions.append(Session(badge, building_of(open_in), d, s, open_in.at, p.at,
                                            f"{_ref(open_in)}; {_ref(p)}"))
                    open_in = None
                else:
                    if open_in:
                        d, s = _shift_for(open_in.at)
                        sessions.append(Session(badge, building_of(open_in), d, s, open_in.at, None, _ref(open_in)))
                        open_in = None
                    d, s = _shift_for_out(p.at)
                    sessions.append(Session(badge, building_of(p), d, s, None, p.at, _ref(p)))
        if open_in:
            d, s = _shift_for(open_in.at)
            sessions.append(Session(badge, building_of(open_in), d, s, open_in.at, None, _ref(open_in)))
    return sessions


def _ref(p: Punch) -> str:
    """'file#row' of a punch, for drill-down."""
    return f"{p.source_file}#{p.source_row}"
