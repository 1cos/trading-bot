/**
 * estrategie/bdrr_trade_dataset.js
 *
 * BDRR Trade Dataset v1.
 *
 * The immutable collection of trade records produced by the Strategy Runner.
 * This becomes the single source of truth for future UI, statistics,
 * optimization, trade browser, clickable charts, and exports.
 *
 * Public API:
 *   buildTradeDataset(strategyRunnerResults)
 *
 * Responsibilities:
 *   - Accept only Strategy Runner output
 *   - Validate record schema
 *   - Reject malformed records
 *   - Reject mixed-homogeneity inputs (symbol / preset_id / exit_target_r /
 *     engine_version must be identical across all records)
 *   - Reject any record whose engine_version cannot be extracted (no exemptions)
 *   - Preserve chronological ordering
 *   - Assign a deterministic, stable dataset ID derived from complete record content
 *   - Expose immutable collections: records (all) and trades (candidates only)
 *   - Expose summary metadata only
 *
 * Does NOT recompute strategy logic.
 *
 * ── Dataset ID derivation ────────────────────────────────────────────────────
 *
 * dataset_id is a 64-char hex SHA-256 digest derived deterministically from the
 * complete canonical content of every ordered Strategy Runner record, combined
 * with dataset-level identity fields.
 *
 * Canonical content is built as follows:
 *
 *   1. Header line:
 *        schema_version\nengine_version\npreset_id\nsymbol\nexit_target_r
 *
 *   2. For each record in input order, append a newline then the output of
 *      canonicalSerialize(record):
 *        - all enumerable own fields of the record are included
 *        - object keys are recursively sorted lexicographically
 *        - array element order is preserved
 *        - null, booleans, numbers, and strings are serialized exactly
 *        - no fields are excluded from records
 *
 *   generated_at (a metadata field, not a record field) is excluded because
 *   it is produced by buildTradeDataset itself, not by the Strategy Runner.
 *
 * The same ordered Strategy Runner records with the same content always produce
 * the same dataset_id, regardless of JavaScript object key insertion order.
 *
 * For an empty input the header alone is hashed (no record lines appended).
 *
 * ── Engine-version contract ──────────────────────────────────────────────────
 *
 * Every record in a non-empty dataset must supply a non-empty engine_version
 * via detection_result.engine_version.  No record type is exempted.
 *
 * The current Strategy Runner (bdrr_strategy_runner.js v1) sets
 * detection_result to null for PIPELINE_FAILURE records, making
 * engine_version unavailable.  Until the runner populates engine_version
 * on all record types, PIPELINE_FAILURE records cannot be included in a
 * TradeDataset/v1.  This is a known runner integration issue; the dataset
 * layer enforces the contract strictly rather than masking the gap.
 *
 * ── Collections ─────────────────────────────────────────────────────────────
 *
 * records — all Strategy Runner records (including NO_VALID_SETUP)
 * trades  — only records where candidate_id !== null
 *
 * ── Metadata ─────────────────────────────────────────────────────────────────
 *
 * session_count === records.length
 * trade_count   === trades.length
 *
 * Run tests: node estrategie/test_bdrr_trade_dataset.js
 */

'use strict';

const crypto = require('crypto');

// ── Constants ─────────────────────────────────────────────────────────────────

const DATASET_SCHEMA_VERSION = 'TradeDataset/v1';

/** Valid outcome values produced by the Strategy Runner (OUTCOME enum). */
const VALID_OUTCOMES = new Set([
  'NO_VALID_SETUP',
  'ENTRY_NOT_TRIGGERED',
  'STOPPED',
  'TARGET_HIT',
  'AMBIGUOUS',
  'OPEN',
  'PIPELINE_FAILURE'
]);

/** Required top-level fields on every Strategy Runner result record. */
const REQUIRED_RECORD_FIELDS = [
  'run_record_id',
  'symbol',
  'session_date',
  'preset_id',
  'exit_target_r',
  'detection_status',
  'failure_stage',
  'failed_rules',
  'detection_result_id',
  'candidate_id',
  'confirmation_timestamp',
  'entry_timestamp',
  'first_evaluation_timestamp',
  'entry_price_ticks',
  'stop_price_ticks',
  'r2_price_ticks',
  'r3_price_ticks',
  'r4_price_ticks',
  'outcome',
  'realized_r',
  'highest_target_achieved',
  'exit_timestamp',
  'exit_price_ticks',
  'detection_result',
  'trade_plan',
  'trade_outcome'
];

/** UUID v4 regex (RFC 4122). */
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// ── Canonical serialization ───────────────────────────────────────────────────

/**
 * canonicalSerialize(val)
 *
 * Produces a deterministic string representation of any JSON-compatible value:
 *   - null, booleans, numbers, strings: serialized with JSON.stringify
 *   - arrays: elements serialized in original order, wrapped in []
 *   - objects: own enumerable keys sorted lexicographically, values serialized
 *     recursively, wrapped in {}
 *
 * Key-insertion order does not affect the output.
 * This function does not produce a human-readable format; it is a stable
 * fingerprint input only.
 */
function canonicalSerialize(val) {
  if (val === null || typeof val !== 'object') {
    // primitive or null: JSON.stringify gives the exact representation
    return JSON.stringify(val);
  }
  if (Array.isArray(val)) {
    return '[' + val.map(canonicalSerialize).join(',') + ']';
  }
  // Plain object: sort keys for determinism
  const keys = Object.keys(val).sort();
  const pairs = keys.map(k => JSON.stringify(k) + ':' + canonicalSerialize(val[k]));
  return '{' + pairs.join(',') + '}';
}

// ── Deterministic dataset ID ──────────────────────────────────────────────────

/**
 * deriveDatasetId(schemaVersion, engineVersion, presetId, symbol,
 *                 exitTargetR, records)
 *
 * Returns a deterministic 64-char hex SHA-256 digest identifying the exact
 * complete content of this ordered record set.
 *
 * Hash input:
 *   header = schema_version\nengine_version\npreset_id\nsymbol\nexit_target_r
 *   For each record: \n + canonicalSerialize(record)
 *
 * generated_at is a metadata field produced by buildTradeDataset itself and
 * is NOT part of any record; it does not appear in the hash input.
 */
function deriveDatasetId(schemaVersion, engineVersion, presetId, symbol,
                         exitTargetR, records) {
  const header = [
    schemaVersion,
    engineVersion !== null ? engineVersion  : '',
    presetId      !== null ? presetId       : '',
    symbol        !== null ? symbol         : '',
    exitTargetR   !== null ? String(exitTargetR) : ''
  ].join('\n');

  // Append canonical serialization of each record
  let canonical = header;
  for (const r of records) {
    canonical += '\n' + canonicalSerialize(r);
  }

  return crypto.createHash('sha256').update(canonical, 'utf8').digest('hex');
}

// ── Deep freeze (mirrors convention from canonical pipeline modules) ───────────

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

// ── Validation helpers ────────────────────────────────────────────────────────

function isValidDateString(s) {
  if (typeof s !== 'string') return false;
  return /^\d{4}-\d{2}-\d{2}$/.test(s);
}

function isValidTimestampOrNull(ts) {
  if (ts === null) return true;
  if (typeof ts !== 'string') return false;
  const d = new Date(ts);
  return !isNaN(d.getTime());
}

function isUUIDv4(v) {
  return typeof v === 'string' && UUID_V4_RE.test(v);
}

// ── Record schema validation ──────────────────────────────────────────────────

/**
 * validateRecord(record, index)
 *
 * Returns { valid: true } or { valid: false, reason: string }.
 *
 * Validates:
 *   1.  All required fields present (own properties)
 *   2.  run_record_id is a valid UUID v4
 *   3.  symbol is a non-empty string
 *   4.  session_date is YYYY-MM-DD
 *   5.  preset_id is a non-empty string
 *   6.  exit_target_r ∈ {2, 3, 4}
 *   7.  detection_status ∈ {'VALID', 'INVALID'}
 *   8.  failed_rules is an array
 *   9.  outcome ∈ VALID_OUTCOMES
 *   10. Nullable timestamp fields are null or valid ISO 8601
 *   11. candidate_id is null or a UUID v4
 */
function validateRecord(record, index) {
  if (record === null || typeof record !== 'object') {
    return { valid: false, reason: `record[${index}] is not an object` };
  }

  for (const field of REQUIRED_RECORD_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(record, field)) {
      return { valid: false, reason: `record[${index}] missing required field "${field}"` };
    }
  }

  if (!isUUIDv4(record.run_record_id)) {
    return {
      valid: false,
      reason: `record[${index}].run_record_id is not a valid UUID v4: ${record.run_record_id}`
    };
  }

  if (typeof record.symbol !== 'string' || record.symbol.trim().length === 0) {
    return { valid: false, reason: `record[${index}].symbol must be a non-empty string` };
  }

  if (!isValidDateString(record.session_date)) {
    return {
      valid: false,
      reason: `record[${index}].session_date must be YYYY-MM-DD, got: ${record.session_date}`
    };
  }

  if (typeof record.preset_id !== 'string' || record.preset_id.trim().length === 0) {
    return { valid: false, reason: `record[${index}].preset_id must be a non-empty string` };
  }

  if (![2, 3, 4].includes(record.exit_target_r)) {
    return {
      valid: false,
      reason: `record[${index}].exit_target_r must be 2, 3, or 4, got: ${record.exit_target_r}`
    };
  }

  if (record.detection_status !== 'VALID' && record.detection_status !== 'INVALID') {
    return {
      valid: false,
      reason: `record[${index}].detection_status must be "VALID" or "INVALID", got: ${record.detection_status}`
    };
  }

  if (!Array.isArray(record.failed_rules)) {
    return { valid: false, reason: `record[${index}].failed_rules must be an array` };
  }

  if (!VALID_OUTCOMES.has(record.outcome)) {
    return {
      valid: false,
      reason: `record[${index}].outcome "${record.outcome}" is not a valid OUTCOME value`
    };
  }

  const timestampFields = [
    'confirmation_timestamp',
    'entry_timestamp',
    'first_evaluation_timestamp',
    'exit_timestamp'
  ];
  for (const f of timestampFields) {
    if (!isValidTimestampOrNull(record[f])) {
      return {
        valid: false,
        reason: `record[${index}].${f} must be null or a valid ISO 8601 timestamp, got: ${record[f]}`
      };
    }
  }

  if (record.candidate_id !== null && !isUUIDv4(record.candidate_id)) {
    return {
      valid: false,
      reason: `record[${index}].candidate_id must be null or a valid UUID v4, got: ${record.candidate_id}`
    };
  }

  return { valid: true };
}

// ── Duplicate detection ───────────────────────────────────────────────────────

/**
 * findDuplicates(records)
 *
 * Returns { ok: true } or { ok: false, reason: string }.
 * Checks duplicate run_record_id and duplicate non-null candidate_id.
 */
function findDuplicates(records) {
  const runIds       = new Set();
  const candidateIds = new Set();

  for (let i = 0; i < records.length; i++) {
    const r = records[i];

    if (runIds.has(r.run_record_id)) {
      return {
        ok: false,
        reason: `duplicate run_record_id "${r.run_record_id}" at index ${i}`
      };
    }
    runIds.add(r.run_record_id);

    if (r.candidate_id !== null) {
      if (candidateIds.has(r.candidate_id)) {
        return {
          ok: false,
          reason: `duplicate candidate_id "${r.candidate_id}" at index ${i}`
        };
      }
      candidateIds.add(r.candidate_id);
    }
  }

  return { ok: true };
}

// ── Chronological ordering ────────────────────────────────────────────────────

/**
 * ensureChronologicalOrder(records)
 *
 * Returns { ok: true } or { ok: false, reason: string }.
 * Records must be in non-decreasing session_date order.
 */
function ensureChronologicalOrder(records) {
  for (let i = 1; i < records.length; i++) {
    const prev = records[i - 1].session_date;
    const curr = records[i].session_date;
    if (curr < prev) {
      return {
        ok: false,
        reason: `records are not in chronological order: "${prev}" (index ${i - 1}) followed by "${curr}" (index ${i})`
      };
    }
  }
  return { ok: true };
}

// ── Homogeneous run validation ────────────────────────────────────────────────

/**
 * extractEngineVersion(record)
 *
 * Returns the engine_version string from detection_result.engine_version, or
 * null if unavailable.
 *
 * NOTE: The current Strategy Runner (bdrr_strategy_runner.js v1) sets
 * detection_result to null for PIPELINE_FAILURE records, which makes
 * engine_version unavailable for those records.  validateHomogeneous() treats
 * a null engine_version as a contract violation and rejects the dataset.
 * This is a known runner integration issue; a future runner version should
 * populate engine_version on all record types (including PIPELINE_FAILURE).
 */
function extractEngineVersion(record) {
  if (record.detection_result &&
      typeof record.detection_result.engine_version === 'string' &&
      record.detection_result.engine_version.length > 0) {
    return record.detection_result.engine_version;
  }
  return null;
}

/**
 * validateHomogeneous(records)
 *
 * Returns { ok: true, symbol, presetId, exitTargetR, engineVersion } when all
 * records share the same symbol, preset_id, exit_target_r, and engine_version.
 *
 * Returns { ok: false, reason: string } on any violation.
 *
 * Rules (all mandatory, no exemptions):
 *   - symbol:         all records must share the same non-empty string
 *   - preset_id:      all records must share the same non-empty string
 *   - exit_target_r:  all records must share the same value
 *   - engine_version: every record must provide a non-empty engine_version
 *                     via detection_result.engine_version; all values must
 *                     be identical.  Records that cannot supply an
 *                     engine_version (e.g. PIPELINE_FAILURE records from the
 *                     current runner) are rejected rather than silently exempted.
 */
function validateHomogeneous(records) {
  const first = records[0];
  const symbol      = first.symbol;
  const presetId    = first.preset_id;
  const exitTargetR = first.exit_target_r;

  // Extract engine_version from the first record; it must be non-null.
  const firstEV = extractEngineVersion(first);
  if (firstEV === null) {
    return {
      ok: false,
      reason: 'homogeneity violation at index 0: engine_version is missing or empty ' +
              '(detection_result is null or engine_version is absent — ' +
              'PIPELINE_FAILURE records from the current runner cannot be included ' +
              'in TradeDataset/v1 until the runner populates engine_version on all record types)'
    };
  }
  const engineVersion = firstEV;

  for (let i = 1; i < records.length; i++) {
    const r = records[i];

    if (r.symbol !== symbol) {
      return {
        ok: false,
        reason: `homogeneity violation at index ${i}: symbol "${r.symbol}" differs from "${symbol}"`
      };
    }

    if (r.preset_id !== presetId) {
      return {
        ok: false,
        reason: `homogeneity violation at index ${i}: preset_id "${r.preset_id}" differs from "${presetId}"`
      };
    }

    if (r.exit_target_r !== exitTargetR) {
      return {
        ok: false,
        reason: `homogeneity violation at index ${i}: exit_target_r ${r.exit_target_r} differs from ${exitTargetR}`
      };
    }

    const ev = extractEngineVersion(r);
    if (ev === null) {
      return {
        ok: false,
        reason: `homogeneity violation at index ${i}: engine_version is missing or empty ` +
                '(detection_result is null or engine_version is absent)'
      };
    }
    if (ev !== engineVersion) {
      return {
        ok: false,
        reason: `homogeneity violation at index ${i}: engine_version "${ev}" differs from "${engineVersion}"`
      };
    }
  }

  return { ok: true, symbol, presetId, exitTargetR, engineVersion };
}

// ── Metadata assembly ─────────────────────────────────────────────────────────

/**
 * buildMetadata(datasetId, records, trades, homogeneous)
 *
 * Assembles summary metadata from the validated, homogeneous record collection.
 * Does NOT modify or reinterpret any record values.
 *
 * @param {string}      datasetId    - Deterministic SHA-256 hex digest
 * @param {Array}       records      - All runner records (frozen)
 * @param {Array}       trades       - Candidate-only records (frozen)
 * @param {object|null} homogeneous  - Result from validateHomogeneous(), or null
 *                                     for empty datasets
 */
function buildMetadata(datasetId, records, trades, homogeneous) {
  if (records.length === 0) {
    return Object.freeze({
      schema_version: DATASET_SCHEMA_VERSION,
      dataset_id:     datasetId,
      engine_version: null,
      preset_id:      null,
      symbol:         null,
      exit_target_r:  null,
      date_range:     Object.freeze({ first: null, last: null }),
      session_count:  0,
      trade_count:    0,
      generated_at:   new Date().toISOString()
    });
  }

  const dates = records.map(r => r.session_date).sort();

  return Object.freeze({
    schema_version: DATASET_SCHEMA_VERSION,
    dataset_id:     datasetId,
    engine_version: homogeneous.engineVersion,
    preset_id:      homogeneous.presetId,
    symbol:         homogeneous.symbol,
    exit_target_r:  homogeneous.exitTargetR,
    date_range:     Object.freeze({ first: dates[0], last: dates[dates.length - 1] }),
    session_count:  records.length,
    trade_count:    trades.length,
    generated_at:   new Date().toISOString()
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * buildTradeDataset(strategyRunnerResults)
 *
 * Accepts the frozen array produced by runBdrrStrategy() and returns an
 * immutable Trade Dataset object.
 *
 * @param {Array} strategyRunnerResults - Frozen array of result records from
 *   runBdrrStrategy().  All records must form a homogeneous strategy run
 *   (same symbol, preset_id, exit_target_r, engine_version).
 *
 * @returns {object} Frozen Trade Dataset:
 *   {
 *     schema_version: 'TradeDataset/v1',
 *     metadata: {
 *       schema_version, dataset_id, engine_version, preset_id, symbol,
 *       exit_target_r, date_range, session_count, trade_count, generated_at
 *     },
 *     records: [ ...all runner records ],   // every session, including NO_VALID_SETUP
 *     trades:  [ ...candidate records ]      // only records where candidate_id !== null
 *   }
 *
 * Object identity of individual records is preserved in both collections.
 *
 * @throws {TypeError}  if input is not an array
 * @throws {RangeError} if any record fails schema validation
 * @throws {RangeError} if duplicate run_record_id or candidate_id detected
 * @throws {RangeError} if records are not in chronological (session_date) order
 * @throws {RangeError} if records are not from a homogeneous strategy run
 */
function buildTradeDataset(strategyRunnerResults) {
  // ── Input type check ─────────────────────────────────────────────────────
  if (!Array.isArray(strategyRunnerResults)) {
    throw new TypeError(
      'buildTradeDataset: strategyRunnerResults must be an array, got: ' +
      typeof strategyRunnerResults
    );
  }

  // ── Empty dataset ────────────────────────────────────────────────────────
  // No records to serialize; hash covers only the header (schema + null fields).
  if (strategyRunnerResults.length === 0) {
    const datasetId = deriveDatasetId(DATASET_SCHEMA_VERSION, null, null, null, null, []);
    const records   = Object.freeze([]);
    const trades    = Object.freeze([]);
    const metadata  = buildMetadata(datasetId, records, trades, null);
    return deepFreeze({ schema_version: DATASET_SCHEMA_VERSION, metadata, records, trades });
  }

  // ── Per-record schema validation ─────────────────────────────────────────
  for (let i = 0; i < strategyRunnerResults.length; i++) {
    const result = validateRecord(strategyRunnerResults[i], i);
    if (!result.valid) {
      throw new RangeError('buildTradeDataset: schema validation failed — ' + result.reason);
    }
  }

  // ── Duplicate detection ──────────────────────────────────────────────────
  const dupCheck = findDuplicates(strategyRunnerResults);
  if (!dupCheck.ok) {
    throw new RangeError('buildTradeDataset: duplicate ID detected — ' + dupCheck.reason);
  }

  // ── Chronological ordering ───────────────────────────────────────────────
  const orderCheck = ensureChronologicalOrder(strategyRunnerResults);
  if (!orderCheck.ok) {
    throw new RangeError('buildTradeDataset: ' + orderCheck.reason);
  }

  // ── Homogeneous run validation ───────────────────────────────────────────
  const homoCheck = validateHomogeneous(strategyRunnerResults);
  if (!homoCheck.ok) {
    throw new RangeError('buildTradeDataset: ' + homoCheck.reason);
  }

  // ── Deterministic dataset ID ─────────────────────────────────────────────
  // Pass the full ordered records array so the hash covers complete content,
  // not only run_record_id values.
  const datasetId = deriveDatasetId(
    DATASET_SCHEMA_VERSION,
    homoCheck.engineVersion,
    homoCheck.presetId,
    homoCheck.symbol,
    homoCheck.exitTargetR,
    strategyRunnerResults
  );

  // ── Assemble immutable collections ───────────────────────────────────────
  // records: all runner records in original order, preserving object identity
  // trades:  only records with a non-null candidate_id
  // Neither collection clones nor rewrites any value.
  const records = Object.freeze(strategyRunnerResults.map(r => r));
  const trades  = Object.freeze(strategyRunnerResults.filter(r => r.candidate_id !== null));

  // ── Build metadata ───────────────────────────────────────────────────────
  const metadata = buildMetadata(datasetId, records, trades, homoCheck);

  // ── Assemble and deep-freeze the dataset ─────────────────────────────────
  return deepFreeze({
    schema_version: DATASET_SCHEMA_VERSION,
    metadata,
    records,
    trades
  });
}

// ── Module exports ────────────────────────────────────────────────────────────

module.exports = {
  buildTradeDataset,
  DATASET_SCHEMA_VERSION
};
