from datetime import datetime, time, timedelta

import pytest

from app.ingestion.parsers import (
    FAIL,
    date_order_evidence,
    has_lao,
    is_null,
    parse_bool,
    parse_date,
    parse_duration,
    parse_number,
    parse_phone,
)


@pytest.mark.parametrize("raw,expected", [
    ("1,500", 1500), ("1,500 ກີບ", 1500), ("₭2,000", 2000), ("LAK 12,000", 12000), ("12000 kip", 12000),
    ("໑໒໓", 123), ("(1,000)", -1000), ("-45", -45), ("12.5", 12.5), ("12,5", 12.5), ("1.234.567,89", 1234567.89),
    ("1 234 567", 1234567), ("15%", 0.15), ("2.5k", 2500), ("3 ລ້ານ", 3_000_000), (42, 42), (3.25, 3.25),
    ("  7  ", 7),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["abc", "", "12-01-2025", "1,2,3", None, True, "020 5555 1234x"])
def test_parse_number_rejects(raw):
    assert parse_number(raw) is FAIL


@pytest.mark.parametrize("raw,order,expected", [
    ("2025-01-31", "dmy", datetime(2025, 1, 31)),
    ("31/01/2025", "dmy", datetime(2025, 1, 31)),
    ("01/02/2025", "dmy", datetime(2025, 2, 1)),
    ("01/02/2025", "mdy", datetime(2025, 1, 2)),
    ("13/02/2025", "mdy", datetime(2025, 2, 13)),  # value proves day-first
    ("31/01/2568", "dmy", datetime(2025, 1, 31)),  # Buddhist Era
    ("5 ມັງກອນ 2025", "dmy", datetime(2025, 1, 5)),
    ("5 ທັນວາ 2568", "dmy", datetime(2025, 12, 5)),
    ("06-Jan-2025", "dmy", datetime(2025, 1, 6)),
    ("Jan 6, 2025", "dmy", datetime(2025, 1, 6)),
    ("2025-03-04 14:05:09", "dmy", datetime(2025, 3, 4, 14, 5, 9)),
    ("04/03/2025 2:05 PM", "dmy", datetime(2025, 3, 4, 14, 5)),
    ("໓໑/໐໑/໒໐໒໕", "dmy", datetime(2025, 1, 31)),
])
def test_parse_date(raw, order, expected):
    assert parse_date(raw, order) == expected


@pytest.mark.parametrize("raw", ["32/01/2025", "2025-13-01", "hello", "12", 45000, "31/02/2025"])
def test_parse_date_rejects(raw):
    assert parse_date(raw) is FAIL


def test_date_order_evidence():
    assert date_order_evidence("31/01/2025") == "dmy"
    assert date_order_evidence("01/31/2025") == "mdy"
    assert date_order_evidence("01/02/2025") is None


@pytest.mark.parametrize("raw,expected", [
    ("00:05:30", 330), ("05:30", 330), ("1:02:03", 3723), ("5 ນາທີ 3 ວິນາທີ", 303), ("3m 20s", 200),
    ("1h 2m 3s", 3723), ("45 sec", 45), ("2 ຊົ່ວໂມງ", 7200), (time(0, 4, 5), 245), (timedelta(hours=26), 93600),
    (datetime(1900, 1, 1, 2, 0, 0), 2 * 86400 + 7200),
])
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "5", 5, "12:75", "5 apples"])
def test_parse_duration_rejects(raw):
    assert parse_duration(raw) is FAIL


def test_phone_keeps_leading_zero():
    assert parse_phone("020 5555 1234") == "02055551234"
    assert parse_phone("+856 20 5555 1234") == "+8562055551234"
    assert parse_phone("12") is FAIL


def test_bool_and_nulls():
    assert parse_bool("ແມ່ນ") is True and parse_bool("ບໍ່") is False and parse_bool("maybe") is FAIL
    assert is_null("  ") and is_null("N/A") and is_null("-") and not is_null("0")
    assert has_lao("ສະບາຍດີ") and not has_lao("hello")
