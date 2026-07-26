# Trading Lab — Python Backend

## Status

Initial project structure. No strategy logic has been ported yet.

## Relationship to the JavaScript BDRR Engine

The existing JavaScript BDRR engine (`estrategie/bdrr_engine.js` and related
modules) is the frozen, validated reference implementation. Its oracle fixtures
(`dati/bdrr_spy_oracle.json`, `dati/bdrr_qqq_oracle.json`) and regression test
suite define the canonical expected behavior for every detection stage.

Python will become the production implementation of the BDRR strategy engine.
Python behavior must be validated against the existing oracle fixtures to
confirm parity with the JavaScript reference before any JavaScript code is
retired.

## Development

```bash
cd backend
pip install -e ".[dev]"
pytest
```

Requires Python >= 3.11.
