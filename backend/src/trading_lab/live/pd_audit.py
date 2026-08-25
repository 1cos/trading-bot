"""PDH/PDL audit telemetry — pure formatting, ZERO trading logic.

This module exists for one reason: make the PDH/PDL evaluation path
observable after the fact. The 2026-08-24 post-close audit could not
answer "was PDH/PDL actually used today?" because the eligibility
verdict and the PD detector's pipeline stage were computed on every
bar and then discarded — ``rt.pdh_pdl_candidate`` and
``rt.decision_trace`` are in-memory only, and no session log was
persisted.

What this module does NOT do
----------------------------
    - Does NOT evaluate eligibility, build a level, run a detector,
      or touch a state machine. It only *reads* results other code
      already computed and turns them into a record + a log line.
    - Does NOT decide anything. Nothing here can change whether a
      setup is eligible, whether a signal fires, or whether a trade
      is taken. Every function is pure: no I/O, no mutation of its
      inputs, no global state.
    - Does NOT re-run ``evaluate_pdh_pdl_candidate()``. The caller
      passes in the result of the evaluation that already happened,
      so audit telemetry can never diverge from what the runtime
      actually did, and never costs a second evaluation.

Emission policy
---------------
``PD_AUDIT_EMIT_EVERY_BAR`` controls volume:

    False (default) — emit only when the audit state CHANGES for a
        given (symbol, direction). Each emitted record carries
        ``evaluations_since_last_emit``, so no evaluation is ever
        invisible: two consecutive records N bars apart mean the
        state held steady across those N evaluations. 17 symbols x 2
        directions x ~390 bars/day would otherwise be ~13k identical
        records per session, which buries the transitions an audit is
        actually looking for.

    True — emit one record per evaluation, unconditionally.

"State" here means the audit tuple (see ``pd_audit_state_key``):
eligibility, reason, stage, lifecycle, setup_key. Deliberately NOT
current_price — price ticks every bar and would defeat the dedup
without adding audit value.
"""

from __future__ import annotations

# Log-line prefix. Chosen so `grep PD_AUDIT:` over a session log
# yields exactly the audit stream and nothing else.
PD_AUDIT_LOG_PREFIX = "PD_AUDIT:"

# See "Emission policy" in the module docstring.
PD_AUDIT_EMIT_EVERY_BAR = False

# Render order for format_pd_audit_line(). Maps record key -> the
# short label used in the log line (the ineligibility reason is stored
# as "failed_reason" but rendered as "reason=", matching the format
# the audit task specified).
_LINE_FIELDS: tuple[tuple[str, str], ...] = (
    ("symbol", "symbol"),
    ("direction", "direction"),
    ("level_source", "level"),
    ("level_price", "price"),
    ("current_price", "current"),
    ("eligible", "eligible"),
    ("failed_reason", "reason"),
    ("pipeline_stage", "stage"),
    ("current_state", "state"),
    ("setup_key", "setup_key"),
)


def normalize_stage(stage: str | None) -> str | None:
    """Turn a human stage label into a greppable token.

    ``SignalResult.pipeline_stage`` carries display labels with spaces
    ("WAITING FOR RETEST"); an audit stream wants a single token
    ("WAITING_FOR_RETEST"). Purely cosmetic — the raw, unmodified
    ``failed_stage`` code is kept alongside it in the record.
    """
    if stage is None:
        return None
    return str(stage).strip().replace(" ", "_")


def build_pd_audit_record(
    *,
    symbol: str,
    direction: str,
    level_source: str,
    level_price: float | None,
    current_price: float | None,
    bar_time_ms: int | None,
    eligibility: dict | None,
    signal_result: object | None,
    current_state: object | None = None,
    evaluations_since_last_emit: int = 1,
) -> dict:
    """Build one audit record from an evaluation that already ran.

    Parameters
    ----------
    eligibility : dict or None
        ``evaluate_pdh_pdl_candidate()["eligibility"]`` verbatim —
        read, never modified.
    signal_result : SignalResult or None
        ``evaluate_pdh_pdl_candidate()["pdh_pdl_result"]`` verbatim.
        None whenever the level was not eligible (the evaluator does
        not build a detector in that case), so ``pipeline_stage`` and
        ``setup_key`` are legitimately absent then.
    current_state : object or None
        The symbol's orchestrator lifecycle (e.g. WAITING_FOR_SIGNAL).
        This is the *execution* state, distinct from the PD detector's
        own ``pipeline_stage``.

    Returns
    -------
    dict
        Audit payload. Keys whose value is None are dropped, so a
        not-eligible record simply has no stage/setup_key rather than
        carrying nulls.
    """
    elig = eligibility or {}
    eligible = bool(elig.get("eligible", False))

    # "reason" is a success label when eligible
    # ("ORB_BREAK_AND_DISPLACEMENT_COMPLETE"), so it is only surfaced
    # as failed_reason when it actually explains a rejection.
    failed_reason = None if eligible else elig.get("reason")

    status = getattr(signal_result, "status", None)

    record: dict = {
        "symbol": symbol,
        "direction": direction,
        "level_source": level_source,
        "level_price": level_price,
        "current_price": current_price,
        "bar_time_ms": bar_time_ms,
        "eligible": eligible,
        "failed_reason": failed_reason,
        # Two distinct stages, deliberately never merged into one key:
        # eligibility_failed_stage belongs to the ORB precondition the
        # eligibility gate checks, failed_stage belongs to the PD
        # detector's own BDRR run (which only exists once eligible).
        # Collapsing them would make an audit record ambiguous about
        # which pipeline actually stopped.
        "eligibility_failed_stage": elig.get("failed_stage"),
        "failed_stage": getattr(signal_result, "failed_stage", None),
        "pipeline_stage": normalize_stage(getattr(signal_result, "pipeline_stage", None)),
        "signal_status": str(status) if status is not None else None,
        "current_state": str(current_state) if current_state is not None else None,
        "setup_key": getattr(signal_result, "setup_key", None),
        "evaluations_since_last_emit": evaluations_since_last_emit,
    }
    return {k: v for k, v in record.items() if v is not None}


def pd_audit_state_key(record: dict) -> tuple:
    """The subset of a record that defines "the audit state changed".

    Excludes current_price, bar_time_ms and the evaluation counter —
    those move every bar and would defeat change-based emission
    without telling an auditor anything.
    """
    return (
        record.get("symbol"),
        record.get("direction"),
        record.get("level_source"),
        record.get("level_price"),
        record.get("eligible"),
        record.get("failed_reason"),
        record.get("eligibility_failed_stage"),
        record.get("failed_stage"),
        record.get("pipeline_stage"),
        record.get("signal_status"),
        record.get("current_state"),
        record.get("setup_key"),
    )


def format_pd_audit_line(record: dict) -> str:
    """Render a record as a single greppable ``PD_AUDIT:`` line.

    One line (not one field per line) so that `grep PD_AUDIT:` returns
    whole records and the stream stays machine-parseable. Absent
    fields are omitted rather than rendered as None.
    """
    parts = []
    for key, label in _LINE_FIELDS:
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{label}={value}")
    return f"{PD_AUDIT_LOG_PREFIX} " + " ".join(parts)
