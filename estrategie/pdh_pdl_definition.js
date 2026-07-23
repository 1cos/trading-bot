/**
 * estrategie/pdh_pdl_definition.js
 *
 * Strategy Specification Layer — Phase 1.
 *
 * Defines the canonical certified preset for the PDH/PDL strategy.
 * These are the EXACT parameter values from the Python baseline (pdh_pdl.py).
 *
 * Purpose:
 *   - Single source of truth for the PDH/PDL Original parameter set.
 *   - The engine (strategyPDHPDL in index.html) reads body_min and body_max
 *     from this definition instead of using hardcoded literals.
 *   - sl_ticks and rr continue to flow through the UI params object;
 *     the definition documents what values constitute "Python parity".
 *
 * What this file does NOT do:
 *   - Does not change trade generation logic.
 *   - Does not affect ORB, OB, or Combined strategies.
 *   - Does not modify the UI.
 *   - Does not remove inTrade position lock.
 *
 * Python source mapping (pdh_pdl.py):
 *   BODY_MIN                → parameters.body_min
 *   BODY_MAX                → parameters.body_max
 *   STOP_LOSS_TICKS         → parameters.sl_ticks
 *   TAKE_PROFIT_MULTIPLIER  → parameters.rr
 *   SESSIONE_INIZIO         → parameters.session_start  (ET)
 *   SESSIONE_FINE           → parameters.session_end    (ET)
 *   allow_reentry           → parameters.allow_reentry
 *     Python never re-enters after a signal fires on a given day
 *     (broken_pdh / broken_pdl reset to False after each signal).
 *     The JS engine mirrors this via the inTrade flag (reset per day).
 *     allow_reentry: false matches both implementations.
 */

// STRATEGY_DEFINITIONS is a namespace object keyed by strategy_id.
// Only pdh_pdl_v1 is defined in Phase 1.
//
// Browser: loaded via <script src="estrategie/pdh_pdl_definition.js"> before
//   the main inline <script>. Writes to window.STRATEGY_DEFINITIONS.
//
// Node (tests): this file mutates the global object so the caller can
//   read STRATEGY_DEFINITIONS after require().
//
// The guard appends to an existing namespace or creates it.
/* global STRATEGY_DEFINITIONS, global, window */
(function (root) {
  if (!root.STRATEGY_DEFINITIONS) root.STRATEGY_DEFINITIONS = {};
  var defs = root.STRATEGY_DEFINITIONS;

  defs['pdh_pdl_v1'] = {
    // ── Identity ──────────────────────────────────────────────────────────────
    strategy_id: 'pdh_pdl_v1',
    version:     '1.0.0',
    name:        'PDH/PDL Original (Python baseline)',

    // ── Parameters ────────────────────────────────────────────────────────────
    // These are the EXACT values from pdh_pdl.py.
    // The engine reads body_min and body_max from here.
    // sl_ticks and rr are also defined here for documentation and future
    // use as certified defaults; the engine currently reads them from the UI.
    parameters: {
      sl_ticks:      4,       // Python: STOP_LOSS_TICKS = 4
      rr:            2,       // Python: TAKE_PROFIT_MULTIPLIER = 2.0
      body_min:      0.20,    // Python: BODY_MIN = 0.20
      body_max:      0.70,    // Python: BODY_MAX = 0.70
      session_start: '09:30', // Python: SESSIONE_INIZIO = "09:30" (ET)
      session_end:   '16:00', // Python: SESSIONE_FINE  = "16:00" (ET)
      allow_reentry: false,   // Python: broken_* resets after each signal → no re-entry
    },
  };

}(typeof globalThis !== 'undefined' ? globalThis :
  typeof global    !== 'undefined' ? global    :
  typeof window    !== 'undefined' ? window    : this));
