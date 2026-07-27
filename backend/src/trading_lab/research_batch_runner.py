"""Deterministic historical research batch runner.

Orchestrates:
    CSV text → csv_parser → session_split → Strategy Runner → ResearchDataset

Public API:
    build_research_dataset_from_csv(...)  → tuple[dict, ...]
    research_csv_from_csv(...)            → str
"""

from __future__ import annotations

from trading_lab.csv_parser import parse_candles_from_csv
from trading_lab.research_dataset import (
    build_research_rows,
    serialize_research_csv,
)
from trading_lab.session_split import split_into_sessions
from trading_lab.strategy_runner import run_bdrr_strategy


def build_research_dataset_from_csv(
    *,
    csv_text: str,
    symbol: str,
    preset: dict,
    config: dict,
    source_dataset_id: str,
    code_commit_hash: str,
) -> tuple[dict, ...]:
    """Run the full BDRR pipeline on historical CSV and return research rows.

    Parameters
    ----------
    csv_text : str
        Complete CSV content (repository 5-minute format).
    symbol : str
        Instrument symbol (e.g. ``"SPY"``).
    preset : dict
        Frozen Stage 1–5 preset configuration.
    config : dict
        Strategy Runner config: tick_size, engine_version, exit_target_r.
    source_dataset_id : str
        Caller-supplied dataset provenance identifier.
    code_commit_hash : str
        Caller-supplied code commit hash.

    Returns
    -------
    tuple[dict, ...]
        One flat research row per eligible valid setup.
    """
    if not isinstance(csv_text, str) or len(csv_text.strip()) == 0:
        raise ValueError("csv_text must be a non-empty string")
    if not isinstance(symbol, str) or len(symbol.strip()) == 0:
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(source_dataset_id, str) or len(source_dataset_id) == 0:
        raise ValueError("source_dataset_id must be a non-empty string")
    if not isinstance(code_commit_hash, str) or len(code_commit_hash) == 0:
        raise ValueError("code_commit_hash must be a non-empty string")

    timezone = preset.get("timezone") or "America/New_York"

    candles = parse_candles_from_csv(csv_text)
    groups = split_into_sessions(candles, timezone)

    sessions = [
        {
            "symbol": symbol,
            "date": g["date"],
            "market_timezone": timezone,
            "session_open_utc_ms": g["candles"][0]["time_ms"],
            "session_close_utc_ms": g["candles"][-1]["time_ms"],
            "timeframe": "5m",
            "candles": g["candles"],
        }
        for g in groups
    ]

    runner_results = run_bdrr_strategy(sessions, preset, config)

    return build_research_rows(
        runner_results,
        source_dataset_id=source_dataset_id,
        code_commit_hash=code_commit_hash,
    )


def research_csv_from_csv(
    *,
    csv_text: str,
    symbol: str,
    preset: dict,
    config: dict,
    source_dataset_id: str,
    code_commit_hash: str,
) -> str:
    """Run the full BDRR pipeline on historical CSV and return research CSV.

    Convenience wrapper: calls ``build_research_dataset_from_csv`` then
    ``serialize_research_csv``.
    """
    rows = build_research_dataset_from_csv(
        csv_text=csv_text,
        symbol=symbol,
        preset=preset,
        config=config,
        source_dataset_id=source_dataset_id,
        code_commit_hash=code_commit_hash,
    )
    return serialize_research_csv(rows)
