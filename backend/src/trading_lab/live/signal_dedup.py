"""Same-entry-candle SignalResult dedup — pure, standalone primitive.

Strategic rule (Max, confirmed 2026-08-24 audit):

    If more than one structural level produces a Max Entry Candle on
    the SAME candle, in the SAME direction, that is ONE trade — not
    several to arbitrate. Entry, stop, and target come from that one
    Max Entry Candle. The level(s) that contributed may be preserved
    as metadata/confluence, but must never generate separate orders.

This module implements only that narrow rule. It does NOT:
    - decide priority between structurally distinct setups that fire
      at different entry_timestamp_ms (still an open question — see
      the arbitration-policy audit);
    - handle opposite-direction same-timestamp signals (a different,
      still-open problem — deliberately left untouched here, see
      dedupe_same_entry_signals()'s docstring);
    - implement any Decision Engine, confluence grading, or ATR-based
      merge (that lives on the research track in
      confluence_zone_builder.py / contracts/zone.py and is a
      geometrically different problem: merging levels at build time,
      not deduplicating already-fired SignalResults after the fact).

This module is a pure, standalone primitive. It is NOT imported or
called from bot_runner.py, trade_orchestrator.py,
pdh_pdl_candidate_evaluator.py, LiveSignalDetector, or any execution
path. Wiring is a separate, future task.

Why a wrapper instead of extending SignalResult
------------------------------------------------
SignalResult (signal_detector.py) is a frozen, __slots__ dataclass
consumed directly by MaxBotTradeOrchestrator, DualSignalDetector, and
much of the live/ test suite. Adding a field to it would touch a
widely-shared contract for a concern (multi-source confluence
metadata) that has no consumer yet. DedupedSignalCandidate wraps a
SignalResult instead of extending it — the canonical SignalResult
inside a candidate is exactly the same object a future orchestrator
wiring would consume unchanged.

Why SignalObservation instead of a bare list[SignalResult]
------------------------------------------------------------
SignalResult itself carries no `symbol` field — today, symbol
identity is guaranteed implicitly by the caller (one
LiveSignalDetector instance is always configured for exactly one
symbol; bot_runner.py evaluates each SymbolRuntime independently).
This module never assumes that guarantee holds for its own inputs —
each observation carries its symbol explicitly, so a future
cross-symbol caller cannot accidentally merge candidates that only
happen to share a direction and timestamp across two different
underlyings (see test D6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from trading_lab.live.signal_detector import SignalResult, SignalStatus


# ── Input / output contracts ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SignalObservation:
    """One SignalResult paired with the symbol it was evaluated for.

    Parameters
    ----------
    symbol : str
        The underlying symbol this SignalResult was produced for.
        Explicit rather than inferred, since SignalResult itself does
        not carry a symbol field.
    signal : SignalResult
        The detector output, as returned by
        LiveSignalDetector.evaluate() / evaluate_pdh_pdl_candidate().
    """

    symbol: str
    signal: SignalResult


@dataclass(frozen=True, slots=True)
class DedupedSignalCandidate:
    """One executable candidate representing a single Max Entry Candle.

    Parameters
    ----------
    signal : SignalResult
        The canonical SignalResult for this candidate. When more than
        one SIGNAL shared the same (symbol, direction,
        entry_timestamp_ms) and their entry/stop/target/confirmation
        candle were verified identical, this is simply the first
        member in input order — a technical container choice, not a
        strategic "this level_source wins" decision. There is no
        strategically meaningful winner for a same-entry-candle
        group: by the rule above, they are the same trade.
    contributing_level_sources : tuple[str, ...]
        Every distinct stage_context["level_source"] value among the
        SignalResults folded into this candidate, in first-seen
        deterministic order (duplicates removed). Confluence/audit
        metadata only — never read to decide execution.
    """

    signal: SignalResult
    contributing_level_sources: tuple[str, ...]


# ── Internal helpers ─────────────────────────────────────────────────────────


def _level_source(signal: SignalResult) -> str | None:
    """Read the real level_source from stage_context — never inferred
    or mapped in parallel."""
    ctx = signal.stage_context or {}
    return ctx.get("level_source")


def _confirmation_bar(signal: SignalResult) -> object | None:
    """Read detection_result.confirmation_bar defensively.

    Returns None if detection_result is missing or has no
    confirmation_bar attribute — treated as "cannot verify identity",
    which _compatible() below turns into a safe non-merge rather than
    a crash or a guess.
    """
    dr = signal.detection_result
    if dr is None:
        return None
    return getattr(dr, "confirmation_bar", None)


def _compatible(a: SignalResult, b: SignalResult) -> bool:
    """True only if `a` and `b` — already known to share the same
    dedup key (symbol, direction, entry_timestamp_ms) — are safe to
    fold into ONE executable candidate.

    This is a defensive technical guardrail against future config
    drift (e.g. two detectors disagreeing on entry_buffer_ticks or
    exit_target_r), not a trading decision. Two SIGNAL results that
    disagree on entry/stop/target or on the confirmation candle
    itself are NEVER blended into one trade plan, regardless of how
    strong the strategic case for merging them might otherwise be.
    """
    if a.entry_price != b.entry_price:
        return False
    if a.stop_price != b.stop_price:
        return False
    if a.target_price != b.target_price:
        return False

    bar_a = _confirmation_bar(a)
    bar_b = _confirmation_bar(b)
    if bar_a is None or bar_b is None:
        # Cannot verify candle identity — refuse to merge rather than
        # assume compatibility.
        return False
    return bar_a == bar_b


def _dedup_sources(members: Sequence[SignalObservation]) -> tuple[str, ...]:
    sources: list[str] = []
    for m in members:
        src = _level_source(m.signal)
        if src is not None and src not in sources:
            sources.append(src)
    return tuple(sources)


# ── Public API ───────────────────────────────────────────────────────────────


def dedupe_same_entry_signals(
    observations: Sequence[SignalObservation],
) -> list[DedupedSignalCandidate]:
    """Fold SignalResults that share the same Max Entry Candle into
    one executable candidate each.

    Dedup key: (symbol, direction, entry_timestamp_ms) — deliberately
    NOT setup_key or signal_key, which differ by design across
    level_sources even for the identical entry candle (they embed the
    source-specific break timestamp and level_source string), and NOT
    entry_price alone (two structurally distinct setups could
    coincidentally share an entry price without being the same
    candle).

    Only observations whose `signal.status == SignalStatus.SIGNAL`
    are considered; every other status (NO_SETUP, etc.) is silently
    filtered out and never produces a DedupedSignalCandidate — this
    function only ever returns *executable* candidates.

    Opposite-direction same-timestamp signals (e.g. an ORB LONG and a
    PDL SHORT both at time T) are NEVER merged here — they are a
    different, still-open problem (see module docstring) and always
    produce separate candidates, one per direction, regardless of
    timestamp coincidence.

    A SignalResult with status SIGNAL but entry_timestamp_ms is None
    (should not happen for a real detector, but handled defensively)
    is never merged with anything else — it always becomes its own
    singleton candidate.

    Parameters
    ----------
    observations : Sequence[SignalObservation]
        Candidates to deduplicate, in the order they should be
        considered. Order matters only for two things: it is the tie
        break for which member becomes the canonical `signal` in a
        merged group, and it determines the first-seen order of
        `contributing_level_sources` — the merge decision itself
        (§_compatible) does not depend on it.

    Returns
    -------
    list[DedupedSignalCandidate]
        One candidate per distinct (symbol, direction,
        entry_timestamp_ms) group among the SIGNAL inputs — except
        that a group whose members disagree on entry/stop/target/
        confirmation candle is NEVER silently merged: each of its
        members is returned as its own separate candidate instead
        (see _compatible()). Deterministic for a given input order.
    """
    signals_only = [
        obs for obs in observations if obs.signal.status == SignalStatus.SIGNAL
    ]

    groups: dict[tuple, list[SignalObservation]] = {}
    order: list[tuple] = []
    for i, obs in enumerate(signals_only):
        ts = obs.signal.entry_timestamp_ms
        if ts is None:
            # Defensive: no concrete entry-candle timestamp to compare
            # against — never eligible to merge with anything.
            key = ("__no_timestamp__", i)
        else:
            key = (obs.symbol, obs.signal.direction, ts)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(obs)

    result: list[DedupedSignalCandidate] = []
    for key in order:
        members = groups[key]
        canonical = members[0].signal

        if len(members) == 1:
            result.append(DedupedSignalCandidate(
                signal=canonical,
                contributing_level_sources=_dedup_sources(members),
            ))
            continue

        all_compatible = all(
            _compatible(canonical, m.signal) for m in members[1:]
        )

        if all_compatible:
            result.append(DedupedSignalCandidate(
                signal=canonical,
                contributing_level_sources=_dedup_sources(members),
            ))
        else:
            # Defensive fallback: entry/stop/target/confirmation
            # candle disagree despite sharing the dedup key (should
            # never happen with today's shared-config detectors, but
            # this function never guesses). Keep every member as its
            # own separate candidate rather than blending or
            # arbitrarily picking one.
            for m in members:
                result.append(DedupedSignalCandidate(
                    signal=m.signal,
                    contributing_level_sources=_dedup_sources([m]),
                ))

    return result
