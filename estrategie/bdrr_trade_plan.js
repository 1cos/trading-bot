/**
 * estrategie/bdrr_trade_plan.js
 *
 * BDRR Trade Plan construction — TradePlan/v1 only.
 *
 * Exports one primary function:
 *   buildTradePlan(detectionResult, config)
 *
 * This module is deliberately isolated from bdrr_engine.js:
 *   - No import of Stage 1–5 detection logic.
 *   - No market data access of any kind.
 *   - No inspection of candles after the confirmation bar.
 *   - No outcome evaluation.
 *   - No mutation of caller-owned objects.
 *
 * Frozen output contract: TradePlan/v1
 *   All price/distance values are integer tick counts stored alongside their
 *   tick_size. No raw floating-point prices are stored in the contract.
 *
 * Supported configuration (initial preset):
 *   direction:           LONG   (SHORT → structured UNSUPPORTED_DIRECTION failure)
 *   entry_model:         CONFIRMATION_CLOSE | BREAK_OF_SIGNAL_BAR
 *   entry_buffer_ticks:  integer >= 0
 *   stop_buffer_ticks:   integer >= 0
 *   tick_size:           finite positive number
 *
 * Run tests: node estrategie/test_bdrr_trade_plan.js
 */

'use strict';

// ── Tick arithmetic (private, mirrors bdrr_engine.js — not imported from it
//   to keep this module independent) ─────────────────────────────────────────

function priceToTicks(price, tickSize) {
  // Converts a floating-point price to the nearest integer tick count.
  // Identical algorithm to bdrr_engine.js priceToTicks.
  return Math.round(price / tickSize);
}

function ticksToPoints(ticks, tickSize) {
  // Converts an integer tick count back to a rounded decimal price string.
  const s = String(tickSize);
  const dot = s.indexOf('.');
  const decimals = dot === -1 ? 0 : s.length - dot - 1;
  return Number((ticks * tickSize).toFixed(Math.max(decimals, 2)));
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function isNonNegativeInteger(v) {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0;
}

function isPositiveFiniteNumber(v) {
  return typeof v === 'number' && isFinite(v) && v > 0;
}

function fail(code, reason) {
  return { status: 'FAILED', failure_code: code, reason };
}

// Build one PriceTicks-shaped object (ticks + tick_size).
function priceTicks(ticks, tickSize) {
  return { ticks, tick_size: tickSize };
}

// ── Input validation ─────────────────────────────────────────────────────────

function validateDetectionResult(dr) {
  if (!dr || typeof dr !== 'object') {
    return fail('INVALID_DETECTION_RESULT', 'detectionResult must be a non-null object');
  }
  if (dr.status !== 'OK') {
    return fail(
      'INVALID_DETECTION_RESULT',
      `detectionResult.status must be "OK" (VALID detection); got "${dr.status}"` +
        (dr.failed_stage ? ` (failed_stage: ${dr.failed_stage})` : '')
    );
  }
  return null; // no error
}

function validateConfig(config) {
  if (!config || typeof config !== 'object') {
    return fail('INVALID_DETECTION_RESULT', 'config must be a non-null object');
  }

  // Direction
  if (config.direction !== 'LONG') {
    return fail(
      'UNSUPPORTED_DIRECTION',
      `direction "${config.direction}" is not supported; only "LONG" is implemented`
    );
  }

  // Entry model
  if (
    config.entry_model !== 'CONFIRMATION_CLOSE' &&
    config.entry_model !== 'BREAK_OF_SIGNAL_BAR'
  ) {
    return fail(
      'UNSUPPORTED_ENTRY_MODEL',
      `entry_model "${config.entry_model}" is not recognized; ` +
        'supported values: CONFIRMATION_CLOSE, BREAK_OF_SIGNAL_BAR'
    );
  }

  // Tick size
  if (!isPositiveFiniteNumber(config.tick_size)) {
    return fail('TICK_SIZE_MISMATCH', 'config.tick_size must be a finite positive number');
  }

  // Buffers
  if (!isNonNegativeInteger(config.entry_buffer_ticks)) {
    return fail(
      'INVALID_BUFFER',
      `entry_buffer_ticks must be a non-negative integer; got ${config.entry_buffer_ticks}`
    );
  }
  if (!isNonNegativeInteger(config.stop_buffer_ticks)) {
    return fail(
      'INVALID_BUFFER',
      `stop_buffer_ticks must be a non-negative integer; got ${config.stop_buffer_ticks}`
    );
  }

  return null;
}

function validateConfirmationBar(dr, tickSize) {
  const bar = dr.confirmation_candle;
  if (!bar || typeof bar !== 'object') {
    return fail('MISSING_CONFIRMATION_BAR', 'detectionResult.confirmation_candle is missing or not an object');
  }

  // Every required OHLC field must be a finite number (raw float from the engine)
  for (const field of ['open', 'high', 'low', 'close']) {
    if (typeof bar[field] !== 'number' || !isFinite(bar[field])) {
      return fail(
        'INVALID_TICK_VALUE',
        `confirmation_candle.${field} must be a finite number; got ${bar[field]}`
      );
    }
  }

  // After converting to ticks, each must be an integer (Math.round always
  // returns an integer for finite inputs, so this is a double-check on the
  // tick_size consistency — if tick_size is wrong, tick counts may be wildly off).
  for (const field of ['open', 'high', 'low', 'close']) {
    const ticks = priceToTicks(bar[field], tickSize);
    if (!Number.isInteger(ticks)) {
      return fail(
        'INVALID_TICK_VALUE',
        `confirmation_candle.${field} (${bar[field]}) does not convert to an integer ` +
          `number of ticks at tick_size ${tickSize}; got ${ticks}`
      );
    }
  }

  return null;
}

// ── Primary export ───────────────────────────────────────────────────────────

/**
 * buildTradePlan(detectionResult, config)
 *
 * Constructs a frozen TradePlan/v1 object from a successful detection result.
 *
 * @param {object} detectionResult  Result of findRejection() with status 'OK'.
 *   Must carry a confirmation_candle with finite open/high/low/close.
 * @param {object} config           Preset configuration object.
 *   Required keys: direction, entry_model, entry_buffer_ticks,
 *                  stop_buffer_ticks, tick_size.
 *
 * @returns {{ status: 'OK', trade_plan: TradePlan/v1 }}
 *        | {{ status: 'FAILED', failure_code: string, reason: string }}
 *
 * Never throws for normal validation failures.
 * Never modifies detectionResult or config.
 * Never reads market data.
 * Never inspects candles after the confirmation bar.
 */
function buildTradePlan(detectionResult, config) {
  // ── Step 1: validate inputs ────────────────────────────────────────────────

  const drErr = validateDetectionResult(detectionResult);
  if (drErr) return drErr;

  const cfgErr = validateConfig(config);
  if (cfgErr) return cfgErr;

  const tickSize = config.tick_size;

  const barErr = validateConfirmationBar(detectionResult, tickSize);
  if (barErr) return barErr;

  // ── Step 2: convert confirmation bar OHLC to integer ticks ────────────────

  const bar = detectionResult.confirmation_candle;
  const highTicks  = priceToTicks(bar.high,  tickSize);
  const lowTicks   = priceToTicks(bar.low,   tickSize);
  const closeTicks = priceToTicks(bar.close, tickSize);

  // ── Step 3: compute entry price ───────────────────────────────────────────

  let entryTicks;
  if (config.entry_model === 'CONFIRMATION_CLOSE') {
    // LONG: entry = close + entry_buffer_ticks (favorable direction is upward)
    entryTicks = closeTicks + config.entry_buffer_ticks;
  } else {
    // BREAK_OF_SIGNAL_BAR — LONG: entry = high + entry_buffer_ticks
    entryTicks = highTicks + config.entry_buffer_ticks;
  }

  // ── Step 4: compute stop price ────────────────────────────────────────────

  // LONG: stop = low - stop_buffer_ticks
  const stopTicks = lowTicks - config.stop_buffer_ticks;

  // ── Step 5: validate geometric relationship ───────────────────────────────

  // LONG: entry must be strictly above stop
  if (entryTicks <= stopTicks) {
    return fail(
      'INVALID_RISK',
      `LONG entry (${entryTicks} ticks) must be strictly above stop (${stopTicks} ticks); ` +
        'check confirmation_candle geometry or buffer configuration'
    );
  }

  // ── Step 6: compute risk ──────────────────────────────────────────────────

  const riskTicks = Math.abs(entryTicks - stopTicks); // always positive given step 5

  if (riskTicks === 0) {
    // Redundant after step 5 but explicit for the INVALID_RISK contract.
    return fail('INVALID_RISK', 'calculated risk is zero ticks; entry and stop are identical');
  }

  // ── Step 7: compute targets (integer tick arithmetic only) ────────────────

  const r2Ticks = entryTicks + 2 * riskTicks;
  const r3Ticks = entryTicks + 3 * riskTicks;
  const r4Ticks = entryTicks + 4 * riskTicks;

  // ── Step 8: assemble TradePlan/v1 ─────────────────────────────────────────

  const trade_plan = {
    schema_version:      'TradePlan/v1',
    entry_model:         config.entry_model,
    entry_buffer_ticks:  config.entry_buffer_ticks,
    stop_buffer_ticks:   config.stop_buffer_ticks,
    tick_size:           tickSize,
    entry_price:         priceTicks(entryTicks,  tickSize),
    stop_price:          priceTicks(stopTicks,   tickSize),
    risk:                priceTicks(riskTicks,   tickSize),
    r2_price:            priceTicks(r2Ticks,     tickSize),
    r3_price:            priceTicks(r3Ticks,     tickSize),
    r4_price:            priceTicks(r4Ticks,     tickSize)
  };

  return { status: 'OK', trade_plan };
}

module.exports = { buildTradePlan };
