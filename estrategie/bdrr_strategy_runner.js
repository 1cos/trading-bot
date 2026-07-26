/**
 * estrategie/bdrr_strategy_runner.js
 *
 * BDRR Multi-Session Strategy Runner v1.
 *
 * Pure orchestrator — imports and invokes the canonical pipeline modules:
 *   bdrr_engine.js           Stage 1–5
 *   bdrr_detection_result.js DetectionResult/v1
 *   bdrr_trade_plan.js       TradePlan/v1
 *   bdrr_trade_outcome.js    TradeOutcome/v1
 *
 * Contains ZERO duplicated executable logic from any canonical module.
 * All tick arithmetic, detection, trade planning, and outcome evaluation
 * are delegated to the canonical modules.
 *
 * Pipeline per session:
 *   1. Run Stage 1–5 (buildSessionContext → buildORB → findBreak →
 *      findDisplacement → findRetestWindow → findRejection).
 *   2. Build DetectionResult/v1 via buildDetectionResult().
 *   3. If VALID: build TradePlan/v1 via buildTradePlan().
 *   4. If VALID: evaluate TradeOutcome/v1 via evaluateTradeOutcome().
 *   5. Produce one frozen result record.
 *
 * Run tests: node estrategie/test_bdrr_strategy_runner.js
 */

'use strict';

const {
  buildSessionContext,
  buildORB,
  findBreak,
  findDisplacement,
  findRetestWindow,
  findRejection,
  priceToTicks          // canonical tick conversion — used only for bar conversion
} = require('./bdrr_engine.js');

const { buildDetectionResult } = require('./bdrr_detection_result.js');
const { buildTradePlan }       = require('./bdrr_trade_plan.js');
const { evaluateTradeOutcome } = require('./bdrr_trade_outcome.js');

// ── UUID v4 (record-level only — not duplicating detection_result's UUID) ────

function uuidv4() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ── Deep freeze ──────────────────────────────────────────────────────────────

function deepFreeze(obj) {
  if (obj === null || typeof obj !== 'object') return obj;
  Object.freeze(obj);
  for (const key of Object.getOwnPropertyNames(obj)) {
    const val = obj[key];
    if (val !== null && typeof val === 'object' && !Object.isFrozen(val)) {
      deepFreeze(val);
    }
  }
  return obj;
}

// ── Outcome category enum ────────────────────────────────────────────────────

const OUTCOME = Object.freeze({
  NO_VALID_SETUP:      'NO_VALID_SETUP',
  ENTRY_NOT_TRIGGERED: 'ENTRY_NOT_TRIGGERED',
  STOPPED:             'STOPPED',
  TARGET_HIT:          'TARGET_HIT',
  AMBIGUOUS:           'AMBIGUOUS',
  OPEN:                'OPEN',
  PIPELINE_FAILURE:    'PIPELINE_FAILURE'
});

// ── CSV / session helpers (data wrangling, not executable pipeline logic) ─────

/**
 * Parse the repository 5-minute CSV format into raw candle objects.
 * CSV lines 0–2 are headers; line 3+ is data:
 *   datetime,close,high,low,open,volume
 */
function parseCandlesFromCSV(csvContent) {
  const lines = csvContent.trim().split('\n');
  const candles = [];
  for (let i = 3; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const cols = line.split(',');
    if (cols.length < 5) continue;
    const close = parseFloat(cols[1]);
    if (isNaN(close)) continue;
    candles.push({
      time:  new Date(cols[0].trim().replace(' ', 'T')),
      close,
      high:  parseFloat(cols[2]),
      low:   parseFloat(cols[3]),
      open:  parseFloat(cols[4])
    });
  }
  return candles;
}

/**
 * Group candles by ET calendar date into session objects.
 */
function splitIntoSessions(candles, timezone) {
  const dateFmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'
  });
  const map = new Map();
  for (const c of candles) {
    const d = dateFmt.format(c.time);
    if (!map.has(d)) map.set(d, []);
    map.get(d).push(c);
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, sessionCandles]) => ({ date, candles: sessionCandles }));
}

/**
 * Convert a raw engine candle into a canonical Bar object suitable for
 * evaluateTradeOutcome(). Uses priceToTicks imported from bdrr_engine.js
 * — no tick arithmetic is duplicated.
 */
function rawCandleToCanonicalBar(candle, tickSize) {
  const ms = candle.time instanceof Date ? candle.time.getTime() : candle.time;
  return {
    bar_utc_ms: ms,
    open:  { ticks: priceToTicks(candle.open,  tickSize), tick_size: tickSize },
    high:  { ticks: priceToTicks(candle.high,  tickSize), tick_size: tickSize },
    low:   { ticks: priceToTicks(candle.low,   tickSize), tick_size: tickSize },
    close: { ticks: priceToTicks(candle.close, tickSize), tick_size: tickSize }
  };
}

// ── Primary export ───────────────────────────────────────────────────────────

/**
 * runBdrrStrategy(sessions, preset, config)
 *
 * @param {Array} sessions - Ordered array of session objects, each containing:
 *   symbol, date, market_timezone, session_open_utc_ms, session_close_utc_ms,
 *   timeframe, candles
 * @param {object} preset  - Frozen Stage 1–5 preset configuration
 * @param {object} config  - { tick_size, engine_version, exit_target_r }
 * @returns {Array} Frozen array of result records, one per session
 */
function runBdrrStrategy(sessions, preset, config) {
  // ── Input validation ──────────────────────────────────────────────────────
  if (!Array.isArray(sessions)) {
    throw new TypeError('sessions must be an array');
  }
  if (!preset || typeof preset !== 'object') {
    throw new TypeError('preset must be a non-null object');
  }
  if (!config || typeof config !== 'object') {
    throw new TypeError('config must be a non-null object');
  }
  if (![2, 3, 4].includes(config.exit_target_r)) {
    throw new TypeError('config.exit_target_r must be 2, 3, or 4');
  }
  if (typeof config.tick_size !== 'number' || !isFinite(config.tick_size) || config.tick_size <= 0) {
    throw new TypeError('config.tick_size must be a positive finite number');
  }
  if (typeof config.engine_version !== 'string' || config.engine_version.length === 0) {
    throw new TypeError('config.engine_version must be a non-empty string');
  }

  const tickSize = config.tick_size;

  // Build the engine config from preset (passed to Stage 1–5 functions)
  const engineConfig = {
    timeframe_minutes:          preset.timeframe_minutes || 5,
    timezone:                   preset.timezone || 'America/New_York',
    session_open:               preset.session_open || '09:30',
    orb_start:                  preset.orb_start || 'session_open',
    orb_duration_minutes:       preset.orb_duration_minutes || 5,
    level_source:               preset.level_source || 'ORB_HIGH',
    direction:                  preset.direction || 'LONG',
    tick_size:                  tickSize,
    min_displacement_ticks:     preset.min_displacement_ticks != null ? preset.min_displacement_ticks : null,
    min_penetration_ticks:      preset.min_penetration_ticks != null ? preset.min_penetration_ticks : null,
    min_close_beyond_level_ticks: preset.min_close_beyond_level_ticks != null ? preset.min_close_beyond_level_ticks : null
  };

  // TradePlan config (passed to buildTradePlan)
  const tradePlanConfig = {
    direction:          preset.direction || 'LONG',
    entry_model:        preset.entry_model || 'CONFIRMATION_CLOSE',
    entry_buffer_ticks: preset.entry_buffer_ticks != null ? preset.entry_buffer_ticks : 0,
    stop_buffer_ticks:  preset.stop_buffer_ticks != null ? preset.stop_buffer_ticks : 0,
    tick_size:          tickSize
  };

  // TradeOutcome config
  const outcomeConfig = {
    direction:      preset.direction || 'LONG',
    exit_target_r:  config.exit_target_r
  };

  const results = [];

  for (const session of sessions) {
    const record = processOneSession(
      session, preset, engineConfig, tradePlanConfig, outcomeConfig, config
    );
    results.push(deepFreeze(record));
  }

  return Object.freeze(results);
}

// ── Single-session pipeline ──────────────────────────────────────────────────

function processOneSession(session, preset, engineConfig, tradePlanConfig, outcomeConfig, config) {
  const runRecordId = uuidv4();
  const tickSize = config.tick_size;

  // Session metadata for DetectionResult
  const sessionMeta = {
    symbol:               session.symbol,
    date:                 session.date,
    market_timezone:      session.market_timezone,
    session_open_utc_ms:  session.session_open_utc_ms,
    session_close_utc_ms: session.session_close_utc_ms,
    timeframe_seconds:    session.timeframe === '5m' ? 300
                          : (typeof session.timeframe === 'number' ? session.timeframe : 300)
  };

  const drMetadata = {
    tick_size:       tickSize,
    session:         sessionMeta,
    preset_id:       preset.preset_id || 'default',
    engine_version:  config.engine_version
  };

  // ── Validate candles ───────────────────────────────────────────────────
  if (!Array.isArray(session.candles) || session.candles.length === 0) {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, null, null, null, 'INVALID_SESSION_INPUT',
      'session contains no candles');
  }

  // ── Stage 1a: Session Context ──────────────────────────────────────────
  let sessionContext;
  try {
    sessionContext = buildSessionContext(session.candles, engineConfig);
  } catch (e) {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, null, null, null, 'INVALID_SESSION_INPUT',
      'buildSessionContext threw: ' + e.message);
  }
  if (sessionContext.status !== 'OK') {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, null, null, null,
      sessionContext.failed_stage, sessionContext.reason);
  }

  const candles = sessionContext.candles;

  // ── Stage 1b: ORB ─────────────────────────────────────────────────────
  const orbResult = buildORB(candles, sessionContext, engineConfig);

  // ── Stage 2: Break ────────────────────────────────────────────────────
  const breakResult = orbResult.status === 'OK'
    ? findBreak(candles, orbResult, engineConfig)
    : { status: 'FAILED', failed_stage: orbResult.failed_stage, reason: orbResult.reason };

  // ── Stage 3: Displacement ─────────────────────────────────────────────
  const dispResult = breakResult.status === 'OK'
    ? findDisplacement(candles, orbResult, breakResult, engineConfig)
    : { status: 'FAILED', failed_stage: breakResult.failed_stage, reason: breakResult.reason };

  // ── Stage 4: Retest Window ────────────────────────────────────────────
  const retestResult = dispResult.status === 'OK'
    ? findRetestWindow(candles, orbResult, breakResult, dispResult, engineConfig)
    : { status: 'FAILED', failed_stage: dispResult.failed_stage, reason: dispResult.reason };

  // ── Stage 5: Rejection ────────────────────────────────────────────────
  const rejResult = retestResult.status === 'OK'
    ? findRejection(candles, orbResult, breakResult, dispResult, retestResult, engineConfig)
    : { status: 'FAILED', failed_stage: retestResult.failed_stage, reason: retestResult.reason };

  // ── Build DetectionResult/v1 (canonical module) ───────────────────────
  const drBuild = buildDetectionResult(
    { orb: orbResult, breakResult, dispResult, retestResult, rejResult },
    drMetadata
  );

  if (drBuild.status !== 'OK') {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, null, null, null,
      drBuild.failure_code, drBuild.reason);
  }

  const detectionResult = drBuild.detection_result;

  // ── INVALID detection → NO_VALID_SETUP ────────────────────────────────
  if (detectionResult.status !== 'VALID') {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.NO_VALID_SETUP, detectionResult, null, null,
      detectionResult.failed_stage, null);
  }

  // ── VALID detection → build TradePlan/v1 (canonical module) ───────────
  // buildTradePlan expects a canonical DetectionResult/v1
  const tpBuild = buildTradePlan(detectionResult, tradePlanConfig);

  if (tpBuild.status !== 'OK') {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, detectionResult, null, null,
      tpBuild.failure_code, tpBuild.reason);
  }

  const tradePlan = tpBuild.trade_plan;

  // ── Build post-confirmation canonical bars ────────────────────────────
  // evaluateTradeOutcome expects canonical Bar[] (PriceTicks OHLC).
  // We convert raw candles strictly after the confirmation bar using
  // priceToTicks imported from bdrr_engine.js — no tick conversion logic
  // is duplicated.
  const confIdx = rejResult.confirmation_candle_index;
  const postConfBars = [];
  for (let i = confIdx + 1; i < candles.length; i++) {
    postConfBars.push(rawCandleToCanonicalBar(candles[i], tickSize));
  }

  // ── Evaluate TradeOutcome/v1 (canonical module) ───────────────────────
  const toBuild = evaluateTradeOutcome(
    detectionResult, tradePlan, postConfBars, outcomeConfig
  );

  if (toBuild.status !== 'OK') {
    return buildResultRecord(runRecordId, sessionMeta, preset, config,
      OUTCOME.PIPELINE_FAILURE, detectionResult, tradePlan, null,
      toBuild.failure_code, toBuild.reason);
  }

  const tradeOutcome = toBuild.outcome;

  // ── Map TradeOutcome outcome to runner OUTCOME ────────────────────────
  const outcomeMap = {
    'STOPPED':              OUTCOME.STOPPED,
    'TARGET_HIT':           OUTCOME.TARGET_HIT,
    'AMBIGUOUS':            OUTCOME.AMBIGUOUS,
    'OPEN':                 OUTCOME.OPEN,
    'ENTRY_NOT_TRIGGERED':  OUTCOME.ENTRY_NOT_TRIGGERED
  };
  const runnerOutcome = outcomeMap[tradeOutcome.outcome] || OUTCOME.PIPELINE_FAILURE;

  // ── Assemble full result record ───────────────────────────────────────
  const candidateId = uuidv4();

  return {
    run_record_id:             runRecordId,
    symbol:                    sessionMeta.symbol,
    session_date:              sessionMeta.date,
    preset_id:                 preset.preset_id || 'default',
    exit_target_r:             config.exit_target_r,

    detection_status:          'VALID',
    failure_stage:             null,
    failed_rules:              [],
    detection_result_id:       detectionResult.result_id,

    candidate_id:              candidateId,
    confirmation_timestamp:    tradeOutcome.entry_bar_utc_ms
                                 ? new Date(tradeOutcome.entry_bar_utc_ms).toISOString()
                                 : null,
    entry_timestamp:           tradeOutcome.entry_bar_utc_ms
                                 ? new Date(tradeOutcome.entry_bar_utc_ms).toISOString()
                                 : null,
    first_evaluation_timestamp: tradeOutcome.first_eval_bar_utc_ms
                                 ? new Date(tradeOutcome.first_eval_bar_utc_ms).toISOString()
                                 : null,

    entry_price_ticks:         tradePlan.entry_price.ticks,
    stop_price_ticks:          tradePlan.stop_price.ticks,
    r2_price_ticks:            tradePlan.r2_price.ticks,
    r3_price_ticks:            tradePlan.r3_price.ticks,
    r4_price_ticks:            tradePlan.r4_price.ticks,

    outcome:                   runnerOutcome,
    realized_r:                tradeOutcome.realized_r,
    highest_target_achieved:   tradeOutcome.highest_target_achieved,
    exit_timestamp:            tradeOutcome.exit_bar_utc_ms
                                 ? new Date(tradeOutcome.exit_bar_utc_ms).toISOString()
                                 : null,
    exit_price_ticks:          tradeOutcome.exit_price_ticks,

    detection_result:          detectionResult,
    trade_plan:                tradePlan,
    trade_outcome:             tradeOutcome
  };
}

// ── Result record builder for non-VALID / pipeline-failure paths ─────────────

function buildResultRecord(runRecordId, sessionMeta, preset, config,
  outcome, detectionResult, tradePlan, tradeOutcome, failureStage, reason) {
  return {
    run_record_id:             runRecordId,
    symbol:                    sessionMeta.symbol,
    session_date:              sessionMeta.date,
    preset_id:                 preset.preset_id || 'default',
    exit_target_r:             config.exit_target_r,

    detection_status:          detectionResult ? detectionResult.status : 'INVALID',
    failure_stage:             failureStage || null,
    failed_rules:              detectionResult ? (detectionResult.failed_rules || []) : [],
    detection_result_id:       detectionResult ? detectionResult.result_id : null,

    candidate_id:              null,
    confirmation_timestamp:    null,
    entry_timestamp:           null,
    first_evaluation_timestamp: null,

    entry_price_ticks:         null,
    stop_price_ticks:          null,
    r2_price_ticks:            null,
    r3_price_ticks:            null,
    r4_price_ticks:            null,

    outcome:                   outcome,
    realized_r:                null,
    highest_target_achieved:   null,
    exit_timestamp:            null,
    exit_price_ticks:          null,

    detection_result:          detectionResult,
    trade_plan:                tradePlan,
    trade_outcome:             tradeOutcome
  };
}

module.exports = {
  runBdrrStrategy,
  parseCandlesFromCSV,
  splitIntoSessions,
  OUTCOME,
  deepFreeze,
  uuidv4
};
