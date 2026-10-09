/*
 * DigitMatchStar Configurable Top-N Simultaneous DEMO UI v4
 */
(() => {
  'use strict';

  if (window.DMSTop4BasketUI) return;

  const state = { latest: null, busy: false, pollTimer: null };
  const money = n => `$${Number(n || 0).toFixed(2)}`;

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
    return Number(window.SERVER_EXECUTION?.sessionId || sessionState()?.id || 0);
  }

  function selectedTopN() {
    const n = Number(document.getElementById('topn-count')?.value || 7);
    return Math.max(1, Math.min(7, Math.trunc(n)));
  }

  function currentRanking() {
    const ranking = sessionState()?.digit_score?.ranking;
    return Array.isArray(ranking)
      ? [...ranking].sort((a, b) => Number(b.score || 0) - Number(a.score || 0))
      : [];
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
            CONFIGURABLE TOP-N SIMULTANEOUS
          </div>
          <div id="top4-basket-status" class="text-sm font-black text-white">
            Waiting for server ranking…
          </div>
        </div>
        <div class="text-[11px] font-black text-emerald-300">
          1–7 DIGITS · SIMULTANEOUS
        </div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
        <label class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Number of top digits</div>
          <select id="topn-count"
            class="mt-1 w-full rounded bg-slate-950 border border-slate-700 px-2 py-1.5 text-white font-black">
            <option value="1">Top 1</option>
            <option value="2">Top 2</option>
            <option value="3">Top 3</option>
            <option value="4">Top 4</option>
            <option value="5">Top 5</option>
            <option value="6">Top 6</option>
            <option value="7" selected>Top 7</option>
          </select>
        </label>

        <label class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Total stake</div>
          <input id="top4-basket-stake" type="number" min="0.07" step="0.01" value="7.00"
            class="mt-1 w-full rounded bg-slate-950 border border-slate-700 px-2 py-1.5 text-white font-black">
        </label>

        <div class="rounded-lg bg-slate-900/70 p-2">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Per digit</div>
          <div id="top4-per-leg" class="mt-1 text-lg font-black text-white">$1.00</div>
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

      <div id="top4-ranks" class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2 mb-3"></div>

      <button id="top4-execute-btn"
        class="w-full rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:bg-slate-800 disabled:text-slate-500 px-3 py-3 font-black text-white text-sm">
        ⚡ EXECUTE TOP 7 SIMULTANEOUSLY
      </button>

      <div class="grid grid-cols-2 gap-2 mt-2">
        <button id="top4-refresh-btn"
          class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-bold text-slate-200">
          Refresh status
        </button>
        <button id="top4-export-btn"
          class="rounded-lg border border-cyan-700 bg-cyan-950/30 px-3 py-2 font-bold text-cyan-200">
          Export Top-N JSON
        </button>
      </div>

      <div class="mt-2 text-[10px] text-slate-400">
        DEMO freezes the selected Top-N and launches one contract per selected digit concurrently.
        REAL executes immediately from the same START/EXECUTE click.
      </div>
    `;

    host.appendChild(panel);

    const refresh = () => render();
    panel.querySelector('#topn-count').addEventListener('change', refresh);
    panel.querySelector('#top4-basket-stake').addEventListener('input', refresh);
    panel.querySelector('#top4-execute-btn').addEventListener('click', executeNow);
    panel.querySelector('#top4-refresh-btn').addEventListener('click', refreshStatus);
    panel.querySelector('#top4-export-btn').addEventListener('click', exportData);

    return panel;
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) return;

    const topN = selectedTopN();
    const selected = currentRanking().slice(0, topN);
    const mode = String(sessionState()?.account_mode || '—').toUpperCase();

    document.getElementById('top4-mode').textContent = mode;

    document.getElementById('top4-ranks').innerHTML = selected.map((row, idx) => `
      <div class="rounded-lg border ${idx === 0 ? 'border-fuchsia-400' : 'border-slate-700'} bg-slate-900/70 p-2">
        <div class="text-[9px] uppercase text-fuchsia-400 font-black">Rank #${idx + 1}</div>
        <div class="text-2xl font-black text-white">${Number(row.digit)}</div>
        <div class="text-[10px] text-slate-400">score ${Number(row.score || 0).toFixed(3)}</div>
      </div>
    `).join('');

    const stake = Number(document.getElementById('top4-basket-stake')?.value || 0);
    document.getElementById('top4-per-leg').textContent =
      Number.isFinite(stake) && stake > 0 ? money(stake / topN) : '—';

    const status = document.getElementById('top4-basket-status');
    const btn = document.getElementById('top4-execute-btn');

    btn.disabled = state.busy || selected.length < topN || !sessionId();

    if (state.busy) {
      status.textContent = `Launching Top-${topN} simultaneously…`;
      btn.textContent = `⚡ LAUNCHING ${topN} CONTRACTS…`;
    } else {
      btn.textContent =
        mode === 'REAL'
          ? `⚡ START REAL TOP ${topN}`
          : `⚡ EXECUTE TOP ${topN} SIMULTANEOUSLY`;

      status.textContent =
        selected.length < topN
          ? `Waiting for Top-${topN} ranking…`
          : `Ready · ${selected.map(x => x.digit).join(' · ')}`;
    }

    const pnl = Number(state.latest?.net_profit);
    const pnlEl = document.getElementById('top4-last-pnl');
    if (Number.isFinite(pnl)) {
      pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${money(pnl)}`;
    } else {
      pnlEl.textContent = '—';
    }
  }

  async function executeNow() {
    const sid = sessionId();
    if (!sid || state.busy) return;

    const basketStake = Number(
      document.getElementById('top4-basket-stake')?.value || 0
    );
    const topN = selectedTopN();

    if (!Number.isFinite(basketStake) || basketStake <= 0) {
      alert('Enter a valid total basket stake.');
      return;
    }

    try {
      state.busy = true;
      render();

      const mode = String(
        sessionState()?.account_mode || 'DEMO'
      ).toUpperCase();

      const endpoint =
        mode === 'REAL'
          ? `/sessions/${sid}/top4/execute-real`
          : `/sessions/${sid}/top4/execute`;

      const result = await api(
        endpoint,
        {
          method: 'POST',
          body: {
            basket_stake: basketStake,
            top_n: topN
          }
        }
      );

      state.latest = result;
      const status = document.getElementById('top4-basket-status');

      if (String(result.account_mode).toUpperCase() === 'REAL') {
        status.textContent =
          `REAL submitted immediately · ${Number(result.opened_count || 0)}/${topN} contracts opened`;
      } else {
        status.textContent =
          `Submitted simultaneously · ${Number(result.opened_count || 0)}/${topN} contracts opened`;
      }
    } catch (e) {
      const status = document.getElementById('top4-basket-status');
      if (status) status.textContent = `Execution error · ${e.message}`;
      alert(e.message);
    } finally {
      state.busy = false;
      render();
      setTimeout(refreshStatus, 500);
    }
  }

  async function refreshStatus() {
    const sid = sessionId();
    if (!sid) return render();

    try {
      const data = await api(`/sessions/${sid}/top4/status`);
      state.latest = data.latest || null;
    } catch (_) {}
    render();
  }

  async function exportData() {
    const sid = sessionId();
    if (!sid) return;

    try {
      const data = await api(`/sessions/${sid}/top4/export`);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json'
      });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `digitmatchstar-topn-${Date.now()}.json`;
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
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }

  window.DMSTop4BasketUI = { state, refreshStatus, executeNow };
})();
