"""Canonical ScoredSetup/v1 contract type (§3.6).

FROZEN — immutable after construction.

The output of the Quality Scorer: a scored and graded setup candidate
with all module results, weight snapshots, and confluence data.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_lab.contracts.confluence_result import ConfluenceResult
from trading_lab.contracts.enums import QualityGrade
from trading_lab.contracts.module_result import ModuleResult
from trading_lab.contracts.setup_candidate import SetupCandidate


def _require_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a str, got {type(val).__name__}"
        )


def _require_dict(val, name):
    if not isinstance(val, dict):
        raise TypeError(
            f"{name} must be a dict, got {type(val).__name__}"
        )


def _require_decimal_str(val, name):
    if not isinstance(val, str):
        raise TypeError(
            f"{name} must be a Decimal string, got {type(val).__name__}"
        )
    try:
        from decimal import Decimal, InvalidOperation
        Decimal(val)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} is not a valid Decimal string: {val!r}")


def _validate_module_scores_dict(val, name):
    """Validate a dict[str, ModuleResult]."""
    _require_dict(val, name)
    for k, v in val.items():
        if not isinstance(k, str):
            raise TypeError(f"{name} keys must be str, got {type(k).__name__}")
        if not isinstance(v, ModuleResult):
            raise TypeError(
                f"{name}[{k!r}] must be a ModuleResult, "
                f"got {type(v).__name__}"
            )


def _validate_weights_snapshot(val, name, expected_keys):
    """Validate a dict[str, Decimal-str] with keys matching a module dict."""
    _require_dict(val, name)
    if set(val.keys()) != expected_keys:
        raise ValueError(
            f"{name} keys must match module scores keys exactly"
        )
    for k, v in val.items():
        _require_decimal_str(v, f"{name}[{k!r}]")


@dataclass(frozen=True, slots=True)
class ScoredSetup:
    """Quality-scored setup candidate.

    Fields
    ------
    schema_version : str
        Must be exactly ``"ScoredSetup/v1"``.
    scored_id : str
        UUID v4.
    scored_at : str
        ISO 8601 UTC processing timestamp.
    scorer_version : str
        Version string of the scorer that produced this.
    setup : SetupCandidate
        Embedded, unchanged.
    core_quality_score : str | None
        Decimal string. Aggregated core quality score [0.0, 1.0].
        Null if no core module is SCORED.
    core_quality_grade : QualityGrade | None
        A_PLUS, A, B, C, or D. Null if core_quality_score is null.
    core_grade_thresholds : dict
        Snapshot of grade boundary values at scoring time.
    core_module_scores : dict[str, ModuleResult]
        Per-module scoring results for core modules.
    core_weights_snapshot : dict[str, str]
        One Decimal-string entry per key in core_module_scores.
    contextual_module_results : dict[str, ModuleResult]
        Per-module scoring results for contextual modules.
    contextual_weights_snapshot : dict[str, str]
        One Decimal-string entry per key in contextual_module_results.
    confluence_result : ConfluenceResult | None
        Null only if no confluence module is registered.
    module_features : dict
        Pre-computed features for reuse within scoring pass.
    """

    schema_version: str
    scored_id: str
    scored_at: str
    scorer_version: str
    setup: SetupCandidate
    core_quality_score: str | None
    core_quality_grade: QualityGrade | None
    core_grade_thresholds: dict
    core_module_scores: dict
    core_weights_snapshot: dict
    contextual_module_results: dict
    contextual_weights_snapshot: dict
    confluence_result: ConfluenceResult | None
    module_features: dict

    def __post_init__(self):
        # schema_version
        _require_str(self.schema_version, "schema_version")
        if self.schema_version != "ScoredSetup/v1":
            raise ValueError(
                f'schema_version must be "ScoredSetup/v1", '
                f"got {self.schema_version!r}"
            )

        # scored_id
        _require_str(self.scored_id, "scored_id")
        if len(self.scored_id) == 0:
            raise ValueError("scored_id must be a non-empty string")

        # scored_at
        _require_str(self.scored_at, "scored_at")
        if len(self.scored_at) == 0:
            raise ValueError("scored_at must be a non-empty string")

        # scorer_version
        _require_str(self.scorer_version, "scorer_version")
        if len(self.scorer_version) == 0:
            raise ValueError("scorer_version must be a non-empty string")

        # setup
        if not isinstance(self.setup, SetupCandidate):
            raise TypeError(
                "setup must be a SetupCandidate, "
                f"got {type(self.setup).__name__}"
            )

        # core_quality_score / core_quality_grade consistency
        if self.core_quality_score is not None:
            _require_decimal_str(
                self.core_quality_score, "core_quality_score"
            )
            if self.core_quality_grade is None:
                raise ValueError(
                    "core_quality_grade must be non-null when "
                    "core_quality_score is non-null"
                )
            if not isinstance(self.core_quality_grade, QualityGrade):
                raise TypeError(
                    "core_quality_grade must be a QualityGrade, "
                    f"got {type(self.core_quality_grade).__name__}"
                )
        else:
            if self.core_quality_grade is not None:
                raise ValueError(
                    "core_quality_grade must be null when "
                    "core_quality_score is null"
                )

        # core_grade_thresholds
        _require_dict(
            self.core_grade_thresholds, "core_grade_thresholds"
        )

        # core_module_scores
        _validate_module_scores_dict(
            self.core_module_scores, "core_module_scores"
        )

        # core_weights_snapshot
        _validate_weights_snapshot(
            self.core_weights_snapshot,
            "core_weights_snapshot",
            set(self.core_module_scores.keys()),
        )

        # contextual_module_results
        _validate_module_scores_dict(
            self.contextual_module_results, "contextual_module_results"
        )

        # contextual_weights_snapshot
        _validate_weights_snapshot(
            self.contextual_weights_snapshot,
            "contextual_weights_snapshot",
            set(self.contextual_module_results.keys()),
        )

        # INV-S-10: core and contextual key sets must be disjoint
        overlap = (
            set(self.core_module_scores.keys())
            & set(self.contextual_module_results.keys())
        )
        if overlap:
            raise ValueError(
                "core_module_scores and contextual_module_results "
                "must have disjoint key sets (INV-S-10), "
                f"overlapping keys: {sorted(overlap)}"
            )

        # confluence_result
        if self.confluence_result is not None:
            if not isinstance(self.confluence_result, ConfluenceResult):
                raise TypeError(
                    "confluence_result must be a ConfluenceResult, "
                    f"got {type(self.confluence_result).__name__}"
                )

        # module_features
        _require_dict(self.module_features, "module_features")

    def to_dict(self) -> dict:
        """Canonical JSON-compatible dict representation."""
        return {
            "schema_version": self.schema_version,
            "scored_id": self.scored_id,
            "scored_at": self.scored_at,
            "scorer_version": self.scorer_version,
            "setup": self.setup.to_dict(),
            "core_quality_score": self.core_quality_score,
            "core_quality_grade": (
                str(self.core_quality_grade)
                if self.core_quality_grade is not None
                else None
            ),
            "core_grade_thresholds": dict(self.core_grade_thresholds),
            "core_module_scores": {
                k: v.to_dict()
                for k, v in self.core_module_scores.items()
            },
            "core_weights_snapshot": dict(self.core_weights_snapshot),
            "contextual_module_results": {
                k: v.to_dict()
                for k, v in self.contextual_module_results.items()
            },
            "contextual_weights_snapshot": dict(
                self.contextual_weights_snapshot
            ),
            "confluence_result": (
                self.confluence_result.to_dict()
                if self.confluence_result is not None
                else None
            ),
            "module_features": dict(self.module_features),
        }
