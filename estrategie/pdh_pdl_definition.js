/**
 * estrategie/pdh_pdl_definition.js
 *
 * Strategy Specification Layer — Phase 2.
 *
 * Canonical certified definition for PDH/PDL Original (Python baseline).
 * All seven parameters are consumed by strategyPDHPDL in index.html.
 *
 * Python source mapping (pdh_pdl.py):
 *   STOP_LOSS_TICKS         → parameters.sl_ticks       (4)
 *   TAKE_PROFIT_MULTIPLIER  → parameters.rr              (2)
 *   BODY_MIN                → parameters.body_min        (0.20)
 *   BODY_MAX                → parameters.body_max        (0.70)
 *   SESSIONE_INIZIO         → parameters.session_start   ("09:30" ET)
 *   SESSIONE_FINE           → parameters.session_end     ("16:00" ET)
 *   allow_reentry           → parameters.allow_reentry   (true)
 *
 * allow_reentry:
 *   Python trova_segnali allows multiple signals per session day.
 *   After a signal fires, only the matching breakout flag resets.
 *   A fresh re-breakout of the same level (or opposite side) re-arms the flag.
 *   allow_reentry: true is now enforced by the Phase 2 engine.
 *
 * session_start / session_end:
 *   Stored as ET strings. The engine converts them using the
 *   America/New_York IANA timezone via Intl.DateTimeFormat, so
 *   session boundaries remain correct across EDT and EST.
 *
 * Parameter-resolution precedence (as applied in strategyPDHPDL):
 *   body_min, body_max, session_start, session_end, allow_reentry:
 *     Always from this definition. No UI override.
 *   sl_ticks, rr:
 *     UI sliders (params.sl_ticks, params.rr) override for experimental runs.
 *     When absent or undefined in params, the certified baseline here is used.
 *     Slider defaults (sl_ticks=4, rr=2.0) are identical to the baseline.
 *
 * Browser: loaded via <script src="estrategie/pdh_pdl_definition.js"> before
 *   the main inline <script>. Writes to window.STRATEGY_DEFINITIONS.
 * Node (tests): the IIFE writes to globalThis; require() works directly.
 */

/* global STRATEGY_DEFINITIONS */
(function (root) {
  if (!root.STRATEGY_DEFINITIONS) root.STRATEGY_DEFINITIONS = {};
  var defs = root.STRATEGY_DEFINITIONS;

  defs['pdh_pdl_v1'] = {
    // ── Identity ──────────────────────────────────────────────────────────────
    strategy_id: 'pdh_pdl_v1',
    version:     '2.0.0',
    name:        'PDH/PDL Original (Python baseline)',

    // ── Parameters ────────────────────────────────────────────────────────────
    parameters: {
      sl_ticks:      4,       // Python: STOP_LOSS_TICKS = 4   (× 0.25 pt per tick)
      rr:            2,       // Python: TAKE_PROFIT_MULTIPLIER = 2.0
      body_min:      0.20,    // Python: BODY_MIN = 0.20
      body_max:      0.70,    // Python: BODY_MAX = 0.70
      session_start: '09:30', // Python: SESSIONE_INIZIO = "09:30" ET
      session_end:   '16:00', // Python: SESSIONE_FINE  = "16:00" ET
      allow_reentry: true,    // Python: no inTrade lock; re-entry after fresh breakout
    },
  };

}(typeof globalThis !== 'undefined' ? globalThis :
  typeof global    !== 'undefined' ? global    :
  typeof window    !== 'undefined' ? window    : this));
