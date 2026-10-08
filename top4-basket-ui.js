/*
 * DigitMatchStar Top-4 Immediate DEMO Basket UI v2
 *
 * - PREPARE removed.
 * - APPROVE removed.
 * - One click: EXECUTE TOP-4 NOW.
 * - The server freezes the freshest Top-4 ranking at request time.
 * - DEMO executes immediately.
 * - REAL is preview-only; no real-money BUY is transmitted.
 */
(() => {
  'use strict';

  if (window.DMSTop4BasketUI) return;

  const state = {
    latest: null,
    busy: false,
    pollTimer: null
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
            TOP-4 IMMEDIATE BASKET
          </div>
          <div id="top4-basket-status" class="text-sm font-black text-white">
            Waiting for server ranking…
          </div>
        </div>
        <div class="text-right">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Execution</div>
          <div class="text-[11px] font-black text-emerald-300">ONE CLICK · FRESH RANKING</div>
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

        <div class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Mode</div>
          <div id="top4-mode" class="mt-1 text-lg font-black text-white">—</div>
        </div>

        <div class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Last basket P/L</div>
          <div id="top4-last-pnl" class="mt-1 text-lg font-black text-white">—</div>
        </div>
      </div>

      <button id="top4-execute-btn"
        class="w-full rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:bg-slate-800 disabled:text-slate-500 px-3 py-3 font-black text-white text-sm">
        ⚡ EXECUTE TOP-4 NOW
      </button>

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
        DEMO: one click freezes the current server Top-4 and submits immediately.
        REAL: preview only; no real-money BUY is sent.
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
      .addEventListener('click', executeNow);

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
    const mode = String(st?.account_mode || '—').toUpperCase();

    document.getElementById('top4-mode').textContent = mode;

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

    btn.disabled = state.busy || top4.length < 4 || !sessionId();

    if (state.busy) {
      status.textContent = 'Executing fresh Top-4…';
      btn.textContent = '⚡ EXECUTING…';
    } else {
      btn.textContent =
        mode === 'REAL'
          ? '👁 REFRESH REAL TOP-4 PREVIEW'
          : '⚡ EXECUTE TOP-4 NOW';

      if (top4.length < 4) {
        status.textContent = 'Waiting for fresh Top-4 ranking…';
      } else {
        status.textContent =
          `Ready · ${top4.map(x => x.digit).join(' · ')}`;
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

  async function executeNow() {
    const sid = sessionId();
    if (!sid || state.busy) return;

    const stake = Number(
      document.getElementById('top4-basket-stake')?.value || 0
    );

    if (!Number.isFinite(stake) || stake <= 0) {
      alert('Enter a valid basket stake.');
      return;
    }

    try {
      state.busy = true;
      render();

      const result = await api(
        `/sessions/${sid}/top4/execute`,
        {
          method: 'POST',
          body: { basket_stake: stake }
        }
      );

      state.latest = result;

      const status = document.getElementById('top4-basket-status');

      if (String(result.account_mode).toUpperCase() === 'REAL') {
        status.textContent = 'REAL preview refreshed · no BUY sent';
      } else {
        const opened = Number(result.opened_count || 0);
        status.textContent =
          `Submitted immediately · ${opened}/4 contracts opened`;
      }

    } catch (e) {
      alert(e.message);
      const status = document.getElementById('top4-basket-status');
      if (status) status.textContent = `Execution error · ${e.message}`;
    } finally {
      state.busy = false;
      render();
      setTimeout(refreshStatus, 500);
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
      if (!state.busy) refreshStatus();
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
    executeNow
  };
})();
