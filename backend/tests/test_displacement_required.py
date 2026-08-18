"""Tests for displacement_required propagation — no None in trace.

Root cause: engine_config has min_displacement_bars=None (OPEN param).
dict.get("min_displacement_bars", 3) returns None because key EXISTS.
Fix: use `or 3` fallback.
"""

import pytest
from trading_lab.live.signal_detector import LiveSignalDetector, SignalStatus


class TestDisplacementRequiredPropagation:
    """Verify displacement_required is never None in stage_context."""

    def _make_detector(self, **kwargs):
        defaults = {"symbol": "QQQ", "direction": "SHORT", "tick_size": 0.01}
        defaults.update(kwargs)
        return LiveSignalDetector(**defaults)

    def test_default_config_produces_3(self):
        """Default config (min_displacement_bars=None) → required=3."""
        sd = self._make_detector()
        cfg = sd._engine_config
        # Config has None
        assert cfg["min_displacement_bars"] is None
        # But min_req in evaluate should resolve to 3
        min_req = cfg.get("min_displacement_bars") or 3
        assert min_req == 3

    def test_explicit_config_2(self):
        """Explicit min_displacement_bars=2 → required=2."""
        sd = self._make_detector()
        sd._engine_config["min_displacement_bars"] = 2
        min_req = sd._engine_config.get("min_displacement_bars") or 3
        assert min_req == 2

    def test_stage_context_never_none(self):
        """After evaluate, displacement_required in context is never None."""
        from trading_lab.live.session_builder_live import LiveSessionBuilder

        sd = self._make_detector()

        # Build a session with ORB + break but no displacement
        MS = 1786455000000
        candles = []
        for i in range(5):
            candles.append({"time_ms": MS + i*60000, "open": 100.0,
                            "high": 101.0, "low": 99.0, "close": 100.0,
                            "volume": 1000})
        # Break below ORB low
        candles.append({"time_ms": MS + 5*60000, "open": 99.0,
                        "high": 99.3, "low": 98.0, "close": 98.5,
                        "volume": 1500})
        # One displacement bar (not enough)
        candles.append({"time_ms": MS + 6*60000, "open": 98.5,
                        "high": 98.8, "low": 98.0, "close": 98.3,
                        "volume": 1200})

        builder = LiveSessionBuilder("QQQ")
        for c in candles:
            builder.add_bar(c)
        session = builder.current_session()

        result = sd.evaluate(session)
        ctx = result.stage_context or {}

        # displacement_required must be an int, never None
        assert ctx.get("displacement_required") is not None
        assert ctx["displacement_required"] == 3
        assert isinstance(ctx["displacement_required"], int)

    def test_trace_format_shows_number(self):
        """Decision trace shows 1/3, not 1/None."""
        from trading_lab.live.decision_trace import build_candle_trace

        candle = {"time_ms": 1000, "open": 98.5, "high": 98.8,
                  "low": 98.0, "close": 98.3, "volume": 1200}
        from trading_lab.live.signal_detector import SignalResult
        result = SignalResult(
            status=SignalStatus.NO_SETUP,
            failed_stage="DISPLACEMENT_TOO_SHORT",
            pipeline_stage="DISP BUILDING",
            stage_context={
                "orb_high": 101.0, "orb_low": 99.0,
                "break_bar_index": 5, "direction": "SHORT",
                "displacement_bars": 1, "displacement_required": 3,
            },
        )
        trace = build_candle_trace(candle, result, 101.0, 99.0, "QQQ", "09:36")
        assert trace.displacement_required == 3
        assert "None" not in trace.candle_event
        assert trace.candle_event == "DISPLACEMENT_1_OF_3"
