import pytest

from ingestion.market_reference.cboe_vix_daily_v001 import parse_csv


def test_invalid_legacy_row_outside_window_is_ignored():
    raw = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        # Invalid high, deliberately outside research window.
        "02/11/1992,20,10,15,18\n"
        # Valid research-window row.
        "01/04/2016,22,24,20,23\n"
    ).encode()

    rows = parse_csv(
        raw,
        start="2016-01-01",
        end_exclusive="2017-01-01",
    )

    assert len(rows) == 1
    assert rows[0]["trading_day"] == "2016-01-04"


def test_invalid_row_inside_window_still_fails():
    raw = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "01/04/2016,22,10,20,23\n"
    ).encode()

    with pytest.raises(ValueError, match="invalid VIX high on 2016-01-04"):
        parse_csv(
            raw,
            start="2016-01-01",
            end_exclusive="2017-01-01",
        )


def test_end_is_exclusive():
    raw = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "12/30/2016,12,14,11,13\n"
        "01/03/2017,13,15,12,14\n"
    ).encode()

    rows = parse_csv(
        raw,
        start="2016-01-01",
        end_exclusive="2017-01-01",
    )
    assert [x["trading_day"] for x in rows] == ["2016-12-30"]


def test_unbounded_parser_remains_strict():
    raw = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "02/11/1992,20,10,15,18\n"
    ).encode()

    with pytest.raises(ValueError, match="invalid VIX high on 1992-02-11"):
        parse_csv(raw)
