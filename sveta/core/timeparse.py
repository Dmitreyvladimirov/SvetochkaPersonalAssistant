"""Russian time phrases → an aware datetime in the user's timezone (FR-19).

The model never computes a date (SPEC.md §6.1): it hands the user's own words to
the reminder tool, and this module turns them into a moment. Everything it
understands is enumerable and every form has a test; anything else returns None
and the tool asks the user to rephrase rather than guessing.

Forms (docs/stages/stage2.md → Reminders):
  relative   через 20 минут · через час · через 2 часа · через полчаса ·
             через 3 дня · через неделю · через месяц
  day        сегодня · завтра · послезавтра · в четверг · в пятницу вечером ·
             25 сентября [2027] · 25.09 · 15 числа
  time       в 11 · в 11:30 · в 11.30 · в 9 утра · в 7 вечера · в 11 часов ·
             в полдень · утром 09:00 · днём 13:00 · вечером 19:00 · ночью 22:00
Numbers may be spelled out ("в девять", "через двадцать минут"), as Whisper
writes them. A bare time with no day means today if still ahead, otherwise
tomorrow. A day with no time means 09:00.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DAY_PARTS = {"утром": 9, "утра": 9, "днем": 13, "дня": 13, "вечером": 19, "вечера": 19,
             "ночью": 22, "ночи": 22}
WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
            "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5,
            "воскресенье": 6}
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
          "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
          "декабря": 12}
DEFAULT_HOUR = 9

_RELATIVE = re.compile(
    r"через\s+(?:(\d+)\s*)?(полчаса|пол\s?часа|минут\w*|час\w*|дн\w*|день|недел\w*|месяц\w*)")
# "в 11", "в 11:30", "в 11.30", "в 9 утра", "в 7 вечера", "в 11 часов"
_CLOCK = re.compile(
    r"(?:^|\s)в\s+(\d{1,2})(?:[:.](\d{2}))?(?:\s*(?:час(?:а|ов)?))?(?:\s+(утра|дня|вечера|ночи))?(?=\s|$|[,!?])")
_DATE_MONTH = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?")
# A day.month[.year] — never a decimal ("1.5 часа"), never a clock (those are
# blanked out before this runs), never followed by a unit word.
_DATE_DOT = re.compile(
    r"(?<![\d.])(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?(?![\d.])(?!\s*(?:час|мин|дн|тыс|км|кг|%))")
_DATE_DAY = re.compile(r"(\d{1,2})\s+числа")
_WEEKDAY = re.compile(r"(?:^|\s)(?:в|во)\s+(" + "|".join(WEEKDAYS) + r")")
_DAYPART = re.compile(r"(?:^|\s)(утром|днем|вечером|ночью)(?=\s|$|[,.!?])")

_UNITS = {"один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
          "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
          "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
          "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
          "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
_NUMBER_WORDS = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:\s+(" + "|".join(_UNITS) + r"))?|(" + "|".join(_UNITS) + r"))\b")


def _words_to_digits(text: str) -> str:
    def repl(m):
        if m.group(1):
            return str(_TENS[m.group(1)] + (_UNITS[m.group(2)] if m.group(2) else 0))
        return str(_UNITS[m.group(3)])
    return _NUMBER_WORDS.sub(repl, text)


def _norm(s: str) -> str:
    return _words_to_digits(re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip())


def parse(phrase: str, *, now: datetime, tz: str) -> datetime | None:
    """`now` may be naive (taken as UTC) or aware; the result is aware in `tz`."""
    zone = ZoneInfo(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(zone)
    text = _norm(phrase)
    if not text:
        return None

    rel = _RELATIVE.search(text)
    if rel:
        result = _relative(rel, now, zone)
        if result is None:
            return None
        clock, _ = _clock(text)
        if clock and rel.group(2)[:2] in ("дн", "де", "не", "ме"):
            result = result.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        return result.replace(second=0, microsecond=0)
    if "через" in text:
        return None   # a relative phrase we could not read must not fall through to a date

    clock, rest = _clock(text)
    part = _DAYPART.search(text)
    if clock is not None:
        hour, minute, explicit = clock
        if part is not None and not explicit:
            # "утром в 8" keeps 8; "вечером в 8" means 20 — the day part is the am/pm.
            if part.group(1) in ("вечером", "ночью") and hour < 12:
                hour += 12
            explicit = True
    elif part is not None:
        hour, minute, explicit = DAY_PARTS[part.group(1)], 0, True
    else:
        hour, minute, explicit = DEFAULT_HOUR, 0, False

    day = _day(rest, now, (hour, minute))
    if day is None and clock is None and part is None:
        return None

    if day is None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            # "в 7" said at 10:00 almost always means 19:00, not tomorrow morning.
            if not explicit and hour < 12 and now.replace(hour=hour + 12, minute=minute,
                                                          second=0, microsecond=0) > now:
                candidate = candidate.replace(hour=hour + 12)
            else:
                candidate += timedelta(days=1)
        return candidate
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _relative(m: re.Match, now: datetime, zone: ZoneInfo) -> datetime | None:
    """Minutes and hours are added in UTC so a DST switch in between does not
    shift the result; days and weeks keep the wall-clock time; months are
    calendar arithmetic."""
    n = int(m.group(1)) if m.group(1) else 1
    unit = m.group(2)
    if unit.startswith("пол"):
        delta = timedelta(minutes=30)
    elif unit.startswith("минут"):
        delta = timedelta(minutes=n)
    elif unit.startswith("час"):
        delta = timedelta(hours=n)
    elif unit.startswith("дн") or unit == "день":
        return now + timedelta(days=n)
    elif unit.startswith("недел"):
        return now + timedelta(weeks=n)
    elif unit.startswith("месяц"):
        month = now.month - 1 + n
        year = now.year + month // 12
        month = month % 12 + 1
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        day = min(now.day, [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return now.replace(year=year, month=month, day=day)
    else:
        return None
    return (now.astimezone(timezone.utc) + delta).astimezone(zone)


def _clock(text: str) -> tuple[tuple[int, int, bool] | None, str]:
    """(hour, minute, explicit) for the first *valid* clock phrase, and the text
    with that phrase blanked so the date patterns never see it. explicit means
    the phrase named am/pm or used minutes, so no 12-hour guessing is allowed."""
    if "в полдень" in text or text.startswith("полдень"):
        return (12, 0, True), text.replace("полдень", " ")
    if "в полночь" in text:
        return (0, 0, True), text.replace("полночь", " ")
    valid = [m for m in _CLOCK.finditer(text)
             if int(m.group(1)) <= 23 and int(m.group(2) or 0) <= 59]
    for i, m in enumerate(valid):
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        # "в 5.10 в 18": a dotted pair followed by another clock is a date, not 05:10.
        if (m.group(2) and "." in m.group(0) and i + 1 < len(valid)
                and 1 <= hour <= 31 and 1 <= minute <= 12):
            continue
        qualifier = m.group(3)
        explicit = bool(m.group(2)) or bool(qualifier) or "час" in m.group(0)
        if qualifier in ("вечера", "дня") and hour < 12:
            hour += 12
        elif qualifier == "ночи" and hour == 12:
            hour = 0
        blanked = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
        return (hour, minute, explicit), blanked
    return None, text


def _day(text: str, now: datetime, at: tuple[int, int]) -> datetime | None:
    hour, minute = at
    if "послезавтра" in text:
        return now + timedelta(days=2)
    if "завтра" in text:
        return now + timedelta(days=1)
    if "сегодня" in text:
        return now
    m = _WEEKDAY.search(text)
    if m:
        target = WEEKDAYS[m.group(1)]
        ahead = (target - now.weekday()) % 7
        # The same weekday means today only while the named (or default) time is
        # still ahead; otherwise next week.
        if ahead == 0 and now.replace(hour=hour, minute=minute, second=0, microsecond=0) <= now:
            ahead = 7
        return now + timedelta(days=ahead)
    m = _DATE_MONTH.search(text)
    if m:
        day, month = int(m.group(1)), MONTHS[m.group(2)]
        if m.group(3):
            try:
                return now.replace(year=int(m.group(3)), month=month, day=day)
            except ValueError:
                return None
        return _date_this_or_next_year(now, month, day)
    m = _DATE_DOT.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3))
            year = year + 2000 if year < 100 else year
            try:
                return now.replace(year=year, month=month, day=day)
            except ValueError:
                return None
        return _date_this_or_next_year(now, month, day)
    m = _DATE_DAY.search(text)
    if m:
        day = int(m.group(1))
        try:
            candidate = now.replace(day=day)
        except ValueError:
            return None
        if candidate.date() < now.date():
            month = now.month % 12 + 1
            year = now.year + (1 if month == 1 else 0)
            try:
                candidate = now.replace(year=year, month=month, day=day)
            except ValueError:
                return None
        return candidate
    return None


def _date_this_or_next_year(now: datetime, month: int, day: int) -> datetime | None:
    try:
        candidate = now.replace(month=month, day=day)
    except ValueError:
        return None
    if candidate.date() < now.date():
        try:
            candidate = candidate.replace(year=now.year + 1)
        except ValueError:
            return None
    return candidate


def fmt(dt: datetime, now: datetime | None = None) -> str:
    """How the reply names the moment: 'чт 18.09 в 11:00', with 'сегодня'/'завтра'
    when that is what it is."""
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    label = f"{days[dt.weekday()]} {dt:%d.%m}"
    if now is not None:
        now = now.astimezone(dt.tzinfo)
        if dt.date() == now.date():
            label = "сегодня"
        elif dt.date() == (now + timedelta(days=1)).date():
            label = "завтра"
    return f"{label} в {dt:%H:%M}"
