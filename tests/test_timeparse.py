"""Every supported phrase, against a fixed clock. Thursday 2026-09-10 10:00
Asia/Jerusalem (UTC+3 in September)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from sveta.core.timeparse import fmt, parse

TZ = "Asia/Jerusalem"
NOW = datetime(2026, 9, 10, 10, 0, tzinfo=ZoneInfo(TZ))  # Thursday


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ))


CASES = [
    # relative
    ("через 20 минут", at(2026, 9, 10, 10, 20)),
    ("через минуту", at(2026, 9, 10, 10, 1)),
    ("через час", at(2026, 9, 10, 11, 0)),
    ("через 2 часа", at(2026, 9, 10, 12, 0)),
    ("через полчаса", at(2026, 9, 10, 10, 30)),
    ("через 3 дня", at(2026, 9, 13, 10, 0)),
    ("через день", at(2026, 9, 11, 10, 0)),
    ("через неделю", at(2026, 9, 17, 10, 0)),
    ("через 2 недели в 15:00", at(2026, 9, 24, 15, 0)),
    ("через месяц", at(2026, 10, 10, 10, 0)),
    # day words
    ("сегодня в 18", at(2026, 9, 10, 18, 0)),
    ("сегодня вечером", at(2026, 9, 10, 19, 0)),
    ("завтра", at(2026, 9, 11, 9, 0)),
    ("завтра утром", at(2026, 9, 11, 9, 0)),
    ("завтра в 11", at(2026, 9, 11, 11, 0)),
    ("завтра в 11:30", at(2026, 9, 11, 11, 30)),
    ("завтра днём", at(2026, 9, 11, 13, 0)),
    ("послезавтра вечером", at(2026, 9, 12, 19, 0)),
    # weekdays (now is Thursday 10:00)
    ("в четверг в 11", at(2026, 9, 10, 11, 0)),      # still ahead today
    ("в четверг в 9", at(2026, 9, 17, 9, 0)),        # already passed → next week
    ("в четверг", at(2026, 9, 17, 9, 0)),            # no time → next Thursday
    ("в пятницу в 11", at(2026, 9, 11, 11, 0)),
    ("в пятницу вечером", at(2026, 9, 11, 19, 0)),
    ("в понедельник утром", at(2026, 9, 14, 9, 0)),
    ("во вторник в 7 вечера", at(2026, 9, 15, 19, 0)),
    ("в среду в 8 утра", at(2026, 9, 16, 8, 0)),
    ("в субботу", at(2026, 9, 12, 9, 0)),
    ("в воскресенье в 12", at(2026, 9, 13, 12, 0)),
    # dates
    ("25 сентября", at(2026, 9, 25, 9, 0)),
    ("25 сентября в 10", at(2026, 9, 25, 10, 0)),
    ("3 января в 10", at(2027, 1, 3, 10, 0)),         # past this year → next year
    ("25.09 в 14:30", at(2026, 9, 25, 14, 30)),
    ("01.02.2027", at(2027, 2, 1, 9, 0)),
    ("15 числа", at(2026, 9, 15, 9, 0)),
    ("5 числа в 18", at(2026, 10, 5, 18, 0)),         # the 5th already passed → next month
    # bare times
    ("в 11", at(2026, 9, 10, 11, 0)),
    ("в 11:30", at(2026, 9, 10, 11, 30)),
    ("в 7", at(2026, 9, 10, 19, 0)),                  # 07:00 passed, 19:00 ahead
    ("в 7 утра", at(2026, 9, 11, 7, 0)),              # explicit morning → tomorrow
    ("в 9 вечера", at(2026, 9, 10, 21, 0)),
    ("в 23 часа", at(2026, 9, 10, 23, 0)),
    ("в 9", at(2026, 9, 10, 21, 0)),                  # 09:00 passed, 21:00 ahead
    ("в полдень", at(2026, 9, 10, 12, 0)),
    ("вечером", at(2026, 9, 10, 19, 0)),
    ("утром", at(2026, 9, 11, 9, 0)),                 # this morning is gone
    # spelled-out numbers, as Whisper often writes them
    ("завтра в девять", at(2026, 9, 11, 9, 0)),
    ("через двадцать минут", at(2026, 9, 10, 10, 20)),
    ("через двадцать пять минут", at(2026, 9, 10, 10, 25)),
    ("в пятницу в одиннадцать тридцать", at(2026, 9, 11, 11, 0)),   # minutes as words: hour only
    ("через два часа", at(2026, 9, 10, 12, 0)),
    ("в семь вечера", at(2026, 9, 10, 19, 0)),
    # review findings I1–I4, S6, S7
    ("в 11.30", at(2026, 9, 10, 11, 30)),
    ("завтра в 11.30", at(2026, 9, 11, 11, 30)),
    ("в 25.09 в 14", at(2026, 9, 25, 14, 0)),
    ("в 5.10 в 18", at(2026, 10, 5, 18, 0)),
    ("утром в 8", at(2026, 9, 11, 8, 0)),             # 08:00 passed → tomorrow morning, not 20:00
    ("вечером в 8", at(2026, 9, 10, 20, 0)),
    ("в четверг вечером", at(2026, 9, 10, 19, 0)),    # today 19:00 is still ahead
    ("23 сентября 2027", at(2027, 9, 23, 9, 0)),
    ("23 сентября 2027 в 10", at(2027, 9, 23, 10, 0)),
    # the past is parsed, so the tools can refuse it with the actual date (FR-23)
    ("вчера в 10", at(2026, 9, 9, 10, 0)),
    ("позавчера", at(2026, 9, 8, 9, 0)),
    # inside a longer phrase, as the model may pass it
    ("напомни в четверг в 11 позвонить в банк", at(2026, 9, 10, 11, 0)),
    ("Завтра в 11 — созвон с Костей", at(2026, 9, 11, 11, 0)),
]


@pytest.mark.parametrize("phrase,expected", CASES)
def test_parse(phrase, expected):
    assert parse(phrase, now=NOW, tz=TZ) == expected


@pytest.mark.parametrize("phrase", ["", "позвонить в банк", "когда-нибудь", "в 25", "в 12:70",
                                    "31 февраля", "скоро", "через 1.5 часа", "версия 2.5",
                                    "через какое-то время"])
def test_unknown_is_none(phrase):
    assert parse(phrase, now=NOW, tz=TZ) is None


def test_naive_now_is_taken_as_utc():
    naive = datetime(2026, 9, 10, 7, 0)  # 07:00 UTC == 10:00 Jerusalem
    assert parse("через час", now=naive, tz=TZ) == at(2026, 9, 10, 11, 0)


def test_result_is_aware_in_the_users_tz():
    dt = parse("завтра в 9", now=NOW, tz="Europe/Berlin")
    assert dt.tzinfo is not None and dt.utcoffset() != NOW.utcoffset()


def test_fmt():
    assert fmt(at(2026, 9, 10, 18, 0), NOW) == "сегодня в 18:00"
    assert fmt(at(2026, 9, 11, 9, 0), NOW) == "завтра в 09:00"
    assert fmt(at(2026, 9, 17, 11, 0), NOW) == "чт 17.09 в 11:00"
    assert fmt(at(2026, 9, 17, 11, 0)) == "чт 17.09 в 11:00"


def test_relative_hours_survive_a_dst_switch():
    # Israel leaves DST on 2026-10-25 at 02:00 (clocks go back to 01:00).
    before = datetime(2026, 10, 25, 0, 30, tzinfo=ZoneInfo(TZ))
    result = parse("через 2 часа", now=before, tz=TZ)
    # Same-tzinfo subtraction is wall-clock in Python; compare in UTC.
    assert (result.astimezone(timezone.utc) - before.astimezone(timezone.utc)).total_seconds() == 7200
    assert result.hour == 1 and result.fold == 1
