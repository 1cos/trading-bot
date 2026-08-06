"""Canonical BDRR contract types.

Re-exports the value types defined in
BDRR_ENGINE_CANONICAL_HANDOFF.md §3.2–§3.6.
"""

from trading_lab.contracts.bar import Bar
from trading_lab.contracts.confluence_result import ConfluenceResult
from trading_lab.contracts.detection_result import DetectionResult
from trading_lab.contracts.detector_audit_record import (
    CandidateStatus,
    DetectorAuditRecord,
)
from trading_lab.contracts.distances import (
    AbsoluteTickDistance,
    DirectionalTickDistance,
)
from trading_lab.contracts.entry_pattern import EntryPatternResult
from trading_lab.contracts.enums import (
    CandleAtrStatus,
    ConfluenceStatus,
    DetectionStatus,
    Direction,
    EntryPatternType,
    EvaluationStatus,
    FailedStage,
    LevelSource,
    Operator,
    QualityGrade,
    Stage,
    ValueType,
    ZoneStatus,
    ZoneType,
)
from trading_lab.contracts.module_result import ModuleResult
from trading_lab.contracts.primitives import (
    PriceTicks,
    Rational,
)
from trading_lab.contracts.rule_failure import (
    RejectionAttempt,
    RuleFailure,
)
from trading_lab.contracts.scored_setup import ScoredSetup
from trading_lab.contracts.session_metadata import SessionMetadata
from trading_lab.contracts.setup_candidate import SetupCandidate
from trading_lab.contracts.trade_outcome import TradeOutcome, TradeOutcomeStatus
from trading_lab.contracts.trade_outcome_v2 import TradeOutcomeV2, rational_to_label
from trading_lab.contracts.zone import CompositeZone, ZoneComponent
from trading_lab.contracts.trade_plan import EntryModel, TradePlan

__all__ = [
    "AbsoluteTickDistance",
    "Bar",
    "CandidateStatus",
    "CandleAtrStatus",
    "CompositeZone",
    "ConfluenceResult",
    "ConfluenceStatus",
    "DetectionResult",
    "DetectorAuditRecord",
    "DetectionStatus",
    "Direction",
    "DirectionalTickDistance",
    "EntryModel",
    "EntryPatternResult",
    "EntryPatternType",
    "EvaluationStatus",
    "FailedStage",
    "LevelSource",
    "ModuleResult",
    "Operator",
    "PriceTicks",
    "QualityGrade",
    "Rational",
    "RejectionAttempt",
    "RuleFailure",
    "ScoredSetup",
    "SessionMetadata",
    "SetupCandidate",
    "Stage",
    "TradeOutcome",
    "TradeOutcomeStatus",
    "TradeOutcomeV2",
    "TradePlan",
    "rational_to_label",
    "ValueType",
    "ZoneComponent",
    "ZoneStatus",
    "ZoneType",
]
