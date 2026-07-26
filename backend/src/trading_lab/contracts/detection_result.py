"""Canonical DetectionResult/v1 contract type for the BDRR pipeline.

Ported from the frozen schema in BDRR_ENGINE_CANONICAL_HANDOFF.md §3.3
and the JavaScript reference in estrategie/bdrr_detection_result.js.

This is a pure data contract.  It holds and validates the canonical fields
produced by the detection engine adapter.  It does not generate result_id
or produced_at, does not compute derived fields, and does not contain
any Stage 1–5 strategy logic.

Validation matches the authoritative JavaScript behavior:
  - schema_version must equal "DetectionResult/v1" exactly.
  - Type validation on every field (matching the schema).
  - No cross-field invariants are enforced at construction time.
    The JS buildDetectionResult produces correct cross-field relationships
    by construction (e.g. failed_stage is null when status is VALID) but
    does not validate the assembled object.  This Python type matches that
    behavior.

Serialization (.to_dict()) produces the canonical JSON-compatible shape
with every field present (including null-valued fields and empty arrays),
matching the JavaScript Object.freeze output exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.enums import (
    DetectionStatus,
    Direction,
    FailedStage,
    LevelSource,
)
from trading_lab.contracts.primitives import PriceTicks, Rational
from trading_lab.contracts.rule_failure import RejectionAttempt, RuleFailure
from trading_lab.contracts.session_metadata import SessionMetadata


# ── Validation helpers ────────────────────────────────────────────────────────

_SCHEMA_VERSION = "DetectionResult/v1"


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int, got bool")
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _require_optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an int or None, got bool")
    if not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int or None, got {type(value).__name__}"
        )
    return value


def _require_optional_str(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"{name} must be a str or None, got {type(value).__name__}"
        )
    return value


def _require_type(value: object, expected: type, name: str) -> object:
    if not isinstance(value, expected):
        raise TypeError(
            f"{name} must be a {expected.__name__} instance, "
            f"got {type(value).__name__}"
        )
    return value


def _require_optional_type(
    value: object, expected: type, name: str
) -> object | None:
    if value is None:
        return None
    if not isinstance(value, expected):
        raise TypeError(
            f"{name} must be a {expected.__name__} instance or None, "
            f"got {type(value).__name__}"
        )
    return value


def _require_optional_enum(
    value: object, enum_type: type, name: str
) -> object | None:
    if value is None:
        return None
    if not isinstance(value, enum_type):
        raise TypeError(
            f"{name} must be a {enum_type.__name__} member or None, "
            f"got {type(value).__name__}"
        )
    return value


def _require_tuple_of(
    value: object, element_type: type, name: str
) -> tuple:
    if not isinstance(value, tuple):
        raise TypeError(
            f"{name} must be a tuple, got {type(value).__name__}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, element_type):
            raise TypeError(
                f"{name}[{i}] must be a {element_type.__name__} instance, "
                f"got {type(item).__name__}"
            )
    return value


def _require_optional_tuple_of(
    value: object, element_type: type, name: str
) -> tuple | None:
    if value is None:
        return None
    return _require_tuple_of(value, element_type, name)


# ── DetectionResult ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Complete canonical DetectionResult/v1 data contract.

    Every field matches the frozen schema in §3.3 exactly.
    Field order follows the canonical schema.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    schema_version: str
    result_id: str
    produced_at: str
    session: SessionMetadata
    preset_id: str
    engine_version: str

    # ── Status ───────────────────────────────────────────────────────────────
    status: DetectionStatus
    failed_stage: FailedStage | None
    failed_rules: tuple[RuleFailure, ...]

    # ── Level ────────────────────────────────────────────────────────────────
    level_price: PriceTicks | None
    level_source: LevelSource | None
    level_bar: Bar | None
    direction: Direction | None

    # ── Break ────────────────────────────────────────────────────────────────
    break_bar: Bar | None
    directional_break_distance: DirectionalTickDistance | None

    # ── Displacement ─────────────────────────────────────────────────────────
    displacement_window: tuple[Bar, ...]
    displacement_bar_count: int | None
    displacement_pts: AbsoluteTickDistance | None
    displacement_pct: Rational | None
    rejection_side_clearance_by_bar: tuple[DirectionalTickDistance, ...] | None
    minimum_rejection_side_clearance: DirectionalTickDistance | None
    average_rejection_side_clearance: str | None

    # ── Retest ───────────────────────────────────────────────────────────────
    retest_window: tuple[Bar, ...]
    retest_bar_count: int | None
    failed_retest_count: int | None
    failed_retests: tuple[RejectionAttempt, ...]
    bars_break_to_first_retest: int | None
    bars_break_to_confirmation: int | None
    retest_closest_approach: AbsoluteTickDistance | None
    retest_penetration_through_level: AbsoluteTickDistance | None
    retest_displacement_retracement_pct: Rational | None

    # ── Rejection candle ─────────────────────────────────────────────────────
    confirmation_bar: Bar | None
    confirmation_rej_wick: Rational | None
    confirmation_body: Rational | None
    confirmation_opp_wick: Rational | None
    confirmation_favorable_close_location: Rational | None
    confirmation_penetration: AbsoluteTickDistance | None
    confirmation_close_beyond_level: DirectionalTickDistance | None

    def __post_init__(self) -> None:
        # ── Identity ─────────────────────────────────────────────────────
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(
                f'schema_version must be "{_SCHEMA_VERSION}", '
                f"got {self.schema_version!r}"
            )
        _require_str(self.result_id, "result_id")
        _require_str(self.produced_at, "produced_at")
        _require_type(self.session, SessionMetadata, "session")
        _require_str(self.preset_id, "preset_id")
        _require_str(self.engine_version, "engine_version")

        # ── Status ───────────────────────────────────────────────────────
        _require_type(self.status, DetectionStatus, "status")
        _require_optional_enum(self.failed_stage, FailedStage, "failed_stage")
        _require_tuple_of(self.failed_rules, RuleFailure, "failed_rules")

        # ── Level ────────────────────────────────────────────────────────
        _require_optional_type(self.level_price, PriceTicks, "level_price")
        _require_optional_enum(self.level_source, LevelSource, "level_source")
        _require_optional_type(self.level_bar, Bar, "level_bar")
        _require_optional_enum(self.direction, Direction, "direction")

        # ── Break ────────────────────────────────────────────────────────
        _require_optional_type(self.break_bar, Bar, "break_bar")
        _require_optional_type(
            self.directional_break_distance,
            DirectionalTickDistance,
            "directional_break_distance",
        )

        # ── Displacement ─────────────────────────────────────────────────
        _require_tuple_of(self.displacement_window, Bar, "displacement_window")
        _require_optional_int(
            self.displacement_bar_count, "displacement_bar_count"
        )
        _require_optional_type(
            self.displacement_pts, AbsoluteTickDistance, "displacement_pts"
        )
        _require_optional_type(
            self.displacement_pct, Rational, "displacement_pct"
        )
        _require_optional_tuple_of(
            self.rejection_side_clearance_by_bar,
            DirectionalTickDistance,
            "rejection_side_clearance_by_bar",
        )
        _require_optional_type(
            self.minimum_rejection_side_clearance,
            DirectionalTickDistance,
            "minimum_rejection_side_clearance",
        )
        _require_optional_str(
            self.average_rejection_side_clearance,
            "average_rejection_side_clearance",
        )

        # ── Retest ───────────────────────────────────────────────────────
        _require_tuple_of(self.retest_window, Bar, "retest_window")
        _require_optional_int(self.retest_bar_count, "retest_bar_count")
        _require_optional_int(
            self.failed_retest_count, "failed_retest_count"
        )
        _require_tuple_of(
            self.failed_retests, RejectionAttempt, "failed_retests"
        )
        _require_optional_int(
            self.bars_break_to_first_retest, "bars_break_to_first_retest"
        )
        _require_optional_int(
            self.bars_break_to_confirmation, "bars_break_to_confirmation"
        )
        _require_optional_type(
            self.retest_closest_approach,
            AbsoluteTickDistance,
            "retest_closest_approach",
        )
        _require_optional_type(
            self.retest_penetration_through_level,
            AbsoluteTickDistance,
            "retest_penetration_through_level",
        )
        _require_optional_type(
            self.retest_displacement_retracement_pct,
            Rational,
            "retest_displacement_retracement_pct",
        )

        # ── Rejection candle ─────────────────────────────────────────────
        _require_optional_type(
            self.confirmation_bar, Bar, "confirmation_bar"
        )
        _require_optional_type(
            self.confirmation_rej_wick, Rational, "confirmation_rej_wick"
        )
        _require_optional_type(
            self.confirmation_body, Rational, "confirmation_body"
        )
        _require_optional_type(
            self.confirmation_opp_wick, Rational, "confirmation_opp_wick"
        )
        _require_optional_type(
            self.confirmation_favorable_close_location,
            Rational,
            "confirmation_favorable_close_location",
        )
        _require_optional_type(
            self.confirmation_penetration,
            AbsoluteTickDistance,
            "confirmation_penetration",
        )
        _require_optional_type(
            self.confirmation_close_beyond_level,
            DirectionalTickDistance,
            "confirmation_close_beyond_level",
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical JSON-compatible shape.

        Every field is present (including null values and empty arrays).
        Nested contract types use their own to_dict().
        Enum values serialize as their string value.
        """

        def _opt(v: object) -> object:
            if v is None:
                return None
            if hasattr(v, "to_dict"):
                return v.to_dict()  # type: ignore[union-attr]
            return v

        def _enum_opt(v: object) -> object:
            if v is None:
                return None
            return str(v)

        def _tuple_to_list(t: tuple) -> list:
            return [item.to_dict() for item in t]

        def _opt_tuple_to_list(t: tuple | None) -> list | None:
            if t is None:
                return None
            return [item.to_dict() for item in t]

        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "produced_at": self.produced_at,
            "session": self.session.to_dict(),
            "preset_id": self.preset_id,
            "engine_version": self.engine_version,
            "status": str(self.status),
            "failed_stage": _enum_opt(self.failed_stage),
            "failed_rules": _tuple_to_list(self.failed_rules),
            "level_price": _opt(self.level_price),
            "level_source": _enum_opt(self.level_source),
            "level_bar": _opt(self.level_bar),
            "direction": _enum_opt(self.direction),
            "break_bar": _opt(self.break_bar),
            "directional_break_distance": _opt(
                self.directional_break_distance
            ),
            "displacement_window": _tuple_to_list(self.displacement_window),
            "displacement_bar_count": self.displacement_bar_count,
            "displacement_pts": _opt(self.displacement_pts),
            "displacement_pct": _opt(self.displacement_pct),
            "rejection_side_clearance_by_bar": _opt_tuple_to_list(
                self.rejection_side_clearance_by_bar
            ),
            "minimum_rejection_side_clearance": _opt(
                self.minimum_rejection_side_clearance
            ),
            "average_rejection_side_clearance": (
                self.average_rejection_side_clearance
            ),
            "retest_window": _tuple_to_list(self.retest_window),
            "retest_bar_count": self.retest_bar_count,
            "failed_retest_count": self.failed_retest_count,
            "failed_retests": _tuple_to_list(self.failed_retests),
            "bars_break_to_first_retest": self.bars_break_to_first_retest,
            "bars_break_to_confirmation": self.bars_break_to_confirmation,
            "retest_closest_approach": _opt(self.retest_closest_approach),
            "retest_penetration_through_level": _opt(
                self.retest_penetration_through_level
            ),
            "retest_displacement_retracement_pct": _opt(
                self.retest_displacement_retracement_pct
            ),
            "confirmation_bar": _opt(self.confirmation_bar),
            "confirmation_rej_wick": _opt(self.confirmation_rej_wick),
            "confirmation_body": _opt(self.confirmation_body),
            "confirmation_opp_wick": _opt(self.confirmation_opp_wick),
            "confirmation_favorable_close_location": _opt(
                self.confirmation_favorable_close_location
            ),
            "confirmation_penetration": _opt(self.confirmation_penetration),
            "confirmation_close_beyond_level": _opt(
                self.confirmation_close_beyond_level
            ),
        }
