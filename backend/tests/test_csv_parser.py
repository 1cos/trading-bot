"""Tests for canonical CSV candle parser.

Parity vectors verified by running parseCandlesFromCSV in
estrategie/bdrr_strategy_runner.js via Node.js on dati/SPY_5m.csv.
"""

import math

import pytest

from trading_lab.csv_parser import parse_candles_from_csv


# ── Minimal valid CSV fixture (matches repository format) ─────────────────────

HEADER = """\
Price,Close,High,Low,Open,Volume
Ticker,SPY,SPY,SPY,SPY,SPY
Datetime,,,,,"""

ROW_1 = "2026-04-24 09:30:00-04:00,709.8350219726562,711.1599731445312,709.760009765625,710.75,3339728"
ROW_2 = "2026-04-24 09:35:00-04:00,710.3099975585938,710.4199829101562,709.5499877929688,709.8400268554688,585429"

VALID_CSV = HEADER + "\n" + ROW_1 + "\n" + ROW_2


# ═══════════════════════════════════════════════════════════════════════════════
# Normal parsing
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalParsing:
    def test_two_rows(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert len(candles) == 2

    def test_output_keys(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert set(candles[0].keys()) == {"time_ms", "open", "high", "low", "close"}

    def test_no_volume_key(self):
        """JS parser ignores volume column."""
        candles = parse_candles_from_csv(VALID_CSV)
        assert "volume" not in candles[0]


class TestParity:
    """Exact parity with JS parseCandlesFromCSV on SPY_5m.csv row 0."""

    def test_time_ms(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[0]["time_ms"] == 1777037400000

    def test_open(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[0]["open"] == 710.75

    def test_high(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[0]["high"] == 711.1599731445312

    def test_low(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[0]["low"] == 709.760009765625

    def test_close(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[0]["close"] == 709.8350219726562

    def test_second_row_time(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[1]["time_ms"] == 1777037700000

    def test_second_row_open(self):
        candles = parse_candles_from_csv(VALID_CSV)
        assert candles[1]["open"] == 709.8400268554688


class TestTimestamp:
    """Timestamp parsing: space→T, ISO 8601 with offset → epoch ms."""

    def test_edt_offset(self):
        """EDT (-04:00) correctly converted to UTC."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,525.00,526.00,524.00,525.50,100"
        candles = parse_candles_from_csv(csv)
        # 09:30 EDT = 13:30 UTC
        assert candles[0]["time_ms"] == 1779802200000

    def test_est_offset(self):
        """EST (-05:00) correctly converted."""
        csv = HEADER + "\n2026-01-05 09:30:00-05:00,400.00,401.00,399.00,400.50,100"
        candles = parse_candles_from_csv(csv)
        # 09:30 EST = 14:30 UTC
        assert candles[0]["time_ms"] == 1767623400000

    def test_utc_offset(self):
        """UTC (+00:00)."""
        csv = HEADER + "\n2026-05-26 13:30:00+00:00,525.00,526.00,524.00,525.50,100"
        candles = parse_candles_from_csv(csv)
        assert candles[0]["time_ms"] == 1779802200000


class TestRowOrder:
    """Row order preserved exactly — no sorting."""

    def test_order_preserved(self):
        csv = HEADER + "\n" + ROW_2 + "\n" + ROW_1
        candles = parse_candles_from_csv(csv)
        # ROW_2 comes first (later timestamp) since we reversed order
        assert candles[0]["time_ms"] == 1777037700000
        assert candles[1]["time_ms"] == 1777037400000

    def test_duplicate_timestamps(self):
        """Duplicates not removed — JS doesn't deduplicate."""
        csv = HEADER + "\n" + ROW_1 + "\n" + ROW_1
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 2
        assert candles[0]["time_ms"] == candles[1]["time_ms"]


# ═══════════════════════════════════════════════════════════════════════════════
# Header skip (lines 0–2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHeaderSkip:
    def test_header_only(self):
        candles = parse_candles_from_csv(HEADER)
        assert candles == []

    def test_three_header_lines_only(self):
        csv = "A\nB\nC"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_fewer_than_three_lines(self):
        candles = parse_candles_from_csv("A\nB")
        assert candles == []

    def test_header_content_irrelevant(self):
        """Headers are skipped by position, not by content."""
        csv = "X\nY\nZ\n2026-05-26 09:30:00-04:00,100.0,101.0,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Blank lines
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlankLines:
    def test_blank_line_between_data(self):
        csv = HEADER + "\n" + ROW_1 + "\n\n" + ROW_2
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 2

    def test_trailing_blank_lines(self):
        csv = VALID_CSV + "\n\n\n"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 2

    def test_whitespace_only_line(self):
        csv = HEADER + "\n" + ROW_1 + "\n   \n" + ROW_2
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 2


class TestEmptyInput:
    def test_empty_string(self):
        candles = parse_candles_from_csv("")
        assert candles == []

    def test_whitespace_only(self):
        candles = parse_candles_from_csv("   \n  \n  ")
        assert candles == []


# ═══════════════════════════════════════════════════════════════════════════════
# Malformed rows
# ═══════════════════════════════════════════════════════════════════════════════


class TestMalformedRows:
    def test_fewer_than_5_columns(self):
        """Rows with < 5 columns are skipped."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,101.0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_four_columns(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,101.0,99.0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_extra_columns_accepted(self):
        """Extra columns beyond 5 are ignored (volume is col 5)."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,101.0,99.0,100.5,9999,extra"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1

    def test_nan_close_skipped(self):
        """NaN close triggers skip."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,NaN,101.0,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_non_numeric_close_skipped(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,abc,101.0,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_invalid_timestamp_skipped(self):
        csv = HEADER + "\nnot-a-date,100.0,101.0,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_non_numeric_high_skipped(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,abc,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_non_numeric_low_skipped(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,101.0,abc,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_non_numeric_open_skipped(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,100.0,101.0,99.0,abc,0"
        candles = parse_candles_from_csv(csv)
        assert candles == []

    def test_valid_after_malformed(self):
        """Valid rows after malformed ones are still parsed."""
        csv = HEADER + "\nbad,row\n" + ROW_1
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Whitespace handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestWhitespace:
    def test_leading_trailing_whitespace_on_lines(self):
        csv = HEADER + "\n  " + ROW_1 + "  "
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1

    def test_timestamp_column_whitespace(self):
        """JS trims cols[0]."""
        csv = HEADER + "\n  2026-05-26 09:30:00-04:00  ,100.0,101.0,99.0,100.5,0"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Input type validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestInputType:
    def test_none_rejected(self):
        with pytest.raises(TypeError, match="must be a str"):
            parse_candles_from_csv(None)

    def test_int_rejected(self):
        with pytest.raises(TypeError, match="must be a str"):
            parse_candles_from_csv(123)

    def test_bytes_rejected(self):
        with pytest.raises(TypeError, match="must be a str"):
            parse_candles_from_csv(b"data")


# ═══════════════════════════════════════════════════════════════════════════════
# Deterministic parsing
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeterministic:
    def test_repeated_parsing(self):
        results = [parse_candles_from_csv(VALID_CSV) for _ in range(10)]
        assert all(r == results[0] for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Negative and special prices
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecialPrices:
    def test_negative_price(self):
        """Negative prices are accepted (JS parseFloat handles them)."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,-5.0,1.0,-10.0,-3.0,0"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1
        assert candles[0]["close"] == -5.0
        assert candles[0]["low"] == -10.0

    def test_zero_price(self):
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,0.0,1.0,0.0,0.5,0"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1
        assert candles[0]["close"] == 0.0

    def test_infinity_close_skipped(self):
        """JS: parseFloat('Infinity') = Infinity, isNaN(Infinity) = false.
        So Infinity would NOT be skipped in JS. But this is an edge case
        that never appears in real CSV data.  Python float('inf') also
        passes the isnan check, so behavior matches JS."""
        csv = HEADER + "\n2026-05-26 09:30:00-04:00,Infinity,1.0,0.0,0.5,0"
        candles = parse_candles_from_csv(csv)
        assert len(candles) == 1
        assert math.isinf(candles[0]["close"])


# ═══════════════════════════════════════════════════════════════════════════════
# Real CSV parity (from dati/SPY_5m.csv)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealCSVParity:
    """Parity against JS parseCandlesFromCSV on dati/SPY_5m.csv.

    Expected values obtained by running:
      node -e "const {parseCandlesFromCSV}=require('./estrategie/bdrr_strategy_runner.js');
               const csv=require('fs').readFileSync('dati/SPY_5m.csv','utf8');
               console.log(parseCandlesFromCSV(csv).length);"
    Result: 4680 candles.
    """

    @pytest.fixture()
    def spy_candles(self):
        import os
        csv_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "dati", "SPY_5m.csv"
        )
        if not os.path.exists(csv_path):
            pytest.skip("SPY_5m.csv not available")
        with open(csv_path) as f:
            return parse_candles_from_csv(f.read())

    def test_count(self, spy_candles):
        assert len(spy_candles) == 4680

    def test_first_candle(self, spy_candles):
        c = spy_candles[0]
        assert c["time_ms"] == 1777037400000
        assert c["open"] == 710.75
        assert c["high"] == 711.1599731445312
        assert c["low"] == 709.760009765625
        assert c["close"] == 709.8350219726562

    def test_last_candle(self, spy_candles):
        c = spy_candles[-1]
        assert c["time_ms"] == 1784663700000
        assert c["open"] == 748.3800048828125
        assert c["high"] == 748.5800170898438
        assert c["low"] == 748.0900268554688
        assert c["close"] == 748.3300170898438
