"""Tests for PD_AUDIT telemetry (audit observability, no logic change).

Covers exactly what the "Add PDH/PDL audit telemetry only" task asks:

    A -- The audit record carries every field the task listed
         (symbol, timestamp, level_source, level_price, current_price,
         direction, eligible, failed_reason, pipeline_stage,
         current_state, setup_key) and renders in the specified
         PD_AUDIT: key=value form.
    B -- The events actually reach the normal maxbot session log
         (SessionEventLog) during live detection, one per evaluated
         direction, and survive JSON export.
    C -- Nothing about detection or trading changed: detector results
         are byte-identical with the telemetry in place, PD_AUDIT is
         not a trade event, and a telemetry failure cannot break the
         bar callback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
from datetime import datetime as dt_cls

from trading_lab.live.bot_runner import MaxBotRunner
from trading_lab.live.context_levels import ContextLevels
from trading_lab.live.event_stream import EventType
from trading_lab.live.pd_audit import (
    build_pd_audit_record,
    format_pd_audit_line,
    normalize_stage,
    pd_audit_state_key,
)
from trading_lab.live.session_builder_live import LiveSessionBuilder
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


_ET = ZoneInfo("America/New_York")
_BASE = int(dt_cls(2026, 8, 11, 9, 30, 0, tzinfo=_ET).timestamp() * 1000)

# Required-field contract straight from the task description.
_REQUIRED_FIELDS = (
    "symbol", "bar_time_ms", "level_source", "level_price",
    "current_price", "direction", "eligible",
)


def _ms(offset_min: int) -> int:
    return _BASE + offset_min * 60_000


def _c(offset_min, o, h, l, cl):
    return {"time_ms": _ms(offset_min), "open": o, "high": h, "low": l,
            "close": cl, "volume": 1000}


def _bars():
    """Ten plain bars — enough to build a session, no BDRR required."""
    return [
        _c(0, 100.00, 101.00, 99.00, 100.50),
        _c(1, 100.50, 100.80, 100.00, 100.30),
        _c(2, 100.30, 100.70, 99.80, 100.40),
        _c(3, 100.40, 100.90, 100.10, 100.60),
        _c(4, 100.60, 100.95, 100.20, 100.70),
        _c(5, 100.80, 101.60, 100.70, 101.50),
        _c(6, 101.55, 101.80, 101.20, 101.60),
        _c(7, 101.60, 101.90, 101.30, 101.70),
        _c(8, 101.70, 101.85, 101.10, 101.40),
        _c(9, 101.10, 101.30, 100.80, 101.20),
    ]


def _prev_sessions():
    return [{
        "date": "2026-08-10",
        "candles": [{"time_ms": 1, "open": 100.0, "high": 103.00, "low": 95.0,
                     "close": 100.5, "volume": 500}],
    }]


def _mock_ib():
    ib = MagicMock()
    ib.managedAccounts.return_value = ["DU123"]
    return ib


def _trading_relevant(r):
    return (r.status, r.direction, r.entry_price, r.stop_price, r.target_price,
            r.entry_timestamp_ms, r.setup_key, r.signal_key)


def _runner(direction="BOTH", symbol="QQQ", mode="OBSERVE_ONLY"):
    """A runner with one symbol wired up and fed real bars."""
    runner = MaxBotRunner(symbol, direction, execution_mode=mode)
    runner._ib = _mock_ib()
    runner._verify_paper()
    runner._setup_all_symbols()

    rt = runner._runtimes[symbol]
    for b in _bars():
        rt.session_builder.add_bar(b)
    rt.previous_sessions = _prev_sessions()
    rt.context_levels = ContextLevels(
        symbol=symbol, pdh=103.00, pdl=95.00, prev_date="2026-08-10",
    )
    return runner, rt


def _pd_events(runner):
    return [e for e in runner.session_log.events
            if e.event_type == EventType.PD_AUDIT]


class _FakeSignalResult:
    """Stands in for a SignalResult — build_pd_audit_record only ever
    getattr()s these fields, never calls into the detector."""

    def __init__(self, status=None, pipeline_stage=None, failed_stage=None,
                 setup_key=None):
        self.status = status
        self.pipeline_stage = pipeline_stage
        self.failed_stage = failed_stage
        self.setup_key = setup_key


# ═════════════════════════════════════════════════════════════════════════
# A -- record shape and line format
# ═════════════════════════════════════════════════════════════════════════

class TestAuditRecord:
    def test_eligible_record_has_every_required_field(self):
        record = build_pd_audit_record(
            symbol="MSFT", direction="LONG",
            level_source="PREVIOUS_DAY_HIGH", level_price=486.36,
            current_price=486.80, bar_time_ms=_ms(3),
            eligibility={"eligible": True,
                         "reason": "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"},
            signal_result=_FakeSignalResult(
                status=SignalStatus.NO_SETUP,
                pipeline_stage="WAITING FOR RETEST",
                failed_stage="RETEST_NOT_FOUND",
            ),
            current_state="WAITING_FOR_SIGNAL",
        )

        for field in _REQUIRED_FIELDS:
            assert field in record, f"missing required audit field: {field}"

        assert record["eligible"] is True
        assert record["pipeline_stage"] == "WAITING_FOR_RETEST"
        # PD detector's own stage — not the ORB eligibility precondition.
        assert record["failed_stage"] == "RETEST_NOT_FOUND"
        assert "eligibility_failed_stage" not in record
        assert record["current_state"] == "WAITING_FOR_SIGNAL"
        # A success label is not a failure reason.
        assert "failed_reason" not in record

    def test_ineligible_record_carries_failed_reason_and_no_stage(self):
        record = build_pd_audit_record(
            symbol="AMD", direction="SHORT",
            level_source="PREVIOUS_DAY_LOW", level_price=462.11,
            current_price=458.81, bar_time_ms=_ms(3),
            eligibility={"eligible": False, "reason": "NO_ORB_BREAK"},
            signal_result=None,      # evaluator builds no detector here
            current_state="WAITING_FOR_SIGNAL",
        )

        assert record["eligible"] is False
        assert record["failed_reason"] == "NO_ORB_BREAK"
        # Not eligible => no PD detector ran => no stage, no setup_key.
        assert "pipeline_stage" not in record
        assert "setup_key" not in record
        assert "failed_stage" not in record

    def test_setup_key_is_captured_when_present(self):
        record = build_pd_audit_record(
            symbol="MSFT", direction="LONG",
            level_source="PREVIOUS_DAY_HIGH", level_price=486.36,
            current_price=487.10, bar_time_ms=_ms(4),
            eligibility={"eligible": True, "reason": "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"},
            signal_result=_FakeSignalResult(
                status=SignalStatus.SIGNAL, pipeline_stage="SIGNAL",
                setup_key="LONG:PREVIOUS_DAY_HIGH:1787579460000",
            ),
            current_state="WAITING_FOR_SIGNAL",
        )
        assert record["setup_key"] == "LONG:PREVIOUS_DAY_HIGH:1787579460000"
        assert record["signal_status"] == str(SignalStatus.SIGNAL)

    def test_normalize_stage(self):
        assert normalize_stage("WAITING FOR RETEST") == "WAITING_FOR_RETEST"
        assert normalize_stage("SIGNAL") == "SIGNAL"
        assert normalize_stage(None) is None


class TestLineFormat:
    def test_eligible_line_matches_specified_format(self):
        record = build_pd_audit_record(
            symbol="MSFT", direction="LONG",
            level_source="PREVIOUS_DAY_HIGH", level_price=486.36,
            current_price=486.80, bar_time_ms=_ms(3),
            eligibility={"eligible": True, "reason": "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"},
            signal_result=_FakeSignalResult(pipeline_stage="WAITING FOR RETEST"),
            current_state="WAITING_FOR_SIGNAL",
        )
        line = format_pd_audit_line(record)

        assert line.startswith("PD_AUDIT: ")
        assert "symbol=MSFT" in line
        assert "level=PREVIOUS_DAY_HIGH" in line
        assert "price=486.36" in line
        assert "current=486.8" in line
        assert "eligible=true" in line
        assert "stage=WAITING_FOR_RETEST" in line
        assert "state=WAITING_FOR_SIGNAL" in line
        assert "\n" not in line          # one grep-able line per record

    def test_ineligible_line_matches_specified_format(self):
        record = build_pd_audit_record(
            symbol="AMD", direction="SHORT",
            level_source="PREVIOUS_DAY_LOW", level_price=462.11,
            current_price=458.81, bar_time_ms=_ms(3),
            eligibility={"eligible": False, "reason": "NO_ORB_BREAK"},
            signal_result=None, current_state="WAITING_FOR_SIGNAL",
        )
        line = format_pd_audit_line(record)

        assert "symbol=AMD" in line
        assert "level=PREVIOUS_DAY_LOW" in line
        assert "eligible=false" in line
        assert "reason=NO_ORB_BREAK" in line
        assert "stage=" not in line


class TestStateKey:
    def test_price_and_time_do_not_count_as_a_state_change(self):
        def make(price, ts):
            return build_pd_audit_record(
                symbol="AMD", direction="SHORT",
                level_source="PREVIOUS_DAY_LOW", level_price=462.11,
                current_price=price, bar_time_ms=ts,
                eligibility={"eligible": False, "reason": "NO_ORB_BREAK"},
                signal_result=None, current_state="WAITING_FOR_SIGNAL",
            )
        assert pd_audit_state_key(make(458.81, _ms(3))) == \
               pd_audit_state_key(make(461.02, _ms(4)))

    def test_reason_change_is_a_state_change(self):
        def make(reason):
            return build_pd_audit_record(
                symbol="AMD", direction="SHORT",
                level_source="PREVIOUS_DAY_LOW", level_price=462.11,
                current_price=458.81, bar_time_ms=_ms(3),
                eligibility={"eligible": False, "reason": reason},
                signal_result=None, current_state="WAITING_FOR_SIGNAL",
            )
        assert pd_audit_state_key(make("NO_ORB_BREAK")) != \
               pd_audit_state_key(make("DISPLACEMENT_INCOMPLETE"))


# ═════════════════════════════════════════════════════════════════════════
# B -- the events reach the normal maxbot log
# ═════════════════════════════════════════════════════════════════════════

class TestEmissionIntoSessionLog:
    def test_pd_audit_emitted_during_live_detection(self):
        runner, rt = _runner(direction="BOTH")
        assert _pd_events(runner) == []

        runner._update_pdh_pdl_candidate(rt)

        events = _pd_events(runner)
        assert len(events) == 2, "one record per evaluated direction"
        assert {e.direction for e in events} == {"LONG", "SHORT"}
        assert {e.data["level_source"] for e in events} == {
            "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW"}

    def test_emitted_payload_has_required_fields_and_real_values(self):
        runner, rt = _runner(direction="LONG")
        runner._update_pdh_pdl_candidate(rt)

        (event,) = _pd_events(runner)
        for field in _REQUIRED_FIELDS:
            assert field in event.data, f"missing required audit field: {field}"

        assert event.symbol == "QQQ"
        assert event.data["direction"] == "LONG"
        assert event.data["level_source"] == "PREVIOUS_DAY_HIGH"
        assert event.data["level_price"] == 103.00        # from context PDH
        assert event.data["current_price"] == 101.20      # last bar close
        assert event.data["bar_time_ms"] == _ms(9)
        assert event.data["current_state"] == "WAITING_FOR_SIGNAL"

    def test_not_eligible_evaluation_records_its_reason(self):
        runner, rt = _runner(direction="LONG")
        rt.previous_sessions = None      # forces a deterministic rejection

        runner._update_pdh_pdl_candidate(rt)

        (event,) = _pd_events(runner)
        assert event.data["eligible"] is False
        assert event.data["failed_reason"] == "NO_PREVIOUS_SESSIONS"

    def test_repeated_identical_evaluations_emit_once_and_stay_counted(self):
        runner, rt = _runner(direction="LONG")

        for _ in range(5):
            runner._update_pdh_pdl_candidate(rt)

        events = _pd_events(runner)
        assert len(events) == 1, "unchanged audit state must not spam the log"
        # ...and the 4 suppressed evaluations are still accounted for on
        # the next emitted record, so no evaluation is invisible.
        rt.context_levels = ContextLevels(
            symbol="QQQ", pdh=999.0, pdl=95.0, prev_date="2026-08-10")
        runner._update_pdh_pdl_candidate(rt)

        events = _pd_events(runner)
        assert len(events) == 2
        assert events[1].data["evaluations_since_last_emit"] == 5

    def test_pd_audit_survives_json_export(self, tmp_path):
        runner, rt = _runner(direction="LONG")
        runner._update_pdh_pdl_candidate(rt)

        out = runner.session_log.export_json(tmp_path / "s.json")
        payload = json.loads(out.read_text())

        pd_events = [e for e in payload["events"]
                     if e["event_type"] == "PD_AUDIT"]
        assert len(pd_events) == 1
        assert pd_events[0]["data"]["level_source"] == "PREVIOUS_DAY_HIGH"


# ═════════════════════════════════════════════════════════════════════════
# C -- no detector / trade behavior change
# ═════════════════════════════════════════════════════════════════════════

class TestNoBehaviorChange:
    def test_detector_result_identical_with_telemetry_running(self):
        runner, rt = _runner(direction="LONG")
        session = rt.session_builder.current_session()

        before = rt.signal_detector.evaluate(session)
        runner._update_pdh_pdl_candidate(rt)      # emits PD_AUDIT
        after = rt.signal_detector.evaluate(session)

        plain = LiveSignalDetector(
            symbol="QQQ", direction="LONG", tick_size=runner._tick_size,
            market_timezone=runner._tz_str, session_open=runner._session_open,
        )
        plain.set_previous_sessions(rt.previous_sessions)

        assert _trading_relevant(after) == _trading_relevant(before)
        assert _trading_relevant(after) == _trading_relevant(plain.evaluate(session))

    def test_pd_audit_is_not_a_trade_event(self):
        runner, rt = _runner(direction="BOTH")
        runner._update_pdh_pdl_candidate(rt)

        assert _pd_events(runner), "sanity: audit events were emitted"
        assert runner.session_log.trade_events == [], \
            "PD_AUDIT must never enter the trade lifecycle stream"

    def test_lifecycle_untouched_by_telemetry(self):
        runner, rt = _runner(direction="LONG")
        before = rt.orchestrator.lifecycle

        runner._update_pdh_pdl_candidate(rt)

        assert rt.orchestrator.lifecycle == before
        assert rt.orchestrator.has_pending_signal is False

    def test_telemetry_failure_cannot_break_the_bar_callback(self, monkeypatch):
        runner, rt = _runner(direction="LONG")

        def _boom(*a, **k):
            raise RuntimeError("telemetry exploded")

        monkeypatch.setattr(runner, "_emit_pd_audit", _boom)

        runner._update_pdh_pdl_candidate(rt)      # must not raise

        assert _pd_events(runner) == []
        # The observational candidate itself is still produced.
        assert rt.pdh_pdl_candidate is not None
        assert "LONG" in rt.pdh_pdl_candidate

    def test_telemetry_does_not_re_evaluate_the_level(self, monkeypatch):
        """The audit must report the evaluation that already ran, not
        trigger a second one (cost + divergence risk)."""
        import trading_lab.live.bot_runner as br

        calls = []
        original = br.evaluate_pdh_pdl_candidate

        def _counting(*a, **k):
            calls.append(k.get("direction"))
            return original(*a, **k)

        monkeypatch.setattr(br, "evaluate_pdh_pdl_candidate", _counting)

        runner, rt = _runner(direction="BOTH")
        runner._update_pdh_pdl_candidate(rt)

        assert calls == ["LONG", "SHORT"], \
            "exactly one evaluation per direction, telemetry adds none"
        assert len(_pd_events(runner)) == 2


class TestStageDisambiguation:
    """An audit record must never leave "which pipeline stopped?"
    ambiguous: the ORB precondition and the PD detector are separate
    stages and get separate keys."""

    def test_orb_precondition_stage_is_kept_separate(self):
        record = build_pd_audit_record(
            symbol="AMD", direction="SHORT",
            level_source="PREVIOUS_DAY_LOW", level_price=462.11,
            current_price=458.81, bar_time_ms=_ms(3),
            eligibility={"eligible": False, "reason": "NO_ORB_BREAK",
                         "failed_stage": "BREAK_NOT_FOUND"},
            signal_result=None, current_state="WAITING_FOR_SIGNAL",
        )
        assert record["eligibility_failed_stage"] == "BREAK_NOT_FOUND"
        assert "failed_stage" not in record

    def test_pd_detector_stage_is_kept_separate(self):
        record = build_pd_audit_record(
            symbol="AMD", direction="SHORT",
            level_source="PREVIOUS_DAY_LOW", level_price=462.11,
            current_price=458.81, bar_time_ms=_ms(3),
            eligibility={"eligible": True,
                         "reason": "ORB_BREAK_AND_DISPLACEMENT_COMPLETE"},
            signal_result=_FakeSignalResult(
                pipeline_stage="WAITING FOR RETEST",
                failed_stage="RETEST_NOT_FOUND"),
            current_state="WAITING_FOR_SIGNAL",
        )
        assert record["failed_stage"] == "RETEST_NOT_FOUND"
        assert "eligibility_failed_stage" not in record
