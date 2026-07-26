"""Tests for canonical SessionMetadata contract type."""

import pytest

from trading_lab.contracts.session_metadata import SessionMetadata


# Epoch ms for 2026-05-26 09:30 ET / 13:30 UTC
OPEN_MS = 1748264400000
# Epoch ms for 2026-05-26 16:00 ET / 20:00 UTC
CLOSE_MS = 1748287800000


def make_session(**overrides):
    defaults = dict(
        symbol="SPY",
        date="2026-05-26",
        market_timezone="America/New_York",
        session_open_utc_ms=OPEN_MS,
        session_close_utc_ms=CLOSE_MS,
        timeframe_seconds=300,
    )
    defaults.update(overrides)
    return SessionMetadata(**defaults)


class TestSessionMetadataConstruction:
    def test_valid(self):
        s = make_session()
        assert s.symbol == "SPY"
        assert s.date == "2026-05-26"
        assert s.market_timezone == "America/New_York"
        assert s.session_open_utc_ms == OPEN_MS
        assert s.session_close_utc_ms == CLOSE_MS
        assert s.timeframe_seconds == 300


class TestSessionMetadataImmutability:
    def test_cannot_set_symbol(self):
        s = make_session()
        with pytest.raises(AttributeError):
            s.symbol = "QQQ"  # type: ignore[misc]

    def test_cannot_set_date(self):
        s = make_session()
        with pytest.raises(AttributeError):
            s.date = "2026-01-01"  # type: ignore[misc]


class TestSessionMetadataSerialization:
    def test_shape(self):
        s = make_session()
        d = s.to_dict()
        assert d == {
            "symbol": "SPY",
            "date": "2026-05-26",
            "market_timezone": "America/New_York",
            "session_open_utc_ms": OPEN_MS,
            "session_close_utc_ms": CLOSE_MS,
            "timeframe_seconds": 300,
        }

    def test_keys_exact(self):
        s = make_session()
        assert set(s.to_dict().keys()) == {
            "symbol", "date", "market_timezone",
            "session_open_utc_ms", "session_close_utc_ms", "timeframe_seconds",
        }


class TestSessionMetadataEquality:
    def test_equal(self):
        a = make_session()
        b = make_session()
        assert a == b

    def test_not_equal(self):
        a = make_session(symbol="SPY")
        b = make_session(symbol="QQQ")
        assert a != b

    def test_hash_equal(self):
        a = make_session()
        b = make_session()
        assert hash(a) == hash(b)


class TestSessionMetadataInvalidSymbol:
    def test_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_session(symbol=None)

    def test_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_session(symbol="")

    def test_int(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_session(symbol=123)


class TestSessionMetadataInvalidDate:
    def test_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_session(date=None)

    def test_wrong_format(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            make_session(date="05-26-2026")

    def test_partial(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            make_session(date="2026-05")


class TestSessionMetadataInvalidTimezone:
    def test_none(self):
        with pytest.raises(TypeError, match="must be a str"):
            make_session(market_timezone=None)

    def test_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            make_session(market_timezone="")


class TestSessionMetadataInvalidTimestamps:
    def test_open_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_session(session_open_utc_ms=1.5)

    def test_open_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_session(session_open_utc_ms=True)

    def test_close_none(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_session(session_close_utc_ms=None)

    def test_close_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_session(session_close_utc_ms=False)


class TestSessionMetadataInvalidTimeframe:
    def test_zero(self):
        with pytest.raises(ValueError, match="must be > 0"):
            make_session(timeframe_seconds=0)

    def test_negative(self):
        with pytest.raises(ValueError, match="must be > 0"):
            make_session(timeframe_seconds=-1)

    def test_float(self):
        with pytest.raises(TypeError, match="must be an int"):
            make_session(timeframe_seconds=300.0)

    def test_bool(self):
        with pytest.raises(TypeError, match="got bool"):
            make_session(timeframe_seconds=True)


class TestSessionMetadataPackageExport:
    def test_import(self):
        from trading_lab.contracts import SessionMetadata as SM
        assert SM is SessionMetadata
