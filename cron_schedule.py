"""Tiny dependency-free 5-field cron engine (minute hour day-of-month month day-of-week).

Powers agent_automations.py: schedule validation, due checks in the scheduler tick,
next-run computation and human-readable labels for the UI. Vixie-cron semantics:
`*`, lists `a,b`, ranges `a-b`, steps `*/n` / `a-b/n`; in day-of-week 0 and 7 are
both Sunday; when BOTH day-of-month and day-of-week are restricted, the day matches
if EITHER does. All datetimes are naive-agnostic: the caller passes MSK datetimes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_FIELD_NAMES = ("минуты", "часы", "день месяца", "месяц", "день недели")

_DOW_SHORT = ("вс", "пн", "вт", "ср", "чт", "пт", "сб")  # cron order: 0=Sunday
_DOW_PLURAL = ("по воскресеньям", "по понедельникам", "по вторникам", "по средам",
               "по четвергам", "по пятницам", "по субботам")


def _parse_field(spec: str, lo: int, hi: int) -> set[int] | None:
    """One cron field -> set of allowed ints, or None for an unrestricted '*'."""
    spec = spec.strip()
    if spec == "*":
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError(f"шаг должен быть ≥ 1: /{step_s}")
        if part in ("", "*"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = int(part)
            end = hi if step > 1 else start  # vixie: "N/step" means N..hi/step
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise ValueError(f"значение вне диапазона {lo}-{hi}: {part}")
        out.update(range(start, end + 1, step))
    return out


def parse(expr: str) -> list[set[int] | None]:
    """Parse a 5-field cron expression; raises ValueError with a Russian message."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise ValueError("нужно 5 полей: минуты часы день-месяца месяц день-недели (например «0 9 * * 1-5»)")
    sets: list[set[int] | None] = []
    for i, (spec, (lo, hi)) in enumerate(zip(parts, _FIELD_RANGES)):
        try:
            s = _parse_field(spec, lo, hi)
        except ValueError as exc:
            raise ValueError(f"поле «{_FIELD_NAMES[i]}» ({spec}): {exc}") from None
        if i == 4 and s is not None:
            s = {v % 7 for v in s}  # 7 == 0 == Sunday
        sets.append(s)
    return sets


def _day_matches(sets: list[set[int] | None], dt: datetime) -> bool:
    dom, dow = sets[2], sets[4]
    cron_dow = (dt.weekday() + 1) % 7  # python Mon=0 -> cron Sun=0
    dom_ok = dom is None or dt.day in dom
    dow_ok = dow is None or cron_dow in dow
    if dom is not None and dow is not None:
        return dom_ok or dow_ok  # vixie OR when both are restricted
    return dom_ok and dow_ok


def matches(expr: str, dt: datetime) -> bool:
    sets = parse(expr)
    if sets[3] is not None and dt.month not in sets[3]:
        return False
    if not _day_matches(sets, dt):
        return False
    if sets[1] is not None and dt.hour not in sets[1]:
        return False
    return sets[0] is None or dt.minute in sets[0]


def next_run(expr: str, after: datetime) -> datetime | None:
    """First fire time strictly after `after` (minute resolution); None if not
    within ~400 days (e.g. an impossible date like Feb 30)."""
    sets = parse(expr)
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=400)
    while dt <= limit:
        if sets[3] is not None and dt.month not in sets[3]:
            # jump to the 1st of the next month
            dt = (dt.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            continue
        if not _day_matches(sets, dt):
            dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if sets[1] is not None and dt.hour not in sets[1]:
            dt = (dt + timedelta(hours=1)).replace(minute=0)
            continue
        if sets[0] is not None and dt.minute not in sets[0]:
            dt += timedelta(minutes=1)
            continue
        return dt
    return None


def max_fires_per_day(expr: str) -> int:
    """Upper bound of fires within one day — the rate guard for automations
    (every run is a full LLM turn, so frequency must be capped)."""
    sets = parse(expr)
    minutes = len(sets[0]) if sets[0] is not None else 60
    hours = len(sets[1]) if sets[1] is not None else 24
    return minutes * hours


def _time_label(sets: list[set[int] | None]) -> str | None:
    """«9:00» when the expression fires at exactly one time of day."""
    if sets[0] is not None and len(sets[0]) == 1 and sets[1] is not None and len(sets[1]) == 1:
        return f"{next(iter(sets[1]))}:{next(iter(sets[0])):02d}"
    return None


def describe(expr: str) -> str:
    """Compact human label in Russian; falls back to the raw expression."""
    try:
        sets = parse(expr)
    except ValueError:
        return expr
    minute, hour, dom, month, dow = sets
    if month is not None:
        return expr
    at = _time_label(sets)

    # sub-daily frequencies: */n minutes / every hour at :mm
    if at is None:
        if hour is None and minute is not None:
            step = sorted(minute)
            if len(step) > 1 and len(set(b - a for a, b in zip(step, step[1:]))) == 1 and step[0] == 0:
                return f"каждые {step[1] - step[0]} мин"
            if len(step) == 1 and dom is None and dow is None:
                return f"каждый час в :{step[0]:02d}"
        if minute is None and hour is None:
            return "каждую минуту"
        return expr

    if dom is None and dow is None:
        return f"ежедневно в {at}"
    if dom is None and dow is not None:
        days = sorted(dow)
        if days == [1, 2, 3, 4, 5]:
            return f"по будням в {at}"
        if days == [0, 6]:
            return f"по выходным в {at}"
        if days == [0, 1, 2, 3, 4, 6]:
            return f"ежедневно, кроме {_DOW_SHORT[5]}, в {at}"
        if len(days) == 1:
            return f"{_DOW_PLURAL[days[0]]} в {at}"
        return ", ".join(_DOW_SHORT[d] for d in days) + f" в {at}"
    if dow is None and dom is not None and len(dom) == 1:
        return f"{next(iter(dom))}-го числа в {at}"
    return expr
