"""Live signal detector — incremental BDRR signal evaluation for MaxBot v0.1.

Thin wrapper around the existing strategy pipeline that answers:

    "Based on candles available right now, has a valid entry setup formed?"

Reuses the canonical detection stages (1a–5) and trade plan builder.
Does NOT call ``evaluate_trade_outcome`` — the broker handles fills/stops
in live trading.

Pipeline reused:
    build_session_context  → Stage 1a
    build_level            → Stage 1b (ORB construction)
    find_break             → Stage 2
    find_displacement      → Stage 3
    validate_sequence      → Stage 3b
    find_retest_window     → Stage 4
    find_rejection         → Stage 5 (entry candle)
    build_detection_result → DetectionResult/v1
    build_trade_plan       → TradePlan/v1 (entry/stop/target)

No strategy logic is duplicated.  No lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from trading_lab.atr import atr_series
from trading_lab.break_finder import find_break
from trading_lab.detection_result_builder import build_detection_result
from trading_lab.displacement_finder import find_displacement
from trading_lab.level_provider import build_level
from trading_lab.rejection_finder import find_rejection
from trading_lab.retest_window import find_retest_window
from trading_lab.sequence_validator import validate_sequence
from trading_lab.session_context import build_session_context
from trading_lab.timeframe_aggregation import timeframe_to_seconds
from trading_lab.trade_plan_builder import build_trade_plan


# ── Signal status ────────────────────────────────────────────────────────────

@unique
class SignalStatus(StrEnum):
    NO_SETUP = "NO_SETUP"
    SIGNAL = "SIGNAL"


# ── Signal result ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SignalResult:
    """Immutable result of a live signal evaluation.

    Attributes
    ----------
    status : SignalStatus
        NO_SETUP if no valid entry exists yet.
        SIGNAL if a valid entry setup has formed.
    direction : str or None
        "LONG" or "SHORT" when status is SIGNAL.
    entry_price : Decimal or None
        Entry price from trade plan.
    stop_price : Decimal or None
        Stop price from trade plan.
    target_price : Decimal or None
        2R target price from trade plan.
    entry_timestamp_ms : int or None
        time_ms of the confirmation/entry candle.
    detection_result : object or None
        Full DetectionResult/v1 for provenance.
    trade_plan : object or None
        Full TradePlan/v1.
    failed_stage : str or None
        Which pipeline stage failed (when NO_SETUP).
    """

    status: SignalStatus
    direction: str | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    entry_timestamp_ms: int | None = None
    detection_result: object | None = None
    trade_plan: object | None = None
    failed_stage: str | None = None
    pipeline_stage: str | None = None   # human-readable stage label
    stage_context: dict | None = None   # key data from reached stages
    setup_key: str | None = None        # structural identity: "direction:level_source:break_time_ms"
    signal_key: str | None = None       # exact signal identity: "setup_key:entry_candle_time_ms"
    rejection_detail: dict | None = None  # raw rejection finder result (for trace)


# ── Stage label mapping ──────────────────────────────────────────────────────

_STAGE_LABELS = {
    "NO_SESSION": "NO SESSION",
    "NO_CANDLES": "NO CANDLES",
    "INVALID_SESSION_INPUT": "SESSION ERROR",
    "LEVEL_NOT_FOUND": "BUILDING ORB",
    "UNSUPPORTED_CONFIGURATION": "CONFIG ERROR",
    "INVALID_INPUT": "INPUT ERROR",
    "BREAK_NOT_FOUND": "WAITING FOR BREAK",
    "DISPLACEMENT_TOO_SHORT": "DISP BUILDING",
    "RETEST_NOT_FOUND": "WAITING FOR RETEST",
    "RETEST_BEFORE_DISPLACEMENT": "RETEST TOO EARLY",
    "SEQUENCE_INVALIDATED": "SETUP INVALIDATED",
    "NO_QUALIFYING_REJECTION_CANDLE": "RETEST — NO ENTRY CANDLE",
    "PROVIDER_NOT_IMPLEMENTED": "LEVEL NOT IMPLEMENTED",
    "MISSING_SESSIONS_DATA": "NO HISTORICAL DATA",
    "NO_PREVIOUS_SESSION": "NO PREVIOUS SESSION",
}


def _stage_label(failed_stage: str | None) -> str:
    if failed_stage is None:
        return "UNKNOWN"
    return _STAGE_LABELS.get(failed_stage, failed_stage)


def _no_setup(failed_stage: str | None = None,
              stage_context: dict | None = None,
              rejection_detail: dict | None = None) -> SignalResult:
    return SignalResult(
        status=SignalStatus.NO_SETUP,
        failed_stage=failed_stage,
        pipeline_stage=_stage_label(failed_stage),
        stage_context=stage_context,
        rejection_detail=rejection_detail,
    )


# ── Helper ───────────────────────────────────────────────────────────────────

def _get(obj, attr, default=None):
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


# ── LiveSignalDetector ───────────────────────────────────────────────────────


class LiveSignalDetector:
    """Stateless live signal evaluator.

    Evaluates a session snapshot through the canonical BDRR pipeline
    (stages 1a–5 + trade plan) and returns whether a valid signal exists.

    Does NOT call evaluate_trade_outcome.
    Does NOT track trade counts or daily limits (see T5).
    Does NOT connect to IBKR.

    Parameters
    ----------
    symbol : str
        Instrument symbol (e.g. "SPY").
    direction : str
        "LONG" or "SHORT".
    tick_size : float
        Instrument tick size (e.g. 0.01 for SPY).
    market_timezone : str
        IANA timezone (e.g. "America/New_York").
    session_open : str
        Session open time HH:MM (e.g. "09:30").
    entry_model : str
        "CONFIRMATION_CLOSE" or "BREAK_OF_SIGNAL_BAR".
    entry_buffer_ticks : int
        Buffer ticks added to entry price.
    stop_buffer_ticks : int
        Buffer ticks added to stop price.
    exit_target_r : int
        R-multiple for target (default 2).
    """

    def __init__(
        self,
        symbol: str,
        direction: str,
        tick_size: float,
        market_timezone: str = "America/New_York",
        session_open: str = "09:30",
        entry_model: str = "CONFIRMATION_CLOSE",
        entry_buffer_ticks: int = 0,
        stop_buffer_ticks: int = 0,
        exit_target_r: int = 2,
        level_source: str | None = None,
    ):
        self._symbol = symbol
        self._direction = direction
        self._tick_size = tick_size

        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

        # Level source: explicit override, or the canonical
        # direction-derived default (LONG -> ORB_HIGH, SHORT -> ORB_LOW).
        # Passing level_source=None (the default) preserves the exact
        # prior behavior; this parameter is pure wiring/configurability —
        # it does not decide when PDH/PDL should be used operationally.
        if level_source is None:
            level_source = "ORB_HIGH" if direction == "LONG" else "ORB_LOW"

        self._engine_config = {
            "timeframe_minutes": 1,
            "timezone": market_timezone,
            "session_open": session_open,
            "orb_start": "session_open",
            "orb_duration_minutes": 5,
            "level_source": level_source,
            "direction": direction,
            "tick_size": tick_size,
            "min_displacement_ticks": None,
            "min_penetration_ticks": None,
            "min_close_beyond_level_ticks": None,
            "min_displacement_bars": None,
            "consecutive_orb_closes": 2,
            "rejection_wick_ratio_min": None,
            "body_ratio_max": None,
            "confirmation_wick_penetration_pct_min": None,
        }

        self._tp_config = {
            "direction": direction,
            "entry_model": entry_model,
            "entry_buffer_ticks": entry_buffer_ticks,
            "stop_buffer_ticks": stop_buffer_ticks,
            "tick_size": tick_size,
        }

        self._exit_target_r = exit_target_r
        self._last_result: SignalResult | None = None

        # Previous-session historical bars (all_sessions format:
        # [{"date": "YYYY-MM-DD", "candles": [...]}]) — required by
        # build_level() for cross-session providers (PREVIOUS_DAY_HIGH,
        # PREVIOUS_DAY_LOW). Not used while level_source stays ORB_HIGH/
        # ORB_LOW (see level_provider._build_orb_level, which never reads
        # all_sessions). Populated post-construction via
        # set_previous_sessions() once available (fetched at bot boot).
        self._previous_sessions: list | None = None

    @property
    def last_result(self) -> SignalResult | None:
        """The result of the most recent evaluate() call."""
        return self._last_result

    def set_previous_sessions(self, previous_sessions: list | None) -> None:
        """Store previous-session historical bars for use by build_level().

        Parameters
        ----------
        previous_sessions : list[dict] | None
            Sessions in the all_sessions format expected by
            level_provider.build_level(..., all_sessions=...):
            [{"date": "YYYY-MM-DD", "candles": [...]}].
            Currently unused operationally while level_source is ORB —
            stored so a future PREVIOUS_DAY_HIGH/LOW level_source can
            consume it without additional wiring.
        """
        self._previous_sessions = previous_sessions

    def evaluate(self, session: dict, consumed_setup_keys: set[str] | None = None) -> SignalResult:
        """Evaluate the current session snapshot for a valid signal.

        Parameters
        ----------
        session : dict
            Session dict as produced by ``LiveSessionBuilder.current_session()``.
        consumed_setup_keys : set[str] | None
            Setup keys that have already produced a trade this session.
            If the first BDRR sequence found matches a consumed key,
            the detector continues scanning for the next valid sequence.

        Returns
        -------
        SignalResult

        Skips two kinds of stale breaks:
        1. Consumed setups (already traded, via setup_key)
        2. Dead breaks:
           - Explicitly invalidated by validate_sequence()
             (SEQUENCE_INVALIDATED, at any point after the break, before
             or after retest began).
           - Structurally terminal at the displacement stage itself
             (RETEST_BEFORE_DISPLACEMENT / DISPLACEMENT_TOO_SHORT) —
             skipped unconditionally, since first_retest_contact_index
             is a fixed historical fact once found and the displacement
             window for this exact break_idx can never grow on a later
             call. No additional ORB-reentry condition is required (see
             Gap A, 2026-08-21 audit) — _is_break_dead() remains
             available as a general-purpose helper but is no longer a
             prerequisite for abandoning these two failed_stage values.
        """
        consumed = consumed_setup_keys or set()
        skip_before = 0

        # Loop to skip consumed/dead setups and find the next valid one.
        result = None
        for _attempt in range(10):  # safety cap
            result = self._evaluate_inner(session, skip_before=skip_before)

            if result.status == SignalStatus.SIGNAL:
                if not result.setup_key or result.setup_key not in consumed:
                    break  # genuinely new setup — use it
                # Consumed setup — skip past its break
                ctx = result.stage_context or {}
                brk_idx = ctx.get("break_bar_index")
                if brk_idx is not None:
                    skip_before = brk_idx + 1
                    continue
                break

            # NO_SETUP — check if this is a dead break we should skip
            if result.status == SignalStatus.NO_SETUP and result.failed_stage == "SEQUENCE_INVALIDATED":
                # validate_sequence() has already confirmed this break is
                # dead (invalidated, whether before or after the retest
                # began) — skip straight past it, no need to re-derive
                # deadness via _is_break_dead().
                ctx = result.stage_context or {}
                brk_idx = ctx.get("break_bar_index")
                if brk_idx is not None:
                    skip_before = brk_idx + 1
                    continue

            elif result.status == SignalStatus.NO_SETUP and result.failed_stage in (
                "RETEST_BEFORE_DISPLACEMENT",
                "DISPLACEMENT_TOO_SHORT",
            ):
                # Structurally terminal, unconditionally — no need to also
                # require _is_break_dead() (ORB-band reentry). Once
                # find_displacement() has located first_retest_contact_index
                # for this break_idx, that index is a fixed historical fact:
                # the displacement window between the break and the first
                # retest contact can never grow on a later call with more
                # candles, so a displacement_bar_count that was already
                # zero (RETEST_BEFORE_DISPLACEMENT) or below min_displacement_bars
                # (DISPLACEMENT_TOO_SHORT) can never become valid for this
                # exact break_idx. Requiring an independent ORB-reentry
                # condition before abandoning it left the detector able to
                # get stuck re-reporting the same dead break indefinitely
                # (as "DISP BUILDING" / "RETEST TOO EARLY") whenever price
                # stayed outside the ORB without closing back in — see
                # Gap A in the 2026-08-21 audit.
                ctx = result.stage_context or {}
                brk_idx = ctx.get("break_bar_index")
                if brk_idx is not None:
                    skip_before = brk_idx + 1
                    continue

            # Not a dead break — return as-is
            break

        self._last_result = result
        return result

    @staticmethod
    def _is_break_dead(
        candles: list[dict],
        break_idx: int,
        direction: str,
        orb_high: float | None,
        orb_low: float | None,
        threshold: int,
    ) -> bool:
        """Check if a break is dead — price returned to ORB and stayed.

        Uses the same consecutive_orb_closes rule as sequence validation,
        applied to the candles after the break.  If N consecutive candles
        closed back inside ORB after the break, the break is dead.

        Parameters
        ----------
        candles : list[dict]
            Session candles.
        break_idx : int
            Index of the break candle.
        direction : str
            'LONG' or 'SHORT'.
        orb_high, orb_low : float | None
            ORB boundaries.
        threshold : int
            Number of consecutive closes inside ORB to declare dead.

        Returns
        -------
        bool
            True if the break is dead.
        """
        if orb_high is None or orb_low is None:
            return False

        consecutive = 0
        for i in range(break_idx + 1, len(candles)):
            close = candles[i]["close"]
            if direction == "SHORT":
                inside = close >= orb_low
            else:
                inside = close <= orb_high

            if inside:
                consecutive += 1
                if consecutive >= threshold:
                    return True
            else:
                consecutive = 0

        return False
        return result

    def _evaluate_inner(self, session: dict, skip_before: int = 0) -> SignalResult:
        """Core evaluation logic — no side effects."""
        if session is None:
            return _no_setup("NO_SESSION")

        candles = session.get("candles")
        if not isinstance(candles, list) or len(candles) == 0:
            return _no_setup("NO_CANDLES")

        # ── Stage 1a: Session context ────────────────────────────────────
        try:
            sc = build_session_context(candles, self._engine_config)
        except Exception:
            return _no_setup("INVALID_SESSION_INPUT")
        if sc.get("status") != "OK":
            return _no_setup(sc.get("failed_stage"))

        sc_candles = sc["candles"]

        # ── Stage 1b: Level (ORB) ────────────────────────────────────────
        # all_sessions is passed through unconditionally for forward
        # compatibility with cross-session providers (PREVIOUS_DAY_HIGH/
        # LOW). While self._engine_config["level_source"] stays ORB_HIGH/
        # ORB_LOW, level_provider._build_orb_level() never reads
        # all_sessions at all (it isn't even in that function's
        # parameter list) — behavior here is unchanged from before this
        # parameter was added.
        level_result = build_level(
            sc_candles, sc, self._engine_config,
            all_sessions=self._previous_sessions,
        )
        if level_result.get("status") != "OK":
            ctx = {"candle_count": len(sc_candles)}
            return _no_setup(level_result.get("failed_stage"), stage_context=ctx)

        orb_high = level_result.get("orb_high")
        orb_low = level_result.get("orb_low")
        level_price = level_result.get("level_price")
        level_source = self._engine_config.get("level_source")
        orb_ctx = {
            "orb_high": float(orb_high) if orb_high else None,
            "orb_low": float(orb_low) if orb_low else None,
            "level": float(level_price) if level_price else None,
            "level_source": level_source,
            "direction": self._direction,
        }

        # ── Stage 2: Break ───────────────────────────────────────────────
        break_config = self._engine_config
        if skip_before > 0:
            break_config = {**self._engine_config, "_scan_start_index": skip_before}
        brk = find_break(sc_candles, level_result, break_config)
        if brk.get("status") != "OK":
            return _no_setup(brk.get("failed_stage"), stage_context=orb_ctx)

        break_idx = brk.get("break_candle_index")
        break_candle = sc_candles[break_idx] if break_idx is not None else None
        break_ctx = {
            **orb_ctx,
            "break_bar_index": break_idx,
            "break_close": float(break_candle["close"]) if break_candle else None,
            "break_time_ms": break_candle["time_ms"] if break_candle else None,
            "break_level": float(level_price) if level_price else None,
        }

        # ── Stage 3: Displacement ────────────────────────────────────────
        engine_config = self._engine_config  # may be shadowed below
        min_req = engine_config.get("min_displacement_bars") or 3
        disp = find_displacement(sc_candles, level_result, brk, engine_config)
        if disp.get("status") != "OK":
            disp_count = disp.get("displacement_bar_count", 0)
            return _no_setup(disp.get("failed_stage"),
                             stage_context={**break_ctx,
                                            "displacement_bars": disp_count,
                                            "displacement_required": min_req})

        disp_ctx = {**break_ctx,
                    "displacement_bars": disp.get("displacement_bar_count"),
                    "displacement_required": min_req,
                    "displacement_end_index": disp.get("displacement_end_index")}

        # ── Stage 3b: Sequence validation ────────────────────────────────
        seq_val = validate_sequence(sc_candles, level_result, brk, disp, engine_config)
        if seq_val.get("status") == "INVALIDATED":
            # A validated-dead sequence never generates a signal, whether
            # the invalidation happened before or after the retest began.
            # Surface SEQUENCE_INVALIDATED unconditionally so the caller
            # (evaluate()'s dead-break skip loop) can advance past this
            # break and search for the next one, instead of freezing the
            # retest window on a break that will never produce an entry.
            max_vi = seq_val["max_valid_index"]
            return _no_setup("SEQUENCE_INVALIDATED",
                             stage_context={**disp_ctx,
                                            "invalidation_index": max_vi})

        # ── Stage 4: Retest window ───────────────────────────────────────
        retest = find_retest_window(sc_candles, level_result, brk, disp, engine_config)
        if retest.get("status") != "OK":
            return _no_setup(retest.get("failed_stage"),
                             stage_context=disp_ctx)

        # ── Stage 5: Rejection / Entry candle ────────────────────────────
        warmed_atr_cache = None
        warmup = session.get("warmup_candles")
        warmup_pc = session.get("warmup_previous_close")
        if warmup and isinstance(warmup, list) and len(warmup) > 0:
            combined = list(warmup) + list(sc_candles)
            full_atr = atr_series(combined, 14, initial_previous_close=warmup_pc)
            warmed_atr_cache = full_atr[len(warmup):]

        rej = find_rejection(
            sc_candles, level_result, brk, disp, retest, engine_config,
            _atr_cache=warmed_atr_cache,
        )
        if rej.get("status") != "OK":
            retest_ctx = {**disp_ctx,
                          "retest_start_index": retest.get("retest_window_start_index"),
                          "retest_end_index": retest.get("retest_window_end_index")}
            # Include rejection failure details
            failed_rules = rej.get("failed_rules", [])
            if failed_rules:
                retest_ctx["failed_rules"] = failed_rules
            # Include the last candidate candle info
            last_idx = retest.get("retest_window_end_index")
            if last_idx and last_idx < len(sc_candles):
                last_c = sc_candles[last_idx]
                retest_ctx["last_candle_close"] = float(last_c["close"])
                retest_ctx["last_candle_time_ms"] = last_c["time_ms"]
            return _no_setup(rej.get("failed_stage"),
                             stage_context=retest_ctx,
                             rejection_detail=rej)

        # ── DetectionResult/v1 ───────────────────────────────────────────
        tf_seconds = timeframe_to_seconds(session.get("timeframe", "1m"))
        session_meta = {
            "symbol": session.get("symbol"),
            "date": session.get("date"),
            "market_timezone": session.get("market_timezone"),
            "session_open_utc_ms": session.get("session_open_utc_ms"),
            "session_close_utc_ms": session.get("session_close_utc_ms"),
            "timeframe_seconds": tf_seconds,
        }
        dr_metadata = {
            "tick_size": self._tick_size,
            "session": session_meta,
            "preset_id": "live_v0.1",
            "engine_version": "maxbot_live_v0.1",
        }

        dr_build = build_detection_result(
            {"orb": level_result, "break_result": brk, "disp_result": disp,
             "retest_result": retest, "rej_result": rej},
            dr_metadata,
        )
        if dr_build.get("status") != "OK":
            return _no_setup(dr_build.get("failure_code"))

        detection_result = dr_build["detection_result"]
        if str(_get(detection_result, "status")) != "VALID":
            return _no_setup(_get(detection_result, "failed_stage"))

        # ── TradePlan/v1 ─────────────────────────────────────────────────
        pair_stop_ticks = rej.get("pair_stop_basis_ticks")
        tp_build = build_trade_plan(
            detection_result, self._tp_config,
            stop_override_ticks=pair_stop_ticks,
        )
        if tp_build.get("status") != "OK":
            return _no_setup(tp_build.get("failure_code"))

        trade_plan = tp_build["trade_plan"]

        # ── Extract prices ───────────────────────────────────────────────
        entry_price = trade_plan.entry_price.to_price()
        stop_price = trade_plan.stop_price.to_price()

        # Target based on exit_target_r
        target_map = {2: trade_plan.r2_price, 3: trade_plan.r3_price, 4: trade_plan.r4_price}
        target_pt = target_map.get(self._exit_target_r, trade_plan.r2_price)
        target_price = target_pt.to_price()

        # Entry candle timestamp
        conf_idx = rej["confirmation_candle_index"]
        entry_ts_ms = sc_candles[conf_idx]["time_ms"]

        # Setup key: structural identity of this BDRR sequence.
        # Same break + direction + level_source = same setup, regardless
        # of which bar evaluates it. A genuinely new setup will have a
        # different break_time_ms (new break candle) OR a different
        # level_source. level_source is included so that two setups
        # sharing the same direction and break timestamp but built from
        # different structural levels (e.g. ORB_HIGH vs
        # PREVIOUS_DAY_HIGH) never collide in setup_key/signal_key,
        # _consumed_setups/_consumed_signals, stale/restart handling
        # (Fix B), or trade_id derivation — level_price is deliberately
        # NOT included (it is not a stable structural identity component
        # the way direction/level_source/break_ts are).
        break_time_ms = brk.get("break_candle_index")
        if break_time_ms is not None and break_time_ms < len(sc_candles):
            break_ts = sc_candles[break_time_ms]["time_ms"]
        else:
            break_ts = 0
        setup_level_source = self._engine_config.get("level_source")
        setup_key = f"{self._direction}:{setup_level_source}:{break_ts}"
        signal_key = f"{setup_key}:{entry_ts_ms}"

        return SignalResult(
            status=SignalStatus.SIGNAL,
            direction=self._direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            entry_timestamp_ms=entry_ts_ms,
            detection_result=detection_result,
            trade_plan=trade_plan,
            setup_key=setup_key,
            signal_key=signal_key,
            pipeline_stage="SIGNAL",
            stage_context=break_ctx,
        )
