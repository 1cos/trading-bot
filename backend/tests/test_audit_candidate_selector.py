"""Tests for audit_candidate_selector — is_audit_worthy / select_audit_candidates.

Covers:
   1.  VALID record is selected
   2.  ORB/session failure excluded (LEVEL_NOT_FOUND)
   3.  BREAK_NOT_FOUND excluded
   4.  Displacement failure after break selected (DISPLACEMENT_MINIMUM_NOT_MET)
   5.  RETEST_BEFORE_DISPLACEMENT selected
   6.  Sequence invalidation after break → not testable via typed records
       (SEQUENCE_INVALIDATED not in FailedStage enum; A2 builder rejects these)
       → tested via RETEST_NOT_FOUND which covers the same depth
   7.  RETEST_NOT_FOUND after break selected
   8.  NO_QUALIFYING_REJECTION_CANDLE selected
   9.  REJECTED with reached_break=False excluded
  10.  Input order preserved
  11.  Duplicates remain (no dedup)
  12.  Empty input → empty tuple
  13.  Generator input supported
  14.  Non-DetectorAuditRecord rejected
  15.  Unknown FailedStage raises ValueError
  16.  Impossible combinations rejected
  17.  Input not mutated
  18.  Records returned unchanged (identity preserved)
"""

import pytest

from trading_lab.audit_candidate_selector import (
    is_audit_worthy,
    select_audit_candidates,
)
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
)
from trading_lab.contracts.primitives import PriceTicks

# Reuse existing detection result fixtures
from test_contract_detection_result import (
    _valid_kwargs as _dr_valid_kwargs,
    _invalid_kwargs as _dr_invalid_kwargs,
)


TICK_SIZE = "0.01"
_SENTINEL = object()  # default-argument sentinel


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _valid_dr() -> DetectionResult:
    return DetectionResult(**_dr_valid_kwargs())


def _invalid_dr(**overrides) -> DetectionResult:
    kw = _dr_invalid_kwargs()
    kw.update(overrides)
    return DetectionResult(**kw)


def _make_valid_audit() -> DetectorAuditRecord:
    """VALID audit record with full progression."""
    return DetectorAuditRecord(
        schema_version="DetectorAuditRecord/v1",
        audit_id="a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        symbol="SPY",
        session_date="2026-07-24",
        timeframe="5m",
        direction=Direction.LONG,
        candidate_status=CandidateStatus.VALID,
        failed_stage=None,
        failed_rules=(),
        reached_orb=True,
        reached_break=True,
        reached_displacement=True,
        reached_retest=True,
        reached_rejection_scan=True,
        orb_high=PriceTicks(ticks=55000, tick_size=TICK_SIZE),
        orb_low=PriceTicks(ticks=54900, tick_size=TICK_SIZE),
        orb_candle_time_ms=1753365000000,
        break_candle_time_ms=1753365300000,
        last_relevant_time_ms=1753369200000,
        detection_result=_valid_dr(),
    )


def _make_rejected_audit(
    failed_stage: FailedStage,
    reached_orb: bool = True,
    reached_break: bool = True,
    reached_displacement: bool = False,
    reached_retest: bool = False,
    reached_rejection_scan: bool = False,
    audit_id: str = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
    symbol: str = "QQQ",
    orb_high: PriceTicks | None = _SENTINEL,
    orb_low: PriceTicks | None = _SENTINEL,
    orb_candle_time_ms: int | None = _SENTINEL,
    break_candle_time_ms: int | None = _SENTINEL,
    last_relevant_time_ms: int | None = _SENTINEL,
) -> DetectorAuditRecord:
    """REJECTED audit record at a configurable failure depth."""
    has_orb = reached_orb
    has_break = reached_break

    if orb_high is _SENTINEL:
        orb_high = PriceTicks(ticks=48500, tick_size=TICK_SIZE) if has_orb else None
    if orb_low is _SENTINEL:
        orb_low = PriceTicks(ticks=48400, tick_size=TICK_SIZE) if has_orb else None
    if orb_candle_time_ms is _SENTINEL:
        orb_candle_time_ms = 1753451400000 if has_orb else None
    if break_candle_time_ms is _SENTINEL:
        break_candle_time_ms = 1753451700000 if has_break else None
    if last_relevant_time_ms is _SENTINEL:
        last_relevant_time_ms = break_candle_time_ms

    dr = _invalid_dr(failed_stage=failed_stage)

    return DetectorAuditRecord(
        schema_version="DetectorAuditRecord/v1",
        audit_id=audit_id,
        symbol=symbol,
        session_date="2026-07-25",
        timeframe="3m",
        direction=Direction.SHORT,
        candidate_status=CandidateStatus.REJECTED,
        failed_stage=failed_stage,
        failed_rules=(),
        reached_orb=reached_orb,
        reached_break=reached_break,
        reached_displacement=reached_displacement,
        reached_retest=reached_retest,
        reached_rejection_scan=reached_rejection_scan,
        orb_high=orb_high,
        orb_low=orb_low,
        orb_candle_time_ms=orb_candle_time_ms,
        break_candle_time_ms=break_candle_time_ms,
        last_relevant_time_ms=last_relevant_time_ms,
        detection_result=dr,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. VALID record is selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidSelected:
    def test_valid_is_audit_worthy(self):
        assert is_audit_worthy(_make_valid_audit()) is True

    def test_valid_in_batch(self):
        r = _make_valid_audit()
        result = select_audit_candidates([r])
        assert len(result) == 1
        assert result[0] is r


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LEVEL_NOT_FOUND excluded
# ═══════════════════════════════════════════════════════════════════════════════


class TestLevelNotFoundExcluded:
    def test_not_audit_worthy(self):
        r = _make_rejected_audit(
            FailedStage.LEVEL_NOT_FOUND,
            reached_orb=False,
            reached_break=False,
        )
        assert is_audit_worthy(r) is False

    def test_excluded_from_batch(self):
        r = _make_rejected_audit(
            FailedStage.LEVEL_NOT_FOUND,
            reached_orb=False,
            reached_break=False,
            orb_high=None,
            orb_low=None,
            orb_candle_time_ms=None,
            break_candle_time_ms=None,
            last_relevant_time_ms=None,
        )
        # Need to rebuild without ORB data — use simpler approach
        result = select_audit_candidates([r])
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BREAK_NOT_FOUND excluded
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakNotFoundExcluded:
    def test_not_audit_worthy(self):
        r = _make_rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            reached_orb=True,
            reached_break=False,
        )
        assert is_audit_worthy(r) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Displacement failure selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisplacementFailureSelected:
    def test_displacement_minimum_not_met(self):
        r = _make_rejected_audit(
            FailedStage.DISPLACEMENT_MINIMUM_NOT_MET,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
        )
        assert is_audit_worthy(r) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RETEST_BEFORE_DISPLACEMENT selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestBeforeDisplacementSelected:
    def test_selected(self):
        r = _make_rejected_audit(
            FailedStage.RETEST_BEFORE_DISPLACEMENT,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
        )
        assert is_audit_worthy(r) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 6 & 7. RETEST_NOT_FOUND selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetestNotFoundSelected:
    def test_selected(self):
        r = _make_rejected_audit(
            FailedStage.RETEST_NOT_FOUND,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
        )
        assert is_audit_worthy(r) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 8. NO_QUALIFYING_REJECTION_CANDLE selected
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoQualifyingRejectionSelected:
    def test_selected(self):
        r = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
        )
        assert is_audit_worthy(r) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 9. REJECTED with reached_break=False excluded
# ═══════════════════════════════════════════════════════════════════════════════


class TestReachedBreakFalseExcluded:
    def test_break_false_excluded(self):
        r = _make_rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            reached_orb=True,
            reached_break=False,
        )
        assert is_audit_worthy(r) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Input order preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrderPreserved:
    def test_order(self):
        r1 = _make_valid_audit()
        r2 = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
            audit_id="cccccccc-dddd-4eee-afff-000000000001",
        )
        result = select_audit_candidates([r2, r1])
        assert result[0] is r2
        assert result[1] is r1


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Duplicates remain
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicatesRemain:
    def test_no_dedup(self):
        r = _make_valid_audit()
        result = select_audit_candidates([r, r])
        assert len(result) == 2
        assert result[0] is r
        assert result[1] is r


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Empty input
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyInput:
    def test_empty_list(self):
        assert select_audit_candidates([]) == ()

    def test_empty_tuple(self):
        assert select_audit_candidates(()) == ()


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Generator input
# ═══════════════════════════════════════════════════════════════════════════════


class TestGeneratorInput:
    def test_generator(self):
        r = _make_valid_audit()

        def gen():
            yield r

        result = select_audit_candidates(gen())
        assert len(result) == 1
        assert result[0] is r


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Non-DetectorAuditRecord rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonRecordRejected:
    def test_dict_rejected(self):
        with pytest.raises(TypeError, match="DetectorAuditRecord"):
            is_audit_worthy({"status": "VALID"})

    def test_string_rejected(self):
        with pytest.raises(TypeError, match="DetectorAuditRecord"):
            is_audit_worthy("not a record")

    def test_bad_item_in_batch(self):
        r = _make_valid_audit()
        with pytest.raises(TypeError, match="DetectorAuditRecord"):
            select_audit_candidates([r, "bad"])


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Unknown FailedStage raises ValueError
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnknownFailedStage:
    def test_unknown_stage_raises(self):
        """Simulate a future FailedStage not yet classified."""
        from enum import StrEnum, unique

        @unique
        class ExtendedStage(StrEnum):
            NEW_FUTURE_STAGE = "NEW_FUTURE_STAGE"

        # Construct a record with a FailedStage-like enum that the
        # selector doesn't know about. We need to trick the contract.
        # Instead, we test the validation directly by monkeypatching.
        r = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
        )
        # Patch the frozen dataclass to inject an unknown stage
        object.__setattr__(r, "failed_stage", ExtendedStage.NEW_FUTURE_STAGE)
        with pytest.raises(ValueError, match="Unknown FailedStage"):
            is_audit_worthy(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Impossible combinations rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestImpossibleCombinations:
    def test_audit_worthy_stage_but_break_false(self):
        """NO_QUALIFYING_REJECTION_CANDLE with reached_break=False."""
        r = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
        )
        # Patch to create impossible combination
        object.__setattr__(r, "reached_break", False)
        with pytest.raises(ValueError, match="Impossible combination"):
            is_audit_worthy(r)

    def test_break_not_found_but_break_true(self):
        """BREAK_NOT_FOUND with reached_break=True."""
        r = _make_rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            reached_orb=True,
            reached_break=False,
        )
        object.__setattr__(r, "reached_break", True)
        with pytest.raises(ValueError, match="Impossible combination"):
            is_audit_worthy(r)

    def test_level_not_found_but_orb_true(self):
        """LEVEL_NOT_FOUND with reached_orb=True."""
        r = _make_rejected_audit(
            FailedStage.LEVEL_NOT_FOUND,
            reached_orb=False,
            reached_break=False,
        )
        object.__setattr__(r, "reached_orb", True)
        with pytest.raises(ValueError, match="Impossible combination"):
            is_audit_worthy(r)


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Input not mutated
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoMutation:
    def test_list_not_mutated(self):
        r1 = _make_valid_audit()
        r2 = _make_rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            reached_orb=True,
            reached_break=False,
        )
        input_list = [r1, r2]
        original_len = len(input_list)
        select_audit_candidates(input_list)
        assert len(input_list) == original_len
        assert input_list[0] is r1
        assert input_list[1] is r2


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Records returned unchanged (identity preserved)
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdentityPreserved:
    def test_same_objects(self):
        r = _make_valid_audit()
        result = select_audit_candidates([r])
        assert result[0] is r

    def test_rejected_same_object(self):
        r = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
        )
        result = select_audit_candidates([r])
        assert result[0] is r


# ═══════════════════════════════════════════════════════════════════════════════
# Mixed batch integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestMixedBatch:
    def test_mixed_selection(self):
        """Multiple records: only audit-worthy survive."""
        valid = _make_valid_audit()
        break_fail = _make_rejected_audit(
            FailedStage.BREAK_NOT_FOUND,
            reached_orb=True,
            reached_break=False,
            audit_id="11111111-1111-4111-a111-111111111111",
        )
        level_fail = _make_rejected_audit(
            FailedStage.LEVEL_NOT_FOUND,
            reached_orb=False,
            reached_break=False,
            audit_id="22222222-2222-4222-a222-222222222222",
            orb_high=None,
            orb_low=None,
            orb_candle_time_ms=None,
            break_candle_time_ms=None,
            last_relevant_time_ms=None,
        )
        rejection_fail = _make_rejected_audit(
            FailedStage.NO_QUALIFYING_REJECTION_CANDLE,
            reached_orb=True,
            reached_break=True,
            reached_displacement=True,
            reached_retest=True,
            reached_rejection_scan=True,
            audit_id="33333333-3333-4333-a333-333333333333",
        )
        disp_fail = _make_rejected_audit(
            FailedStage.RETEST_BEFORE_DISPLACEMENT,
            reached_orb=True,
            reached_break=True,
            audit_id="44444444-4444-4444-a444-444444444444",
        )

        result = select_audit_candidates([
            valid, break_fail, level_fail, rejection_fail, disp_fail,
        ])
        assert len(result) == 3
        assert result[0] is valid
        assert result[1] is rejection_fail
        assert result[2] is disp_fail


# ═══════════════════════════════════════════════════════════════════════════════
# Sequence invalidation regression (A3.1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSequenceInvalidationSelected:
    def test_sequence_invalidated_is_audit_worthy(self):
        """SEQUENCE_INVALIDATED after break must be selected for audit."""
        r = _make_rejected_audit(
            FailedStage.SEQUENCE_INVALIDATED,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
            reached_retest=False,
            reached_rejection_scan=False,
            audit_id="55555555-5555-4555-a555-555555555555",
        )
        assert is_audit_worthy(r) is True

    def test_sequence_invalidated_in_batch(self):
        """SEQUENCE_INVALIDATED survives batch selection."""
        r = _make_rejected_audit(
            FailedStage.SEQUENCE_INVALIDATED,
            reached_orb=True,
            reached_break=True,
            reached_displacement=False,
            audit_id="66666666-6666-4666-a666-666666666666",
        )
        result = select_audit_candidates([r])
        assert len(result) == 1
        assert result[0] is r
