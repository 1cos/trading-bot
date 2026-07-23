/**
 * estrategie/pdh_pdl_definition.js
 *
 * Strategy Specification Layer — Phase 1B.
 *
 * Canonical certified definition for PDH/PDL Original (Python baseline).
 * All seven parameters are consumed by strategyPDHPDL in index.html.
 *
 * Python source mapping (pdh_pdl.py):
 *   STOP_LOSS_TICKS         → parameters.sl_ticks       (4)
 *   TAKE_PROFIT_MULTIPLIER  → parameters.rr              (2.0)
 *   BODY_MIN                → parameters.body_min        (0.20)
 *   BODY_MAX                → parameters.body_max        (0.70)
 *   SESSIONE_INIZIO         → parameters.session_start   ("09:30" ET)
 *   SESSIONE_FINE           → parameters.session_end     ("16:00" ET)
 *   allow_reentry           → parameters.allow_reentry   (true)
 *
 * allow_reentry rationale:
 *   Python trova_segnali resets broken_pdh / broken_pdl to False after a
 *   signal fires on a given level, but does NOT block further signals if a
 *   fresh opposite-side move occurs and a new breakout on a different level
 *   is detected within the same session. Multiple signals per day are
 *   therefore possible. allow_reentry: true reflects this.
 *
 *   The current JS engine enforces a stricter per-day lock via `inTrade`
 *   (maximum 1 trade per day). That lock is intentionally preserved through
 *   Phase 1B and will be aligned with the canonical specification in Phase 2.
 *   See the comment inside strategyPDHPDL for the exact alignment note.
 *
 * Parameter-resolution precedence (as applied in strategyPDHPDL):
 *   body_min, body_max, session_start, session_end, allow_reentry:
 *     Always taken from this definition. No UI override. Fallback to
 *     the hardcoded literal in case this file fails to load.
 *   sl_ticks, rr:
 *     Certified baseline defined here. UI sliders override these for
 *     experimental runs (params.sl_ticks, params.rr come from getParams()).
 *     When the UI slider is at its default position the values are identical
 *     to the certified baseline (sl_ticks=4 default, rr=2.0 default).
 *
 * Browser: loaded via <script src="estrategie/pdh_pdl_definition.js"> before
 *   the main inline <script>. Writes to window.STRATEGY_DEFINITIONS.
 * Node (tests): the IIFE writes to globalThis, so require() works directly.
 */

/* global STRATEGY_DEFINITIONS */
(function (root) {
  if (!root.STRATEGY_DEFINITIONS) root.STRATEGY_DEFINITIONS = {};
  var defs = root.STRATEGY_DEFINITIONS;

  defs['pdh_pdl_v1'] = {
    // ── Identity ──────────────────────────────────────────────────────────────
    strategy_id: 'pdh_pdl_v1',
    version:     '1.0.0',
    name:        'PDH/PDL Original (Python baseline)',

    // ── Parameters ────────────────────────────────────────────────────────────
    parameters: {
      // Risk parameters — certified baseline; UI sliders override for experiments.
      sl_ticks:      4,       // Python: STOP_LOSS_TICKS = 4       (× 0.25 pt per tick)
      rr:            2,       // Python: TAKE_PROFIT_MULTIPLIER = 2.0

      // Candle-body filter — no UI override; always from this definition.
      body_min:      0.20,    // Python: BODY_MIN = 0.20
      body_max:      0.70,    // Python: BODY_MAX = 0.70

      // Session window — ET strings; converted to UTC HHMM inside strategyPDHPDL.
      // No UI override.
      session_start: '09:30', // Python: SESSIONE_INIZIO = "09:30" ET → 1330 UTC (EDT)
      session_end:   '16:00', // Python: SESSIONE_FINE  = "16:00" ET → 2000 UTC (EDT)

      // Re-entry policy.
      // true: Python allows multiple signals per day from independent breakout/retest
      //       sequences on different levels or after opposite-side moves.
      // The JS engine currently blocks re-entry via inTrade (Phase 2 alignment pending).
      allow_reentry: true,
    },
  };

}(typeof globalThis !== 'undefined' ? globalThis :
  typeof global    !== 'undefined' ? global    :
  typeof window    !== 'undefined' ? window    : this));
