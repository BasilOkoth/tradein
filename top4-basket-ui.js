/*
 * DigitMatchStar Top-4 Hybrid UI v3 (Real Trade Execution & Automation Enabled)
 *
 * - Allows switching between DEMO and REAL trade execution modes.
 * - Supports manual single-click execution.
 * - Adds Automated Trading loop mode to auto-trigger basket orders on fresh ranking snapshots.
 */
(() => {
  'use strict';

  if (window.DMSTop4BasketUI) return;

  const state = {
    latest: null,
    busy: false,
    pollTimer: null,
    autoTimer: null,
    autoTradingEnabled: false,
    lastAutoEpoch: 0
  };

  const money = (n) => `$${Number(n || 0).toFixed(2)}`;

  function api(path, options = {}) {
    if (typeof window.serverExecRequest !== 'function') {
      throw new Error('Server execution API is not available');
    }
    return window.serverExecRequest(path, options);
  }

  function sessionState() {
    return window.SERVER_EXECUTION?.state || null;
  }

  function sessionId() {
    return Number(
      window.SERVER_EXECUTION?.sessionId ||
      sessionState()?.id ||
      0
    );
  }

  function ensurePanel() {
    const host =
      document.getElementById('recycle-runtime-panel') ||
      document.getElementById('trade-result-container');

    if (!host) return null;

    let panel = document.getElementById('dms-top4-basket-panel');
    if (panel) return panel;

    panel = document.createElement('div');
    panel.id = 'dms-top4-basket-panel';
    panel.className =
      'mt-3 rounded-xl border border-fuchsia-500/30 bg-slate-950/80 p-3 text-xs';

    panel.innerHTML = `
      <div class="flex items-center justify-between gap-3 mb-3">
        <div>
          <div class="text-[10px] uppercase tracking-wider text-fuchsia-400 font-black">
            TOP-4 BASKET EXECUTION ENGINE
          </div>
          <div id="top4-basket-status" class="text-sm font-black text-white">
            Waiting for server ranking…
          </div>
        </div>
        <div class="text-right">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Automation</div>
          <div id="top4-auto-badge" class="text-[11px] font-black text-slate-400">AUTOMATION: OFF</div>
        </div>
      </div>

      <div id="top4-ranks" class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3"></div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <label class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Basket stake</div>
          <input id="top4-basket-stake" type="number" min="0.04" step="0.01" value="10.00"
            class="mt-1 w-full rounded bg-slate-950 border border-slate-700 px-2 py-1.5 text-white font-black">
        </label>

        <div class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Per digit</div>
          <div id="top4-per-leg" class="mt-1 text-lg font-black text-white">$2.50</div>
        </div>

        <label class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Execution Mode</div>
          <select id="top4-exec-mode-select" class="mt-1 w-full rounded bg-slate-950 border border-slate-700 px-1 py-1 text-white font-black">
            <option value="demo">DEMO</option>
            <option value="real">REAL (LIVE MONEY)</option>
          </select>
        </label>

        <div class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Last basket P/L</div>
          <div id="top4-last-pnl" class="mt-1 text-lg font-black text-white">—</div>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-2 mb-2">
        <button id="top4-execute-btn"
          class="rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:bg-slate-800 disabled:text-slate-500 px-3 py-3 font-black text-white text-sm">
          ⚡ EXECUTE TOP-4 NOW
        </button>

        <button id="top4-toggle-auto-btn"
          class="rounded-lg bg-fuchsia-800 hover:bg-fuchsia-700 px-3 py-3 font-black text-white text-sm">
          🤖 START AUTO-TRADER
        </button>
      </div>

      <div class="grid grid-cols-2 gap-2 mt-2">
        <button id="top4-refresh-btn"
          class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-bold text-slate-200">
          Refresh status
        </button>
        <button id="top4-export-btn"
          class="rounded-lg border border-cyan-700 bg-cyan-950/30 px-3 py-2 font-bold text-cyan-200">
          Export Top-4 JSON
        </button>
      </div>

      <div id="top4-note" class="mt-2 text-[10px] text-slate-400">
        DEMO: Freezes current ranking & executes virtual contracts.
        REAL: Transmits real-money orders directly to Deriv API.
      </div>
    `;

    host.appendChild(panel);

    const stake = panel.querySelector('#top4-basket-stake');
    stake.addEventListener('input', () => {
      const total = Number(stake.value || 0);
      document.getElementById('top4-per-leg').textContent =
        Number.isFinite(total) && total > 0
          ? money(total / 4)
          : '—';
    });

    panel.querySelector('#top4-execute-btn')
      .addEventListener('click', () => executeNow());

    panel.querySelector('#top4-toggle-auto-btn')
      .addEventListener('click', toggleAutoTrader);

    panel.querySelector('#top4-refresh-btn')
      .addEventListener('click', refreshStatus);

    panel.querySelector('#top4-export-btn')
      .addEventListener('click', exportData);

    return panel;
  }

  function currentTop4() {
    const st = sessionState();
    const score = st?.digit_score || {};
    const ranking = Array.isArray(score.ranking)
      ? [...score.ranking].sort(
          (a, b) => Number(b.score || 0) - Number(a.score || 0)
        )
      : [];
    return ranking.slice(0, 4);
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;

    const st = sessionState();
    const top4 = currentTop4();
    const modeSelect = document.getElementById('top4-exec-mode-select');
    const selectedMode = (modeSelect?.value || 'demo').toUpperCase();

    document.getElementById('top4-ranks').innerHTML = top4.map((row, idx) => `
      <div class="rounded-lg border ${
        idx === 0 ? 'border-fuchsia-400' : 'border-slate-700'
      } bg-slate-900/70 p-2">
        <div class="text-[9px] uppercase text-fuchsia-400 font-black">Rank #${idx + 1}</div>
        <div class="text-2xl font-black text-white">${Number(row.digit)}</div>
        <div class="text-[10px] text-slate-400">score ${Number(row.score || 0).toFixed(3)}</div>
      </div>
    `).join('');

    const status = document.getElementById('top4-basket-status');
    const btn = document.getElementById('top4-execute-btn');
    const autoBtn = document.getElementById('top4-toggle-auto-btn');
    const autoBadge = document.getElementById('top4-auto-badge');

    btn.disabled = state.busy || top4.length < 4 || !sessionId();

    if (state.autoTradingEnabled) {
      autoBadge.textContent = 'AUTOMATION: ACTIVE 🟢';
      autoBadge.className = 'text-[11px] font-black text-emerald-400 animate-pulse';
      autoBtn.textContent = '🛑 STOP AUTO-TRADER';
      autoBtn.className = 'rounded-lg bg-rose-800 hover:bg-rose-700 px-3 py-3 font-black text-white text-sm';
    } else {
      autoBadge.textContent = 'AUTOMATION: OFF';
      autoBadge.className = 'text-[11px] font-black text-slate-400';
      autoBtn.textContent = '🤖 START AUTO-TRADER';
      autoBtn.className = 'rounded-lg bg-fuchsia-800 hover:bg-fuchsia-700 px-3 py-3 font-black text-white text-sm';
    }

    if (state.busy) {
      status.textContent = `Executing [${selectedMode}] Top-4…`;
      btn.textContent = '⚡ EXECUTING…';
    } else {
      btn.textContent = `⚡ EXECUTE TOP-4 NOW [${selectedMode}]`;

      if (top4.length < 4) {
        status.textContent = 'Waiting for fresh Top-4 ranking…';
      } else {
        status.textContent = `Ready · ${top4.map(x => x.digit).join(' · ')}`;
      }
    }

    const pnl = Number(state.latest?.net_profit);
    const pnlEl = document.getElementById('top4-last-pnl');

    if (Number.isFinite(pnl)) {
      pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${money(pnl)}`;
      pnlEl.className =
        `mt-1 text-lg font-black ${
          pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'
        }`;
    } else {
      pnlEl.textContent = '—';
      pnlEl.className = 'mt-1 text-lg font-black text-white';
    }
  }

  async function executeNow(autoTriggered = false) {
    const sid = sessionId();
    if (!sid || state.busy) return;

    const stake = Number(
      document.getElementById('top4-basket-stake')?.value || 0
    );
    const mode = document.getElementById('top4-exec-mode-select')?.value || 'demo';

    if (!Number.isFinite(stake) || stake <= 0) {
      if (!autoTriggered) alert('Enter a valid basket stake.');
      return;
    }

    if (mode === 'real' && !autoTriggered) {
      const confirmReal = confirm('⚠️ REAL MONEY TRADING: Are you sure you want to execute live Deriv orders?');
      if (!confirmReal) return;
    }

    try {
      state.busy = true;
      render();

      const result = await api(
        `/sessions/${sid}/top4/execute`,
        {
          method: 'POST',
          body: {
            basket_stake: stake,
            mode: mode
          }
        }
      );

      state.latest = result;
      const status = document.getElementById('top4-basket-status');
      const opened = Number(result.opened_count || 0);

      status.textContent = `[${mode.toUpperCase()}] Submitted · ${opened}/4 contracts opened`;

    } catch (e) {
      if (!autoTriggered) alert(e.message);
      const status = document.getElementById('top4-basket-status');
      if (status) status.textContent = `Execution error · ${e.message}`;
    } finally {
      state.busy = false;
      render();
      setTimeout(refreshStatus, 500);
    }
  }

  function toggleAutoTrader() {
    state.autoTradingEnabled = !state.autoTradingEnabled;
    if (state.autoTradingEnabled) {
      const mode = document.getElementById('top4-exec-mode-select')?.value || 'demo';
      if (mode === 'real') {
        const confirmAutoReal = confirm('⚠️ AUTOMATED REAL TRADING: Auto-trader will execute LIVE ORDERS automatically. Proceed?');
        if (!confirmAutoReal) {
          state.autoTradingEnabled = false;
          render();
          return;
        }
      }
    }
    render();
  }

  function checkAutomationLoop() {
    if (!state.autoTradingEnabled || state.busy) return;

    const st = sessionState();
    const currentEpoch = Number(st?.digit_score?.last_epoch || 0);
    const latestStatus = state.latest?.status;

    // Trigger auto execution if server generated a new tick/score epoch and last basket is settled or non-existent
    if (
      currentEpoch > state.lastAutoEpoch &&
      (!latestStatus || latestStatus === 'SETTLED')
    ) {
      const top4 = currentTop4();
      if (top4.length === 4) {
        state.lastAutoEpoch = currentEpoch;
        executeNow(true);
      }
    }
  }

  async function refreshStatus() {
    const sid = sessionId();

    if (!sid) {
      render();
      return;
    }

    try {
      const data = await api(`/sessions/${sid}/top4/status`);
      state.latest = data.latest || null;
      render();
    } catch (_) {
      render();
    }
  }

  async function exportData() {
    const sid = sessionId();
    if (!sid) {
      alert('No server session selected.');
      return;
    }

    try {
      const data = await api(`/sessions/${sid}/top4/export`);
      const blob = new Blob(
        [JSON.stringify(data, null, 2)],
        { type: 'application/json' }
      );
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `digitmatchstar-top4-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      alert(e.message);
    }
  }

  function start() {
    ensurePanel();
    render();
    refreshStatus();

    state.pollTimer = setInterval(() => {
      render();
      if (!state.busy) {
        refreshStatus();
        checkAutomationLoop();
      }
    }, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener(
      'DOMContentLoaded',
      start,
      { once: true }
    );
  } else {
    start();
  }

  window.DMSTop4BasketUI = {
    state,
    refreshStatus,
    executeNow,
    toggleAutoTrader
  };
})();