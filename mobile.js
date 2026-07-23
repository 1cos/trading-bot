/**
 * mobile.js — Trading Bot Dashboard
 * Phase 1: Mobile Shell + Navigation
 *
 * Strategy for controls:
 *   The desktop sidebar (.sidebar) contains all controls with unique IDs.
 *   On mobile, we MOVE the sidebar DOM node into #mob-setup-screen.
 *   On desktop, we MOVE it back into .main.
 *   No IDs are duplicated. All existing JS functions continue to work unchanged.
 *
 * What this file does NOT touch:
 *   - runBacktest(), runStrategy(), displayResults() — zero changes
 *   - Supabase functions — zero changes
 *   - Chart.js instances — reused by reference
 *   - Any desktop DOM structure
 */

(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────────
  let activeTab = 'chart';
  let isMobileView = false;
  let sidebarLocation = 'desktop'; // 'desktop' | 'mobile'
  let miniChartInstance = null;

  // ── Media query ────────────────────────────────────────────────────────────
  const mq = window.matchMedia('(max-width: 767px)');

  function checkLayout() {
    const shouldBeMobile = mq.matches;
    if (shouldBeMobile && !isMobileView) {
      activateMobile();
    } else if (!shouldBeMobile && isMobileView) {
      activateDesktop();
    }
  }

  // ── Activate mobile mode ───────────────────────────────────────────────────
  function activateMobile() {
    isMobileView = true;
    document.body.classList.add('mobile-active');
    moveSidebarToMobile();
    syncContextBar();
    updateMobDbStatus();
    updateMobNotifDot();
    // Reflect any results already computed
    syncMetricsToMobile();
    syncTradesToMobile();
  }

  // ── Activate desktop mode ──────────────────────────────────────────────────
  function activateDesktop() {
    isMobileView = false;
    document.body.classList.remove('mobile-active');
    moveSidebarToDesktop();
  }

  // ── Sidebar DOM move ───────────────────────────────────────────────────────
  // The sidebar element is the source of truth for all controls.
  // We move it (not clone it) between desktop and mobile.

  function moveSidebarToMobile() {
    if (sidebarLocation === 'mobile') return;
    const sidebar = document.querySelector('.main .sidebar');
    const setupScreen = document.getElementById('mob-setup-content');
    if (!sidebar || !setupScreen) return;
    setupScreen.appendChild(sidebar);
    sidebarLocation = 'mobile';
  }

  function moveSidebarToDesktop() {
    if (sidebarLocation === 'desktop') return;
    const sidebar = document.querySelector('.sidebar');
    const main = document.querySelector('.main');
    if (!sidebar || !main) return;
    // Insert before .content div
    const content = main.querySelector('.content');
    main.insertBefore(sidebar, content);
    sidebarLocation = 'desktop';
  }

  // ── Build mobile shell HTML ────────────────────────────────────────────────
  function buildShell() {
    const shell = document.createElement('div');
    shell.id = 'mobile-shell';
    shell.innerHTML = `
      <!-- Mobile Header -->
      <div id="mob-header">
        <h1>Trading Bot <span>Dashboard</span></h1>
        <div class="mob-header-right">
          <span class="db-status err" id="mob-db-status">⚪ SB</span>
          <button id="mob-notif-btn" onclick="mobToggleNotif()">
            🔔
            <span id="mob-notif-dot"></span>
          </button>
        </div>
      </div>

      <!-- Context Bar -->
      <div id="mob-context-bar">
        <span id="mob-mode-pill">Futures</span>
        <span id="mob-symbol-pill">SPY</span>
        <button id="mob-run-btn" onclick="mobRunBacktest()">▶ Run</button>
      </div>

      <!-- Screen Container -->
      <div id="mob-screen-container">

        <!-- Chart Screen -->
        <div id="mob-chart-screen" class="mob-screen active">
          <!-- Empty state shown before first backtest -->
          <div id="mob-chart-empty" class="mob-empty-state">
            <div class="mob-empty-icon">📈</div>
            <div class="mob-empty-title">Nessun backtest ancora</div>
            <div class="mob-empty-desc">Configura i parametri in Setup ed esegui il backtest per vedere la curva del capitale.</div>
            <button class="mob-empty-cta" onclick="mobSwitchTab('setup')">Vai a Setup →</button>
          </div>

          <!-- Results shown after backtest -->
          <div id="mob-chart-results" style="display:none;">
            <!-- Equity Curve -->
            <div class="mob-chart-panel">
              <div class="mob-chart-panel-header">Curva del Capitale — P&amp;L Cumulativo</div>
              <div class="mob-chart-wrap">
                <canvas id="mob-pnl-canvas"></canvas>
              </div>
              <div id="mob-trigger-stats" class="mob-trigger-breakdown"></div>
            </div>

            <!-- Metrics 2×3 grid -->
            <div class="mob-metrics-grid">
              <div class="mob-metric">
                <div class="mlabel">Trade chiusi</div>
                <div class="mval neu" id="mob-m-trades">—</div>
              </div>
              <div class="mob-metric">
                <div class="mlabel">Win Rate</div>
                <div class="mval" id="mob-m-winrate">—</div>
              </div>
              <div class="mob-metric">
                <div class="mlabel">P&amp;L totale</div>
                <div class="mval" id="mob-m-profit">—</div>
                <div class="msub" id="mob-m-profit-sub"></div>
              </div>
              <div class="mob-metric">
                <div class="mlabel">P&amp;L medio</div>
                <div class="mval" id="mob-m-avg">—</div>
              </div>
              <div class="mob-metric">
                <div class="mlabel">Max vincita</div>
                <div class="mval pos" id="mob-m-maxwin">—</div>
              </div>
              <div class="mob-metric">
                <div class="mlabel">Max perdita</div>
                <div class="mval neg" id="mob-m-maxloss">—</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Setup Screen -->
        <div id="mob-setup-screen" class="mob-screen">
          <div id="mob-setup-content">
            <!-- .sidebar DOM node gets moved here by JS -->
          </div>
        </div>

        <!-- Trades Screen -->
        <div id="mob-trades-screen" class="mob-screen">
          <div id="mob-trades-empty" class="mob-empty-state">
            <div class="mob-empty-icon">📋</div>
            <div class="mob-empty-title">Nessun trade</div>
            <div class="mob-empty-desc">Esegui il backtest per vedere la lista dei trade.</div>
            <button class="mob-empty-cta" onclick="mobRunBacktest()">▶ Esegui Backtest</button>
          </div>
          <div id="mob-trades-content" style="display:none;">
            <div class="mob-trades-header">
              <span class="mob-trades-title" id="mob-trades-title">Trade</span>
              <span class="mob-trades-subtitle" id="mob-trades-subtitle"></span>
            </div>
            <div class="mob-trades-table-wrap" id="mob-trades-table-wrap"></div>
          </div>
        </div>

        <!-- Results Screen -->
        <div id="mob-results-screen" class="mob-screen">
          <div id="mob-results-empty" class="mob-empty-state">
            <div class="mob-empty-icon">📊</div>
            <div class="mob-empty-title">Nessun risultato</div>
            <div class="mob-empty-desc">Esegui il backtest per vedere i risultati dettagliati.</div>
            <button class="mob-empty-cta" onclick="mobRunBacktest()">▶ Esegui Backtest</button>
          </div>
          <div id="mob-results-content" style="display:none;">
            <!-- Performance -->
            <div class="mob-results-section">
              <div class="mob-results-section-header">Performance</div>
              <div class="mob-results-row">
                <span class="mob-results-label">Trade chiusi</span>
                <span class="mob-results-value neu" id="mob-r-trades">—</span>
              </div>
              <div class="mob-results-row">
                <span class="mob-results-label">Win Rate</span>
                <span class="mob-results-value" id="mob-r-winrate">—</span>
              </div>
              <div class="mob-results-row">
                <span class="mob-results-label">P&amp;L totale</span>
                <span class="mob-results-value" id="mob-r-profit">—</span>
              </div>
              <div class="mob-results-row">
                <span class="mob-results-label">P&amp;L medio/trade</span>
                <span class="mob-results-value" id="mob-r-avg">—</span>
              </div>
            </div>

            <!-- Distribution -->
            <div class="mob-results-section">
              <div class="mob-results-section-header">Distribuzione</div>
              <div class="mob-results-row">
                <span class="mob-results-label">Max vincita</span>
                <span class="mob-results-value pos" id="mob-r-maxwin">—</span>
              </div>
              <div class="mob-results-row">
                <span class="mob-results-label">Max perdita</span>
                <span class="mob-results-value neg" id="mob-r-maxloss">—</span>
              </div>
              <div class="mob-results-row" id="mob-r-wick-row" style="display:none;">
                <span class="mob-results-label"><span class="tag-wick">WICK</span> trigger</span>
                <span class="mob-results-value" id="mob-r-wick">—</span>
              </div>
              <div class="mob-results-row" id="mob-r-eng-row" style="display:none;">
                <span class="mob-results-label"><span class="tag-eng">ENG</span> trigger</span>
                <span class="mob-results-value" id="mob-r-eng">—</span>
              </div>
            </div>

            <!-- Mini equity in results -->
            <div class="mob-results-section">
              <div class="mob-results-section-header">Curva del Capitale</div>
              <div class="mob-results-chart-wrap">
                <canvas id="mob-mini-pnl-canvas"></canvas>
              </div>
            </div>
          </div>
        </div>

      </div><!-- end screen-container -->

      <!-- Bottom Navigation -->
      <nav id="mob-bottom-nav">
        <button class="mob-tab active" data-tab="chart" onclick="mobSwitchTab('chart')">
          <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
          Chart
        </button>
        <button class="mob-tab" data-tab="setup" onclick="mobSwitchTab('setup')">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M16.24 7.76a6 6 0 0 1 0 8.48M4.93 4.93a10 10 0 0 0 0 14.14M7.76 7.76a6 6 0 0 0 0 8.48"/></svg>
          Setup
        </button>
        <button class="mob-tab" data-tab="trades" onclick="mobSwitchTab('trades')">
          <svg viewBox="0 0 24 24"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="2"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="12" y2="16"/></svg>
          Trades
        </button>
        <button class="mob-tab" data-tab="results" onclick="mobSwitchTab('results')">
          <svg viewBox="0 0 24 24"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          Results
        </button>
      </nav>

      <!-- Mobile Notif Panel (slides in from right) -->
      <div id="mob-notif-panel">
        <div class="mob-notif-header">
          <div class="mob-section-label" style="margin-bottom:0">Notifiche</div>
          <button class="mob-notif-close" onclick="mobToggleNotif()">✕</button>
        </div>
        <div id="mob-notif-list"><div class="preset-empty">Nessuna notifica</div></div>
      </div>
    `;
    document.body.appendChild(shell);
  }

  // ── Tab switching ──────────────────────────────────────────────────────────
  window.mobSwitchTab = function (tab) {
    activeTab = tab;

    // Update screens
    document.querySelectorAll('.mob-screen').forEach(s => s.classList.remove('active'));
    const screen = document.getElementById('mob-' + tab + '-screen');
    if (screen) screen.classList.add('active');

    // Update tab buttons
    document.querySelectorAll('.mob-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // When switching to setup, ensure sidebar is there
    if (tab === 'setup' && isMobileView) {
      moveSidebarToMobile();
    }
  };

  // ── Run backtest proxy ─────────────────────────────────────────────────────
  // Calls the existing runBacktest() then syncs mobile UI
  window.mobRunBacktest = function () {
    // Switch to chart to show results appearing
    mobSwitchTab('chart');
    // runBacktest is defined in the main script; call it
    if (typeof runBacktest === 'function') {
      runBacktest();
    }
  };

  // ── Context bar sync ───────────────────────────────────────────────────────
  // Called after mode/symbol changes to keep context bar updated
  function syncContextBar() {
    const modePill = document.getElementById('mob-mode-pill');
    const symbolPill = document.getElementById('mob-symbol-pill');
    if (!modePill || !symbolPill) return;

    // Read current values from global state (set by existing functions)
    const modeLabels = { futures: 'Futures', options: 'Opzioni', equity: 'Azioni' };
    const mode = (typeof currentMode !== 'undefined') ? currentMode : 'futures';
    modePill.textContent = modeLabels[mode] || 'Futures';

    const simboloEl = document.getElementById('simbolo');
    symbolPill.textContent = simboloEl ? simboloEl.value : 'SPY';
  }

  // ── DB status mirror ───────────────────────────────────────────────────────
  function updateMobDbStatus() {
    const src = document.getElementById('db-status');
    const dst = document.getElementById('mob-db-status');
    if (!src || !dst) return;
    dst.textContent = src.textContent;
    dst.className = src.className;
  }

  // ── Notif dot mirror ───────────────────────────────────────────────────────
  function updateMobNotifDot() {
    const src = document.getElementById('notif-dot');
    const dst = document.getElementById('mob-notif-dot');
    if (!src || !dst) return;
    dst.style.display = src.style.display;
  }

  // ── Mobile notif panel ─────────────────────────────────────────────────────
  window.mobToggleNotif = function () {
    const panel = document.getElementById('mob-notif-panel');
    if (!panel) return;
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      // Sync notif content from desktop list
      const srcList = document.getElementById('notif-list');
      const dstList = document.getElementById('mob-notif-list');
      if (srcList && dstList) dstList.innerHTML = srcList.innerHTML;
      // Hide dot
      const dot = document.getElementById('mob-notif-dot');
      if (dot) dot.style.display = 'none';
    }
  };

  // ── Sync metrics from desktop DOM to mobile DOM ────────────────────────────
  // Called after displayResults() runs (patched below)
  function syncMetricsToMobile() {
    if (!isMobileView) return;

    const ids = ['trades', 'winrate', 'profit', 'avg', 'maxwin', 'maxloss'];
    ids.forEach(key => {
      const src = document.getElementById('m-' + key);
      const dst = document.getElementById('mob-m-' + key);
      if (!src || !dst) return;
      dst.textContent = src.textContent;
      dst.className = src.className;
    });

    // profit-sub separately
    const srcSub = document.getElementById('m-profit-sub');
    const dstSub = document.getElementById('mob-m-profit-sub');
    if (srcSub && dstSub) dstSub.textContent = srcSub.textContent;

    // Trigger stats
    syncTriggerStats();

    // Show results, hide empty state
    const emptyEl = document.getElementById('mob-chart-empty');
    const resultsEl = document.getElementById('mob-chart-results');
    if (emptyEl) emptyEl.style.display = 'none';
    if (resultsEl) resultsEl.style.display = 'block';

    // Mirror equity chart into mobile canvas
    mirrorEquityChart();

    // Also sync results screen
    syncResultsScreen();

    // Update context bar (symbol may have changed)
    syncContextBar();
  }

  function syncTriggerStats() {
    const srcStats = document.getElementById('trigger-stats');
    const dstStats = document.getElementById('mob-trigger-stats');
    if (!srcStats || !dstStats) return;
    dstStats.innerHTML = srcStats.innerHTML;
    if (srcStats.style.display !== 'none' && srcStats.innerHTML.trim()) {
      dstStats.classList.add('visible');
    } else {
      dstStats.classList.remove('visible');
    }
  }

  function syncResultsScreen() {
    // Performance
    const map = {
      'mob-r-trades': 'm-trades',
      'mob-r-winrate': 'm-winrate',
      'mob-r-profit': 'm-profit',
      'mob-r-avg': 'm-avg',
      'mob-r-maxwin': 'm-maxwin',
      'mob-r-maxloss': 'm-maxloss',
    };
    Object.entries(map).forEach(([dstId, srcId]) => {
      const src = document.getElementById(srcId);
      const dst = document.getElementById(dstId);
      if (!src || !dst) return;
      dst.textContent = src.textContent;
      dst.className = 'mob-results-value ' + (
        src.className.includes('pos') ? 'pos' :
        src.className.includes('neg') ? 'neg' :
        src.className.includes('neu') ? 'neu' :
        src.className.includes('warn') ? 'warn' : ''
      );
    });

    // Trigger breakdown in results
    const srcStats = document.getElementById('trigger-stats');
    if (srcStats && srcStats.style.display !== 'none' && srcStats.innerHTML.trim()) {
      const wickRow = document.getElementById('mob-r-wick-row');
      const engRow = document.getElementById('mob-r-eng-row');
      if (wickRow) wickRow.style.display = 'flex';
      if (engRow) engRow.style.display = 'flex';
      // Parse text from trigger stats
      const tbds = srcStats.querySelectorAll('.tbd');
      if (tbds[0]) {
        const wickDst = document.getElementById('mob-r-wick');
        if (wickDst) wickDst.textContent = tbds[0].textContent.replace('WICK', '').trim();
      }
      if (tbds[1]) {
        const engDst = document.getElementById('mob-r-eng');
        if (engDst) engDst.textContent = tbds[1].textContent.replace('ENG', '').trim();
      }
    }

    // Show results, hide empty
    const empty = document.getElementById('mob-results-empty');
    const content = document.getElementById('mob-results-content');
    if (empty) empty.style.display = 'none';
    if (content) content.style.display = 'block';

    // Mini equity chart
    mirrorMiniChart();
  }

  // ── Mirror equity chart into mobile canvas ─────────────────────────────────
  // Uses the data from the existing pnlChartInstance (Chart.js)
  function mirrorEquityChart() {
    const srcCanvas = document.getElementById('pnlChart');
    const dstCanvas = document.getElementById('mob-pnl-canvas');
    if (!srcCanvas || !dstCanvas) return;

    // Get data from existing chart instance
    const inst = window.pnlChartInstance;
    if (!inst) return;

    // Destroy old mobile instance if any
    if (window._mobPnlChart) {
      window._mobPnlChart.destroy();
      window._mobPnlChart = null;
    }

    // Shallow-clone config with responsive settings
    const srcDataset = inst.data.datasets[0];
    window._mobPnlChart = new Chart(dstCanvas, {
      type: 'line',
      data: {
        labels: inst.data.labels,
        datasets: [{
          data: srcDataset.data,
          borderColor: srcDataset.borderColor,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: true,
          backgroundColor: srcDataset.backgroundColor,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` P&L: ${ctx.raw >= 0 ? '+' : ''}${ctx.raw.toLocaleString('it-IT', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 })}`,
            },
          },
        },
        scales: {
          x: {
            ticks: { color: '#555', font: { size: 9 }, maxTicksLimit: 8 },
            grid: { color: 'rgba(255,255,255,0.03)' },
          },
          y: {
            ticks: {
              color: '#555', font: { size: 9 },
              callback: v => (v >= 0 ? '+' : '') + v.toLocaleString('it-IT', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0 }),
            },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
        },
      },
    });
  }

  function mirrorMiniChart() {
    const inst = window.pnlChartInstance;
    const dstCanvas = document.getElementById('mob-mini-pnl-canvas');
    if (!inst || !dstCanvas) return;

    if (window._mobMiniChart) {
      window._mobMiniChart.destroy();
      window._mobMiniChart = null;
    }

    const srcDataset = inst.data.datasets[0];
    window._mobMiniChart = new Chart(dstCanvas, {
      type: 'line',
      data: {
        labels: inst.data.labels,
        datasets: [{
          data: srcDataset.data,
          borderColor: srcDataset.borderColor,
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          backgroundColor: srcDataset.backgroundColor,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: false },
          y: { display: false },
        },
      },
    });
  }

  // ── Sync trades list to mobile ─────────────────────────────────────────────
  function syncTradesToMobile() {
    if (!isMobileView) return;

    const srcTable = document.getElementById('tradesTable');
    if (!srcTable) return;

    // Check if there's real content (not empty state)
    const hasTable = srcTable.querySelector('table');
    if (!hasTable) {
      // Still empty or error
      const empty = document.getElementById('mob-trades-empty');
      const content = document.getElementById('mob-trades-content');
      if (empty) empty.style.display = 'flex';
      if (content) content.style.display = 'none';
      return;
    }

    // Build mobile-friendly trade table
    // We use a simpler column set: Data, Ora, Tipo, Setup, Esito, P&L $
    const rows = srcTable.querySelectorAll('tbody tr');
    if (!rows.length) return;

    const empty = document.getElementById('mob-trades-empty');
    const content = document.getElementById('mob-trades-content');
    const wrap = document.getElementById('mob-trades-table-wrap');
    const title = document.getElementById('mob-trades-title');
    const subtitle = document.getElementById('mob-trades-subtitle');

    if (empty) empty.style.display = 'none';
    if (content) content.style.display = 'block';

    // Count from desktop header
    const headerEl = srcTable.querySelector('div[style*="padding"]');
    if (title) title.textContent = 'Ultimi trade';
    if (subtitle && headerEl) subtitle.textContent = headerEl.textContent.trim();

    // Build simplified table — clone rows but use only 6 of 11 columns
    // Column indices in desktop table: 0=Data, 1=Ora, 2=Tipo, 3=Setup, 8=Esito, 10=P&L$
    let html = '<table><thead><tr>';
    html += '<th>Data</th><th>Ora</th><th>Tipo</th><th>Setup</th><th>Esito</th><th>P&L</th>';
    html += '</tr></thead><tbody>';

    rows.forEach((row, i) => {
      const cells = row.querySelectorAll('td');
      if (cells.length < 11) return;
      const onclick = row.getAttribute('onclick') || '';
      const style = row.getAttribute('style') || '';
      html += `<tr class="clickable" onclick="${onclick}" style="${style}">`;
      html += `<td>${cells[0].textContent}</td>`;
      html += `<td>${cells[1].textContent}</td>`;
      html += `<td class="${cells[2].className}">${cells[2].textContent}</td>`;
      html += `<td>${cells[3].textContent}</td>`;
      html += `<td class="${cells[8].className}">${cells[8].textContent}</td>`;
      html += `<td style="font-weight:500" class="${cells[10].className}">${cells[10].textContent}</td>`;
      html += '</tr>';
    });

    html += '</tbody></table>';
    if (wrap) wrap.innerHTML = html;
  }

  // ── Patch displayResults to sync mobile after it runs ─────────────────────
  // We wrap displayResults without modifying the original function definition.
  function patchDisplayResults() {
    // Wait until displayResults is available (defined in main script)
    if (typeof displayResults !== 'function') return;
    const original = displayResults;
    window.displayResults = function (r, params) {
      original.call(this, r, params);
      // After desktop renders, sync to mobile
      if (isMobileView) {
        // Small delay to let Chart.js finish rendering
        setTimeout(() => {
          syncMetricsToMobile();
          syncTradesToMobile();
          // Update run button state
          const mobBtn = document.getElementById('mob-run-btn');
          const desktopBtn = document.getElementById('runBtn');
          if (mobBtn && desktopBtn) {
            mobBtn.disabled = desktopBtn.disabled;
          }
        }, 100);
      }
    };
  }

  // ── Patch runBacktest to update mobile run button state ───────────────────
  function patchRunBacktest() {
    if (typeof runBacktest !== 'function') return;
    const original = runBacktest;
    window.runBacktest = async function () {
      const mobBtn = document.getElementById('mob-run-btn');
      if (mobBtn) { mobBtn.disabled = true; mobBtn.textContent = '⏳ Run'; }
      await original.call(this);
      if (mobBtn) { mobBtn.disabled = false; mobBtn.textContent = '▶ Run'; }
      // Sync context bar after backtest (symbol/mode confirmed)
      syncContextBar();
    };
  }

  // ── Patch setMode to keep context bar in sync ──────────────────────────────
  function patchSetMode() {
    if (typeof setMode !== 'function') return;
    const original = setMode;
    window.setMode = function (m, el) {
      original.call(this, m, el);
      syncContextBar();
    };
  }

  // ── Patch simbolo select to keep symbol pill in sync ──────────────────────
  function patchSimbolo() {
    const sel = document.getElementById('simbolo');
    if (!sel) return;
    sel.addEventListener('change', () => syncContextBar());
  }

  // ── Patch addNotifica to sync notif dot ───────────────────────────────────
  function patchAddNotifica() {
    if (typeof addNotifica !== 'function') return;
    const original = addNotifica;
    window.addNotifica = function (tipo, msg) {
      original.call(this, tipo, msg);
      if (isMobileView) {
        updateMobNotifDot();
      }
    };
  }

  // ── Patch checkSupabase to mirror db status ────────────────────────────────
  function patchCheckSupabase() {
    if (typeof checkSupabase !== 'function') return;
    const original = checkSupabase;
    window.checkSupabase = async function () {
      await original.call(this);
      if (isMobileView) updateMobDbStatus();
    };
  }

  // ── Observe desktop db-status for any future updates ─────────────────────
  function observeDbStatus() {
    const src = document.getElementById('db-status');
    if (!src) return;
    new MutationObserver(() => {
      if (isMobileView) updateMobDbStatus();
    }).observe(src, { childList: true, characterData: true, subtree: true });
  }

  // ── orientationchange / resize ────────────────────────────────────────────
  mq.addEventListener('change', checkLayout);
  window.addEventListener('resize', checkLayout);
  window.addEventListener('orientationchange', checkLayout);

  // ── Init: run after all scripts loaded ────────────────────────────────────
  function init() {
    buildShell();
    // Patch existing functions
    patchDisplayResults();
    patchRunBacktest();
    patchSetMode();
    patchSimbolo();
    patchAddNotifica();
    patchCheckSupabase();
    observeDbStatus();
    // Run layout check
    checkLayout();
  }

  // DOMContentLoaded is already past if this runs after main script,
  // but use it as fallback just in case.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
