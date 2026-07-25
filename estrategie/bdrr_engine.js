/**
 * estrategie/bdrr_engine.js
 *
 * BDRR Detection Engine — Stage 1 (Session + ORB construction), Stage 2
 * (Confirmed Break), Stage 3 (Displacement), Stage 4 (Retest Window), and
 * Stage 5 (Rejection Qualification) ONLY.
 *
 * Deliberately isolated: no entry/stop/target calculation, position sizing,
 * outcome evaluation, multi-day running, backtesting aggregation, or UI
 * integration. No import of / from index.html.
 *
 * All functions are pure and deterministic: same input -> same output,
 * every time, with no mutation of caller-owned data and no reliance on
 * wall-clock time, randomness, or any other side channel.
 *
 * Candle shape (matches the convention already used by strategyPDHPDL /
 * strategyORB / strategyOB in index.html):
 *   { time: Date, open: number, high: number, low: number, close: number }
 * `time` must be a JS Date (or a value accepted by Intl.DateTimeFormat,
 * i.e. a Date or epoch-ms number) representing the bar timestamp in UTC;
 * ET wall-clock time is derived from it via Intl.DateTimeFormat, exactly
 * as index.html already does for PDH/PDL (DST-safe).
 *
 * Design note on function boundaries (stated explicitly since the task
 * spec did not fully pin this down):
 *   - buildSessionContext(candles, config) assumes `candles` already
 *     belongs to exactly one trading session (one ET calendar date).
 *     It does NOT slice a multi-day dataset down to one day — that is
 *     out of scope for this stage. It validates, defensively sorts,
 *     and reports the session's ET date.
 *   - buildORB(candles, sessionContext, config) is the function
 *     responsible for locating the specific 09:30 ORB candle and
 *     failing explicitly if it is missing. It uses
 *     sessionContext.candles (the validated, sorted array) as the
 *     source of truth; `candles` is accepted per the required
 *     signature and cross-checked defensively against sessionContext.
 *   - findBreak(candles, orb, config) expects `candles` to be the SAME
 *     chronologically sorted array that produced `orb` (typically
 *     sessionContext.candles from the same run). This is verified
 *     defensively rather than assumed silently.
 *
 * Frozen failure vocabulary reused from BDRR_ENGINE_CANONICAL_HANDOFF.md
 * §3.3 (`failed_stage` enum) where applicable: LEVEL_NOT_FOUND,
 * BREAK_NOT_FOUND. Two additional, stage-agnostic codes are used for
 * conditions the frozen enum does not cover (input hygiene / config
 * support), and are clearly distinguished from real detection failures:
 *   - INVALID_SESSION_INPUT   (buildSessionContext: bad/empty candle input)
 *   - UNSUPPORTED_CONFIGURATION (config values this stage does not implement)
 *   - INVALID_INPUT           (candles array doesn't match upstream result)
 * These three are NOT part of the frozen DetectionResult/v1 enum and must
 * not be confused with real Stage 1-5 detection outcomes.
 *
 * Malformed config (missing required keys / wrong types) throws a plain
 * Error — that is a caller/integration bug, not a market-data detection
 * outcome, and per spec only *normal detection failures* must avoid
 * throwing and return a structured result instead.
 *
 * Run tests: node estrategie/test_bdrr_stage1_stage2.js
 *            node estrategie/test_bdrr_stage3.js
 *            node estrategie/test_bdrr_stage4.js
 *            node estrategie/test_bdrr_stage5.js
 */

'use strict';

// ── Timezone helpers (self-contained; mirrors index.html's ET helpers) ─────

function makeETFormatters(timezone) {
  const hhmmFmt = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone, hour: '2-digit', minute: '2-digit', hour12: false
  });
  const dateFmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'
  });
  return { hhmmFmt, dateFmt };
}

function getETTimeString(time, hhmmFmt) {
  // Returns "HH:MM" in the configured timezone (e.g. "09:30").
  const parts = hhmmFmt.formatToParts(time);
  const hour = parts.find(p => p.type === 'hour').value;
  const minute = parts.find(p => p.type === 'minute').value;
  return `${hour}:${minute}`;
}

function getETDateString(time, dateFmt) {
  // Returns "YYYY-MM-DD" in the configured timezone.
  return dateFmt.format(time);
}

function toMillis(time) {
  if (time instanceof Date) return time.getTime();
  if (typeof time === 'number') return time;
  throw new TypeError('candle.time must be a Date or an epoch-ms number');
}

// ── Tick arithmetic (integer ticks are the internal source of truth) ──────

function priceToTicks(price, tickSize) {
  if (typeof price !== 'number' || !isFinite(price)) {
    throw new TypeError('price must be a finite number');
  }
  return Math.round(price / tickSize);
}

function decimalsOf(tickSize) {
  const s = String(tickSize);
  const i = s.indexOf('.');
  return i === -1 ? 0 : s.length - i - 1;
}

function ticksToPoints(ticks, tickSize) {
  const decimals = decimalsOf(tickSize);
  const value = ticks * tickSize;
  return Number(value.toFixed(Math.max(decimals, 2)));
}

// ── Config validation (throws — caller/integration bug, not a detection outcome) ──

function assertValidConfig(config) {
  if (!config || typeof config !== 'object') {
    throw new TypeError('config must be an object');
  }
  const required = [
    'timeframe_minutes', 'timezone', 'session_open', 'orb_start',
    'orb_duration_minutes', 'level_source', 'direction', 'tick_size'
  ];
  required.forEach(key => {
    if (config[key] === undefined) {
      throw new TypeError(`config.${key} is required`);
    }
  });
}

// ── Stage 1a: Session construction ─────────────────────────────────────────

/**
 * buildSessionContext(candles, config)
 *
 * Precondition: `candles` belongs to exactly one trading session (one ET
 * calendar date). This function validates that, sorts defensively, and
 * reports the session's ET date. It does not select or validate the ORB
 * candle itself — see buildORB.
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function buildSessionContext(candles, config) {
  assertValidConfig(config);

  if (!Array.isArray(candles)) {
    throw new TypeError('candles must be an array');
  }
  if (candles.length === 0) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_SESSION_INPUT',
      reason: 'no candles provided'
    };
  }

  const { hhmmFmt, dateFmt } = makeETFormatters(config.timezone);

  // Defensive copy, sorted ascending by time. Never mutates the input array.
  const sorted = candles.slice().sort((a, b) => toMillis(a.time) - toMillis(b.time));

  const firstDate = getETDateString(sorted[0].time, dateFmt);
  const mismatched = sorted.find(c => getETDateString(c.time, dateFmt) !== firstDate);
  if (mismatched) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_SESSION_INPUT',
      reason: `candles span multiple ET calendar dates (found ${firstDate} and ` +
              `${getETDateString(mismatched.time, dateFmt)}); buildSessionContext ` +
              `expects candles from exactly one trading session`
    };
  }

  return {
    status: 'OK',
    date: firstDate,
    timezone: config.timezone,
    session_open: config.session_open,
    candles: sorted,
    candle_count: sorted.length
  };
}

// ── Stage 1b: ORB construction ─────────────────────────────────────────────

/**
 * buildORB(candles, sessionContext, config)
 *
 * Locates the single ORB candle (the first session candle beginning at
 * config.session_open, per the current preset where orb_start ===
 * "session_open" and orb_duration_minutes === timeframe_minutes) and
 * derives ORB High / ORB Low from it. Uses sessionContext.candles as the
 * authoritative, validated candle list; `candles` is accepted per the
 * required signature and cross-checked against it defensively.
 *
 * Fails explicitly (status: 'FAILED') if the required ORB candle is
 * missing, or if the config requests ORB behaviour this stage does not
 * implement (multi-candle ORB windows, orb_start other than
 * "session_open"). Never uses candles after the ORB candle.
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function buildORB(candles, sessionContext, config) {
  assertValidConfig(config);

  if (!sessionContext || sessionContext.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: 'LEVEL_NOT_FOUND',
      reason: 'cannot build ORB: sessionContext is missing or failed ' +
              `(${sessionContext && sessionContext.reason})`
    };
  }

  if (config.orb_start !== 'session_open') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `orb_start "${config.orb_start}" is not implemented; only "session_open" is supported`
    };
  }

  if (config.orb_duration_minutes !== config.timeframe_minutes) {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: 'multi-candle ORB windows are not implemented in this stage; ' +
              `orb_duration_minutes (${config.orb_duration_minutes}) must equal ` +
              `timeframe_minutes (${config.timeframe_minutes})`
    };
  }

  if (config.level_source !== 'ORB_HIGH') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `level_source "${config.level_source}" is not implemented in this stage; only "ORB_HIGH" is supported`
    };
  }

  const source = sessionContext.candles;

  // Defensive cross-check: if the caller's `candles` differs in length or
  // first/last timestamp from sessionContext.candles, refuse to guess.
  if (Array.isArray(candles) && candles.length > 0 &&
      (candles.length !== source.length ||
       toMillis(candles[0].time) !== toMillis(source[0].time))) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_INPUT',
      reason: 'candles does not match sessionContext.candles; buildORB requires ' +
              'the same array (or an equivalent copy) used to build sessionContext'
    };
  }

  const { hhmmFmt } = makeETFormatters(config.timezone);

  const orbIndex = source.findIndex(c => getETTimeString(c.time, hhmmFmt) === config.session_open);

  if (orbIndex === -1) {
    return {
      status: 'FAILED',
      failed_stage: 'LEVEL_NOT_FOUND',
      reason: `ORB candle not found at ${config.session_open} for session ${sessionContext.date}`
    };
  }

  const orbCandle = source[orbIndex];
  const orbHigh = orbCandle.high;
  const orbLow = orbCandle.low;
  const levelPrice = orbHigh; // level_source === 'ORB_HIGH'

  return {
    status: 'OK',
    date: sessionContext.date,
    orb_candle_index: orbIndex,
    orb_candle: orbCandle,
    orb_high: orbHigh,
    orb_low: orbLow,
    orb_low_active: false, // present for completeness only; not an active detection level
    level_source: 'ORB_HIGH',
    level_price: levelPrice,
    level_price_ticks: priceToTicks(levelPrice, config.tick_size),
    direction: config.direction
  };
}

// ── Stage 2: Confirmed Break ────────────────────────────────────────────────

/**
 * findBreak(candles, orb, config)
 *
 * Scans `candles` strictly after orb.orb_candle_index, in chronological
 * order, for the first candle whose close qualifies as a confirmed break
 * (LONG: close > level_price). Returns the first qualifying break only.
 * Never inspects a candle before deciding on the previous one — no
 * look-ahead. `candles` must be the same chronologically sorted array
 * that produced `orb` (this is verified defensively).
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function findBreak(candles, orb, config) {
  assertValidConfig(config);

  if (!orb || orb.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (orb && orb.failed_stage) || 'LEVEL_NOT_FOUND',
      reason: 'cannot search for a break: upstream ORB result failed ' +
              `(${orb && orb.reason})`
    };
  }

  if (config.direction !== 'LONG') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `direction "${config.direction}" is not implemented in this stage; only "LONG" is supported`
    };
  }

  if (!Array.isArray(candles)) {
    throw new TypeError('candles must be an array');
  }

  const orbCandleAtIndex = candles[orb.orb_candle_index];
  if (!orbCandleAtIndex || toMillis(orbCandleAtIndex.time) !== toMillis(orb.orb_candle.time)) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_INPUT',
      reason: 'candles does not match the array used to build orb ' +
              '(orb_candle_index does not point at the same candle)'
    };
  }

  const levelPrice = orb.level_price;
  const levelTicks = orb.level_price_ticks;

  for (let i = orb.orb_candle_index + 1; i < candles.length; i++) {
    const candle = candles[i];
    // LONG break condition: a confirmed close beyond the level. A wick that
    // extends beyond the level without a closing confirmation does not
    // qualify (Stage 2 reads close only). Exact equality does not qualify
    // (strict >).
    if (candle.close > levelPrice) {
      const closeTicks = priceToTicks(candle.close, config.tick_size);
      const distanceTicks = closeTicks - levelTicks;
      return {
        status: 'OK',
        date: orb.date,
        break_candle_index: i,
        break_candle: candle,
        break_timestamp: candle.time,
        directional_break_distance: {
          points: ticksToPoints(distanceTicks, config.tick_size),
          ticks: distanceTicks
        }
      };
    }
  }

  return {
    status: 'FAILED',
    failed_stage: 'BREAK_NOT_FOUND',
    reason: `no candle closed beyond level_price (${levelPrice}) after the ORB candle ` +
            `at index ${orb.orb_candle_index}`
  };
}

// ── Stage 3: Displacement ───────────────────────────────────────────────────

/**
 * findDisplacement(candles, orb, breakResult, config)
 *
 * Scans `candles` strictly after breakResult.break_candle_index, in
 * chronological order. A candle is a valid displacement bar only while its
 * low is strictly greater than level_price. The first candle whose low is
 * less than or equal to level_price begins the retest and ends the
 * displacement window (that candle itself is never part of the window).
 *
 * At least one completed post-break displacement bar must exist before that
 * first retest contact — if the very first post-break candle already
 * touches or crosses the level, this is the frozen structural failure
 * RETEST_BEFORE_DISPLACEMENT, independent of any numeric threshold.
 *
 * No numeric minimum-distance gate is implemented here: min_displacement_ticks
 * must be disabled (null/undefined) in `config` for this stage to run.
 *
 * `candles` must be the same chronologically sorted array that produced both
 * `orb` and `breakResult` (verified defensively, as in findBreak).
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function findDisplacement(candles, orb, breakResult, config) {
  assertValidConfig(config);

  if (!orb || orb.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (orb && orb.failed_stage) || 'LEVEL_NOT_FOUND',
      reason: 'cannot search for displacement: upstream ORB result failed ' +
              `(${orb && orb.reason})`
    };
  }

  if (!breakResult || breakResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (breakResult && breakResult.failed_stage) || 'BREAK_NOT_FOUND',
      reason: 'cannot search for displacement: upstream break result failed ' +
              `(${breakResult && breakResult.reason})`
    };
  }

  if (config.direction !== 'LONG') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `direction "${config.direction}" is not implemented in this stage; only "LONG" is supported`
    };
  }

  if (config.level_source !== 'ORB_HIGH') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `level_source "${config.level_source}" is not implemented in this stage; only "ORB_HIGH" is supported`
    };
  }

  if (config.min_displacement_ticks !== null && config.min_displacement_ticks !== undefined) {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: 'min_displacement_ticks must be disabled (null/undefined) in this stage; ' +
              'no numeric displacement threshold is implemented'
    };
  }

  if (!Array.isArray(candles)) {
    throw new TypeError('candles must be an array');
  }

  // Defensive chain validation: candles must be the same array that produced
  // both orb and breakResult.
  const orbCandleAtIndex = candles[orb.orb_candle_index];
  if (!orbCandleAtIndex || toMillis(orbCandleAtIndex.time) !== toMillis(orb.orb_candle.time)) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_INPUT',
      reason: 'candles does not match the array used to build orb'
    };
  }
  const breakCandleAtIndex = candles[breakResult.break_candle_index];
  if (!breakCandleAtIndex || toMillis(breakCandleAtIndex.time) !== toMillis(breakResult.break_candle.time)) {
    return {
      status: 'FAILED',
      failed_stage: 'INVALID_INPUT',
      reason: 'candles does not match the array used to build breakResult'
    };
  }

  const levelPrice = orb.level_price;
  const levelTicks = orb.level_price_ticks;
  const startIndex = breakResult.break_candle_index + 1; // breakout candle itself is never counted

  let i = startIndex;
  for (; i < candles.length; i++) {
    if (candles[i].low <= levelPrice) break; // first retest contact (low === level counts as contact)
  }

  if (i === candles.length) {
    return {
      status: 'FAILED',
      failed_stage: 'RETEST_NOT_FOUND',
      reason: `no candle with low <= level_price (${levelPrice}) found after the break candle ` +
              `at index ${breakResult.break_candle_index}; displacement window cannot be closed ` +
              'within the provided candles',
      date: orb.date,
      break_candle_index: breakResult.break_candle_index,
      displacement_start_index: startIndex,
      displacement_bar_count: candles.length - startIndex
    };
  }

  const firstRetestContactIndex = i;
  const displacementBarCount = firstRetestContactIndex - startIndex;

  if (displacementBarCount === 0) {
    return {
      status: 'FAILED',
      failed_stage: 'RETEST_BEFORE_DISPLACEMENT',
      reason: 'first post-break bar contacted the level; no displacement phase ' +
              'existed before retest began',
      date: orb.date,
      break_candle_index: breakResult.break_candle_index,
      first_retest_contact_index: firstRetestContactIndex,
      first_retest_contact_candle: candles[firstRetestContactIndex],
      first_retest_contact_timestamp: candles[firstRetestContactIndex].time
    };
  }

  // displacement_window excludes both the breakout candle (index < startIndex)
  // and the first retest-contact candle (slice end is exclusive).
  const displacementWindow = candles.slice(startIndex, firstRetestContactIndex);
  const displacementEndIndex = firstRetestContactIndex - 1;

  let maxFavorableHigh = -Infinity;
  let maxDistanceTicks = -Infinity;
  displacementWindow.forEach(bar => {
    if (bar.high > maxFavorableHigh) maxFavorableHigh = bar.high;
    const barHighTicks = priceToTicks(bar.high, config.tick_size);
    const distanceTicks = barHighTicks - levelTicks;
    if (distanceTicks > maxDistanceTicks) maxDistanceTicks = distanceTicks;
  });

  return {
    status: 'OK',
    date: orb.date,
    level_price: levelPrice,
    break_candle_index: breakResult.break_candle_index,
    displacement_start_index: startIndex,
    displacement_end_index: displacementEndIndex,
    displacement_bar_count: displacementBarCount,
    displacement_window: displacementWindow,
    max_favorable_high: maxFavorableHigh,
    displacement_distance: {
      points: ticksToPoints(maxDistanceTicks, config.tick_size),
      ticks: maxDistanceTicks
    },
    first_retest_contact_index: firstRetestContactIndex,
    first_retest_contact_candle: candles[firstRetestContactIndex],
    first_retest_contact_timestamp: candles[firstRetestContactIndex].time
  };
}

// ── Stage 4: Retest Window ──────────────────────────────────────────────────

/**
 * findRetestWindow(candles, orb, breakResult, displacementResult, config)
 *
 * Begins at displacementResult.first_retest_contact_index (that candle IS
 * included in the returned window and, since its low already qualifies, is
 * itself the first retest contact). Scans forward chronologically to the
 * last available candle, collecting every candle whose low <= level_price
 * (equality counts) as a retest contact. Candles whose low is above
 * level_price between contacts remain part of retest_window but are not
 * added to retest_contacts.
 *
 * Deliberately does NOT: apply rejection-candle geometry thresholds, decide
 * on a final confirmation candle, cap the number of failed retests, cap
 * setup age, or use min_penetration_ticks / min_close_beyond_level_ticks —
 * all of that is Stage 5 and later. retest_window_end_index is simply the
 * last candle available in `candles`, since no confirmation candle has been
 * selected yet at this stage.
 *
 * `candles` must be the same chronologically sorted array that produced
 * orb, breakResult, and displacementResult (verified defensively).
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function findRetestWindow(candles, orb, breakResult, displacementResult, config) {
  assertValidConfig(config);

  if (!orb || orb.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (orb && orb.failed_stage) || 'LEVEL_NOT_FOUND',
      reason: 'cannot search for retest window: upstream ORB result failed ' +
              `(${orb && orb.reason})`
    };
  }

  if (!breakResult || breakResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (breakResult && breakResult.failed_stage) || 'BREAK_NOT_FOUND',
      reason: 'cannot search for retest window: upstream break result failed ' +
              `(${breakResult && breakResult.reason})`
    };
  }

  if (!displacementResult || displacementResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (displacementResult && displacementResult.failed_stage) || 'RETEST_NOT_FOUND',
      reason: 'cannot search for retest window: upstream displacement result failed ' +
              `(${displacementResult && displacementResult.reason})`
    };
  }

  if (config.direction !== 'LONG') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `direction "${config.direction}" is not implemented in this stage; only "LONG" is supported`
    };
  }

  if (config.level_source !== 'ORB_HIGH') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `level_source "${config.level_source}" is not implemented in this stage; only "ORB_HIGH" is supported`
    };
  }

  if (!Array.isArray(candles)) {
    throw new TypeError('candles must be an array');
  }

  // Defensive chain validation against every upstream stage.
  const orbCandleAtIndex = candles[orb.orb_candle_index];
  if (!orbCandleAtIndex || toMillis(orbCandleAtIndex.time) !== toMillis(orb.orb_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build orb' };
  }
  const breakCandleAtIndex = candles[breakResult.break_candle_index];
  if (!breakCandleAtIndex || toMillis(breakCandleAtIndex.time) !== toMillis(breakResult.break_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build breakResult' };
  }
  const contactCandleAtIndex = candles[displacementResult.first_retest_contact_index];
  if (!contactCandleAtIndex ||
      toMillis(contactCandleAtIndex.time) !== toMillis(displacementResult.first_retest_contact_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build displacementResult' };
  }

  const levelPrice = orb.level_price;
  const levelTicks = orb.level_price_ticks;
  const retestStartIndex = displacementResult.first_retest_contact_index;
  const windowEndIndex = candles.length - 1;
  const displacementTicks = displacementResult.displacement_distance.ticks;

  const retestWindow = candles.slice(retestStartIndex, windowEndIndex + 1);
  const retestContacts = [];

  for (let i = retestStartIndex; i <= windowEndIndex; i++) {
    const c = candles[i];
    if (c.low <= levelPrice) {
      const lowTicks = priceToTicks(c.low, config.tick_size);
      const closestDirectionalPositionTicks = lowTicks - levelTicks;
      const penetrationTicks = Math.max(0, levelTicks - lowTicks);
      const penetrationPoints = ticksToPoints(penetrationTicks, config.tick_size);
      const retracementPct = displacementTicks === 0 ? null : penetrationTicks / displacementTicks;
      retestContacts.push({
        candle_index: i,
        candle: c,
        timestamp: c.time,
        closest_directional_position_ticks: closestDirectionalPositionTicks,
        penetration_through_level_ticks: penetrationTicks,
        penetration_through_level_points: penetrationPoints,
        displacement_retracement_pct: retracementPct
      });
    }
  }

  return {
    status: 'OK',
    date: orb.date,
    level_price: levelPrice,
    retest_start_index: retestStartIndex,
    retest_start_timestamp: candles[retestStartIndex].time,
    retest_window_start_index: retestStartIndex,
    retest_window_end_index: windowEndIndex,
    retest_window: retestWindow,
    retest_contacts: retestContacts,
    retest_contact_count: retestContacts.length
  };
}

// ── Stage 5: Rejection Qualification ────────────────────────────────────────

const REJECTION_WICK_RATIO_MIN = 0.47;
const BODY_RATIO_MAX = 0.40;
const FAVORABLE_CLOSE_LOCATION_MIN = 0.80;

/**
 * findRejection(candles, orb, breakResult, displacementResult, retestResult, config)
 *
 * Scans retestResult's window (via absolute indices retest_window_start_index
 * .. retest_window_end_index in `candles`) chronologically. Only candles
 * whose low <= level_price are retest attempts and may qualify; candles
 * whose low is above level_price are skipped entirely (they are not retest
 * attempts and never appear in failed_retests). The first attempt candle
 * whose geometry satisfies all three thresholds is returned as the
 * confirmation candle and scanning stops immediately — no candle after it
 * is inspected. Every earlier attempt that failed qualification is recorded
 * in failed_retests, in chronological order.
 *
 * Qualification (LONG, all three required):
 *   rejection_wick_ratio >= 0.47
 *   body_ratio           <= 0.40
 *   favorable_close_location >= 0.80
 * Penetration and close-beyond-level are computed and reported but never
 * gate qualification. A zero-range candle (range_ticks === 0) cannot
 * qualify; its ratio fields are null and its only failure reason is
 * ZERO_RANGE_CANDLE.
 *
 * `candles` must be the same chronologically sorted array used to build
 * orb, breakResult, displacementResult, and retestResult (verified
 * defensively).
 *
 * @returns {object} status: 'OK' | 'FAILED'
 */
function findRejection(candles, orb, breakResult, displacementResult, retestResult, config) {
  assertValidConfig(config);

  if (!orb || orb.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (orb && orb.failed_stage) || 'LEVEL_NOT_FOUND',
      reason: 'cannot search for rejection: upstream ORB result failed ' + `(${orb && orb.reason})`
    };
  }
  if (!breakResult || breakResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (breakResult && breakResult.failed_stage) || 'BREAK_NOT_FOUND',
      reason: 'cannot search for rejection: upstream break result failed ' + `(${breakResult && breakResult.reason})`
    };
  }
  if (!displacementResult || displacementResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (displacementResult && displacementResult.failed_stage) || 'RETEST_NOT_FOUND',
      reason: 'cannot search for rejection: upstream displacement result failed ' + `(${displacementResult && displacementResult.reason})`
    };
  }
  if (!retestResult || retestResult.status !== 'OK') {
    return {
      status: 'FAILED',
      failed_stage: (retestResult && retestResult.failed_stage) || 'RETEST_NOT_FOUND',
      reason: 'cannot search for rejection: upstream retest window result failed ' + `(${retestResult && retestResult.reason})`
    };
  }

  if (config.direction !== 'LONG') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `direction "${config.direction}" is not implemented in this stage; only "LONG" is supported`
    };
  }
  if (config.level_source !== 'ORB_HIGH') {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: `level_source "${config.level_source}" is not implemented in this stage; only "ORB_HIGH" is supported`
    };
  }
  if (config.min_penetration_ticks !== null && config.min_penetration_ticks !== undefined) {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: 'min_penetration_ticks must be disabled (null/undefined) in this stage; penetration is reported but never gated'
    };
  }
  if (config.min_close_beyond_level_ticks !== null && config.min_close_beyond_level_ticks !== undefined) {
    return {
      status: 'FAILED',
      failed_stage: 'UNSUPPORTED_CONFIGURATION',
      reason: 'min_close_beyond_level_ticks must be disabled (null/undefined) in this stage; close-beyond-level is reported but never gated'
    };
  }

  if (!Array.isArray(candles)) {
    throw new TypeError('candles must be an array');
  }

  // Defensive chain validation against every upstream stage.
  const orbCandleAtIndex = candles[orb.orb_candle_index];
  if (!orbCandleAtIndex || toMillis(orbCandleAtIndex.time) !== toMillis(orb.orb_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build orb' };
  }
  const breakCandleAtIndex = candles[breakResult.break_candle_index];
  if (!breakCandleAtIndex || toMillis(breakCandleAtIndex.time) !== toMillis(breakResult.break_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build breakResult' };
  }
  const dispContactAtIndex = candles[displacementResult.first_retest_contact_index];
  if (!dispContactAtIndex ||
      toMillis(dispContactAtIndex.time) !== toMillis(displacementResult.first_retest_contact_candle.time)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build displacementResult' };
  }
  const retestStartCandleAtIndex = candles[retestResult.retest_window_start_index];
  if (!retestStartCandleAtIndex || toMillis(retestStartCandleAtIndex.time) !== toMillis(retestResult.retest_start_timestamp)) {
    return { status: 'FAILED', failed_stage: 'INVALID_INPUT', reason: 'candles does not match the array used to build retestResult' };
  }

  const levelPrice = orb.level_price;
  const levelTicks = orb.level_price_ticks;
  const tickSize = config.tick_size;

  function evaluateGeometry(candle) {
    const highTicks = priceToTicks(candle.high, tickSize);
    const lowTicks = priceToTicks(candle.low, tickSize);
    const openTicks = priceToTicks(candle.open, tickSize);
    const closeTicks = priceToTicks(candle.close, tickSize);

    const rangeTicks = highTicks - lowTicks;
    const penetrationTicks = Math.max(0, levelTicks - lowTicks);
    const closeBeyondLevelTicks = closeTicks - levelTicks;

    if (rangeTicks === 0) {
      return {
        geometry: {
          range_ticks: 0,
          body_ticks: 0,
          rejection_wick_ticks: 0,
          opposite_wick_ticks: 0,
          rejection_wick_ratio: null,
          body_ratio: null,
          favorable_close_location: null,
          opposite_wick_ratio: null,
          penetration_through_level_ticks: penetrationTicks,
          penetration_through_level_points: ticksToPoints(penetrationTicks, tickSize),
          close_beyond_level_ticks: closeBeyondLevelTicks,
          close_beyond_level_points: ticksToPoints(closeBeyondLevelTicks, tickSize)
        },
        failed_rules: ['ZERO_RANGE_CANDLE'],
        qualifies: false
      };
    }

    const bodyTicks = Math.abs(closeTicks - openTicks);
    const rejectionWickTicks = Math.min(openTicks, closeTicks) - lowTicks;
    const oppositeWickTicks = highTicks - Math.max(openTicks, closeTicks);

    const rejectionWickRatio = rejectionWickTicks / rangeTicks;
    const bodyRatio = bodyTicks / rangeTicks;
    const favorableCloseLocation = (closeTicks - lowTicks) / rangeTicks;
    const oppositeWickRatio = oppositeWickTicks / rangeTicks;

    const failedRules = [];
    if (rejectionWickRatio < REJECTION_WICK_RATIO_MIN) failedRules.push('REJECTION_WICK_RATIO_TOO_LOW');
    if (bodyRatio > BODY_RATIO_MAX) failedRules.push('BODY_RATIO_TOO_HIGH');
    if (favorableCloseLocation < FAVORABLE_CLOSE_LOCATION_MIN) failedRules.push('FAVORABLE_CLOSE_LOCATION_TOO_LOW');

    return {
      geometry: {
        range_ticks: rangeTicks,
        body_ticks: bodyTicks,
        rejection_wick_ticks: rejectionWickTicks,
        opposite_wick_ticks: oppositeWickTicks,
        rejection_wick_ratio: rejectionWickRatio,
        body_ratio: bodyRatio,
        favorable_close_location: favorableCloseLocation,
        opposite_wick_ratio: oppositeWickRatio,
        penetration_through_level_ticks: penetrationTicks,
        penetration_through_level_points: ticksToPoints(penetrationTicks, tickSize),
        close_beyond_level_ticks: closeBeyondLevelTicks,
        close_beyond_level_points: ticksToPoints(closeBeyondLevelTicks, tickSize)
      },
      failed_rules: failedRules,
      qualifies: failedRules.length === 0
    };
  }

  const failedRetests = [];

  for (let i = retestResult.retest_window_start_index; i <= retestResult.retest_window_end_index; i++) {
    const candle = candles[i];
    if (candle.low > levelPrice) continue; // not a retest attempt; cannot qualify, not recorded

    const result = evaluateGeometry(candle);

    if (result.qualifies) {
      return {
        status: 'OK',
        date: orb.date,
        level_price: levelPrice,
        confirmation_candle_index: i,
        confirmation_candle: candle,
        confirmation_timestamp: candle.time,
        geometry: result.geometry,
        failed_retests: failedRetests,
        failed_retest_count: failedRetests.length
      };
    }

    failedRetests.push({
      candle_index: i,
      candle: candle,
      timestamp: candle.time,
      geometry: result.geometry,
      failed_rules: result.failed_rules
    });
  }

  return {
    status: 'FAILED',
    failed_stage: 'NO_QUALIFYING_REJECTION_CANDLE',
    reason: 'no retest-attempt candle satisfied all three rejection geometry thresholds ' +
            `within the retest window (indices ${retestResult.retest_window_start_index}-${retestResult.retest_window_end_index})`,
    failed_retests: failedRetests,
    failed_retest_count: failedRetests.length
  };
}

module.exports = {
  buildSessionContext,
  buildORB,
  findBreak,
  findDisplacement,
  findRetestWindow,
  findRejection,
  // exported for the test suite / future stages, not part of the "public API"
  // surface the task asked for, but harmless and useful to reuse verbatim:
  priceToTicks,
  ticksToPoints,
  getETTimeString,
  getETDateString
};
