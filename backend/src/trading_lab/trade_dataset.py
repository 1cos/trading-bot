"""Canonical BDRR Trade Dataset v1.

Ported from ``buildTradeDataset`` in estrategie/bdrr_trade_dataset.js.

The immutable collection of trade records produced by the Strategy Runner.

Public API:
    build_trade_dataset(strategy_runner_results) → dict
    DATASET_SCHEMA_VERSION = 'TradeDataset/v1'

Responsibilities:
    - Accept only Strategy Runner output
    - Validate record schema
    - Reject malformed records
    - Reject mixed-homogeneity inputs
    - Reject records missing engine_version
    - Preserve chronological ordering
    - Assign a deterministic dataset ID (SHA-256)
    - Expose records (all) and trades (candidate_id not None)
    - Expose summary metadata

Does NOT recompute strategy logic.

Run tests: python -m pytest backend/tests/test_trade_dataset.py
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from trading_lab.contracts.primitives import Rational

# ── Constants ────────────────────────────────────────────────────────────────

DATASET_SCHEMA_VERSION = "TradeDataset/v1"

_VALID_OUTCOMES = frozenset({
    "NO_VALID_SETUP",
    "ENTRY_NOT_TRIGGERED",
    "STOPPED",
    "TARGET_HIT",
    "AMBIGUOUS",
    "OPEN",
    "SESSION_CLOSE",
    "PIPELINE_FAILURE",
})

_REQUIRED_RECORD_FIELDS = (
    "run_record_id",
    "symbol",
    "session_date",
    "preset_id",
    "exit_target_r",
    "detection_status",
    "failure_stage",
    "failed_rules",
    "detection_result_id",
    "candidate_id",
    "confirmation_timestamp",
    "entry_timestamp",
    "first_evaluation_timestamp",
    "entry_price_ticks",
    "stop_price_ticks",
    "r2_price_ticks",
    "r3_price_ticks",
    "r4_price_ticks",
    "outcome",
    "realized_r",
    "highest_target_achieved",
    "exit_timestamp",
    "exit_price_ticks",
    "detection_result",
    "trade_plan",
    "trade_outcome",
)

_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Validation helpers ───────────────────────────────────────────────────────


def _is_valid_date_string(s):
    if not isinstance(s, str):
        return False
    return bool(_DATE_RE.match(s))


def _is_valid_timestamp_or_none(ts):
    if ts is None:
        return True
    if not isinstance(ts, str):
        return False
    try:
        # Match JS: new Date(ts).getTime() not NaN
        # Python's fromisoformat handles ISO 8601 strings
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def _is_uuid_v4(v):
    return isinstance(v, str) and bool(_UUID_V4_RE.match(v))


# ── Record schema validation ────────────────────────────────────────────────


def _validate_record(record, index):
    """Validate a single runner result record.

    Returns {"valid": True} or {"valid": False, "reason": str}.
    """
    if not isinstance(record, dict):
        return {"valid": False, "reason": f"record[{index}] is not an object"}

    for field in _REQUIRED_RECORD_FIELDS:
        if field not in record:
            return {
                "valid": False,
                "reason": f'record[{index}] missing required field "{field}"',
            }

    if not _is_uuid_v4(record["run_record_id"]):
        return {
            "valid": False,
            "reason": (
                f"record[{index}].run_record_id is not a valid UUID v4: "
                f"{record['run_record_id']}"
            ),
        }

    symbol = record["symbol"]
    if not isinstance(symbol, str) or symbol.strip() == "":
        return {
            "valid": False,
            "reason": f"record[{index}].symbol must be a non-empty string",
        }

    if not _is_valid_date_string(record["session_date"]):
        return {
            "valid": False,
            "reason": (
                f"record[{index}].session_date must be YYYY-MM-DD, "
                f"got: {record['session_date']}"
            ),
        }

    preset_id = record["preset_id"]
    if not isinstance(preset_id, str) or preset_id.strip() == "":
        return {
            "valid": False,
            "reason": f"record[{index}].preset_id must be a non-empty string",
        }

    etr = record["exit_target_r"]
    if isinstance(etr, Rational):
        if etr.numerator <= 0:
            return {
                "valid": False,
                "reason": (
                    f"record[{index}].exit_target_r Rational must be "
                    f"strictly positive, got: "
                    f"{etr.numerator}/{etr.denominator}"
                ),
            }
    elif etr not in (2, 3, 4):
        return {
            "valid": False,
            "reason": (
                f"record[{index}].exit_target_r must be 2, 3, or 4 "
                f"(v1) or a positive Rational (v2), "
                f"got: {record['exit_target_r']}"
            ),
        }

    ds = str(record["detection_status"])
    if ds not in ("VALID", "INVALID"):
        return {
            "valid": False,
            "reason": (
                f'record[{index}].detection_status must be "VALID" or '
                f'"INVALID", got: {record["detection_status"]}'
            ),
        }

    fr = record["failed_rules"]
    if not isinstance(fr, (list, tuple)):
        return {
            "valid": False,
            "reason": f"record[{index}].failed_rules must be an array",
        }

    outcome_str = str(record["outcome"])
    if outcome_str not in _VALID_OUTCOMES:
        return {
            "valid": False,
            "reason": (
                f'record[{index}].outcome "{outcome_str}" is not a valid '
                f"OUTCOME value"
            ),
        }

    for f in (
        "confirmation_timestamp",
        "entry_timestamp",
        "first_evaluation_timestamp",
        "exit_timestamp",
    ):
        if not _is_valid_timestamp_or_none(record[f]):
            return {
                "valid": False,
                "reason": (
                    f"record[{index}].{f} must be null or a valid ISO 8601 "
                    f"timestamp, got: {record[f]}"
                ),
            }

    cid = record["candidate_id"]
    if cid is not None and not _is_uuid_v4(cid):
        return {
            "valid": False,
            "reason": (
                f"record[{index}].candidate_id must be null or a valid "
                f"UUID v4, got: {cid}"
            ),
        }

    return {"valid": True}


# ── Duplicate detection ──────────────────────────────────────────────────────


def _find_duplicates(records):
    run_ids = set()
    candidate_ids = set()

    for i, r in enumerate(records):
        rid = r["run_record_id"]
        if rid in run_ids:
            return {
                "ok": False,
                "reason": f'duplicate run_record_id "{rid}" at index {i}',
            }
        run_ids.add(rid)

        cid = r["candidate_id"]
        if cid is not None:
            if cid in candidate_ids:
                return {
                    "ok": False,
                    "reason": f'duplicate candidate_id "{cid}" at index {i}',
                }
            candidate_ids.add(cid)

    return {"ok": True}


# ── Chronological ordering ───────────────────────────────────────────────────


def _ensure_chronological_order(records):
    for i in range(1, len(records)):
        prev = records[i - 1]["session_date"]
        curr = records[i]["session_date"]
        if curr < prev:
            return {
                "ok": False,
                "reason": (
                    f'records are not in chronological order: "{prev}" '
                    f'(index {i - 1}) followed by "{curr}" (index {i})'
                ),
            }
    return {"ok": True}


# ── Engine version extraction ────────────────────────────────────────────────


def _extract_engine_version(record):
    dr = record.get("detection_result")
    if dr is None:
        return None
    ev = getattr(dr, "engine_version", None)
    if ev is None and isinstance(dr, dict):
        ev = dr.get("engine_version")
    if isinstance(ev, str) and len(ev) > 0:
        return ev
    return None


# ── Homogeneous run validation ───────────────────────────────────────────────


def _validate_homogeneous(records):
    first = records[0]
    symbol = first["symbol"]
    preset_id = first["preset_id"]
    exit_target_r = first["exit_target_r"]

    first_ev = _extract_engine_version(first)
    if first_ev is None:
        return {
            "ok": False,
            "reason": (
                "homogeneity violation at index 0: engine_version is "
                "missing or empty (detection_result is null or "
                "engine_version is absent — PIPELINE_FAILURE records "
                "from the current runner cannot be included in "
                "TradeDataset/v1 until the runner populates "
                "engine_version on all record types)"
            ),
        }
    engine_version = first_ev

    for i in range(1, len(records)):
        r = records[i]

        if r["symbol"] != symbol:
            return {
                "ok": False,
                "reason": (
                    f'homogeneity violation at index {i}: symbol '
                    f'"{r["symbol"]}" differs from "{symbol}"'
                ),
            }

        if r["preset_id"] != preset_id:
            return {
                "ok": False,
                "reason": (
                    f'homogeneity violation at index {i}: preset_id '
                    f'"{r["preset_id"]}" differs from "{preset_id}"'
                ),
            }

        if r["exit_target_r"] != exit_target_r:
            return {
                "ok": False,
                "reason": (
                    f"homogeneity violation at index {i}: exit_target_r "
                    f"{r['exit_target_r']} differs from {exit_target_r}"
                ),
            }

        ev = _extract_engine_version(r)
        if ev is None:
            return {
                "ok": False,
                "reason": (
                    f"homogeneity violation at index {i}: engine_version "
                    f"is missing or empty (detection_result is null or "
                    f"engine_version is absent)"
                ),
            }
        if ev != engine_version:
            return {
                "ok": False,
                "reason": (
                    f'homogeneity violation at index {i}: engine_version '
                    f'"{ev}" differs from "{engine_version}"'
                ),
            }

    return {
        "ok": True,
        "symbol": symbol,
        "preset_id": preset_id,
        "exit_target_r": exit_target_r,
        "engine_version": engine_version,
    }


# ── Canonical serialization ──────────────────────────────────────────────────


def _to_serializable(val):
    """Convert Python contract objects and enums to JSON-serializable form.

    Recursively converts frozen dataclasses (with .to_dict()) and enums
    to plain dicts/strings so canonical_serialize can process them.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, (list, tuple)):
        return [_to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {k: _to_serializable(v) for k, v in val.items()}
    # Frozen dataclass with to_dict
    if hasattr(val, "to_dict"):
        return _to_serializable(val.to_dict())
    # Enum with .value
    if hasattr(val, "value"):
        return str(val)
    return str(val)


def _canonical_serialize(val):
    """Deterministic string representation matching JS canonicalSerialize.

    - null → "null"
    - booleans → "true"/"false"
    - numbers → JSON number representation
    - strings → JSON-quoted strings
    - arrays → elements in order, wrapped in []
    - objects → keys sorted lexicographically, wrapped in {}
    """
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        # Match JS JSON.stringify for floats
        return json.dumps(val)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, list):
        items = ",".join(_canonical_serialize(v) for v in val)
        return f"[{items}]"
    if isinstance(val, dict):
        keys = sorted(val.keys())
        pairs = ",".join(
            json.dumps(k) + ":" + _canonical_serialize(val[k])
            for k in keys
        )
        return "{" + pairs + "}"
    # Fallback
    return json.dumps(str(val))


def _derive_dataset_id(schema_version, engine_version, preset_id,
                        symbol, exit_target_r, records):
    """Compute deterministic 64-char hex SHA-256 dataset ID.

    Matches JS deriveDatasetId exactly.
    """
    # Canonical exit_target_r string: int for v1, "numerator/denominator" for Rational
    if isinstance(exit_target_r, Rational):
        etr_str = f"{exit_target_r.numerator}/{exit_target_r.denominator}"
    elif exit_target_r is not None:
        etr_str = str(exit_target_r)
    else:
        etr_str = ""

    header_parts = [
        schema_version,
        engine_version if engine_version is not None else "",
        preset_id if preset_id is not None else "",
        symbol if symbol is not None else "",
        etr_str,
    ]
    header = "\n".join(header_parts)

    canonical = header
    for r in records:
        serializable = _to_serializable(r)
        canonical += "\n" + _canonical_serialize(serializable)

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Metadata assembly ────────────────────────────────────────────────────────


def _build_metadata(dataset_id, records, trades, homogeneous):
    if len(records) == 0:
        return {
            "schema_version": DATASET_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "engine_version": None,
            "preset_id": None,
            "symbol": None,
            "exit_target_r": None,
            "date_range": {"first": None, "last": None},
            "session_count": 0,
            "trade_count": 0,
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S."
            ) + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
        }

    dates = sorted(r["session_date"] for r in records)

    now = datetime.now(timezone.utc)
    generated_at = (
        now.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{now.microsecond // 1000:03d}Z"
    )

    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "engine_version": homogeneous["engine_version"],
        "preset_id": homogeneous["preset_id"],
        "symbol": homogeneous["symbol"],
        "exit_target_r": homogeneous["exit_target_r"],
        "date_range": {"first": dates[0], "last": dates[-1]},
        "session_count": len(records),
        "trade_count": len(trades),
        "generated_at": generated_at,
    }


# ── Public API ───────────────────────────────────────────────────────────────


def build_trade_dataset(strategy_runner_results):
    """Build a Trade Dataset from Strategy Runner results.

    Mirrors JS ``buildTradeDataset(strategyRunnerResults)``.

    Parameters
    ----------
    strategy_runner_results : list
        List of result record dicts from ``run_bdrr_strategy()``.

    Returns
    -------
    dict
        Trade Dataset with schema_version, metadata, records, trades.

    Raises
    ------
    TypeError
        If input is not a list.
    ValueError
        If any record fails schema validation, duplicates detected,
        records not chronological, or records not homogeneous.
        (JS throws RangeError; Python uses ValueError as closest
        equivalent.)
    """
    # ── Input type check ─────────────────────────────────────────────────
    if not isinstance(strategy_runner_results, list):
        raise TypeError(
            "buildTradeDataset: strategyRunnerResults must be an array, "
            f"got: {type(strategy_runner_results).__name__}"
        )

    # ── Empty dataset ────────────────────────────────────────────────────
    if len(strategy_runner_results) == 0:
        dataset_id = _derive_dataset_id(
            DATASET_SCHEMA_VERSION, None, None, None, None, []
        )
        records = []
        trades = []
        metadata = _build_metadata(dataset_id, records, trades, None)
        return {
            "schema_version": DATASET_SCHEMA_VERSION,
            "metadata": metadata,
            "records": records,
            "trades": trades,
        }

    # ── Per-record schema validation ─────────────────────────────────────
    for i, rec in enumerate(strategy_runner_results):
        result = _validate_record(rec, i)
        if not result["valid"]:
            raise ValueError(
                "buildTradeDataset: schema validation failed — "
                + result["reason"]
            )

    # ── Duplicate detection ──────────────────────────────────────────────
    dup_check = _find_duplicates(strategy_runner_results)
    if not dup_check["ok"]:
        raise ValueError(
            "buildTradeDataset: duplicate ID detected — "
            + dup_check["reason"]
        )

    # ── Chronological ordering ───────────────────────────────────────────
    order_check = _ensure_chronological_order(strategy_runner_results)
    if not order_check["ok"]:
        raise ValueError(
            "buildTradeDataset: " + order_check["reason"]
        )

    # ── Homogeneous run validation ───────────────────────────────────────
    homo_check = _validate_homogeneous(strategy_runner_results)
    if not homo_check["ok"]:
        raise ValueError(
            "buildTradeDataset: " + homo_check["reason"]
        )

    # ── Deterministic dataset ID ─────────────────────────────────────────
    dataset_id = _derive_dataset_id(
        DATASET_SCHEMA_VERSION,
        homo_check["engine_version"],
        homo_check["preset_id"],
        homo_check["symbol"],
        homo_check["exit_target_r"],
        strategy_runner_results,
    )

    # ── Assemble collections ─────────────────────────────────────────────
    records = list(strategy_runner_results)
    trades = [r for r in strategy_runner_results if r.get("candidate_id") is not None]

    # ── Build metadata ───────────────────────────────────────────────────
    metadata = _build_metadata(dataset_id, records, trades, homo_check)

    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "metadata": metadata,
        "records": records,
        "trades": trades,
    }
