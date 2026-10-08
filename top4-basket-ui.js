/*
 * DigitMatchStar Top-4 Manual Basket UI v1
 *
 * Exact-codebase overlay:
 * - uses existing SERVER_EXECUTION session/JWT/API helper
 * - uses existing DigitScore V1 ranking from st.digit_score
 * - no automatic basket execution
 * - PREPARE -> explicit APPROVE
 * - DEMO approval buys four DEMO legs
 * - REAL approval records final approval only; no real-money BUY is transmitted
 */
(() => {
  'use strict';

  if (window.DMSTop4BasketUI) return;

  const state = {
    pending: null,
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
            TOP‑4 MANUAL BASKET
          </div>
          <div id="top4-basket-status" class="text-sm font-black text-white">
            Waiting for server ranking…
          </div>
        </div>
        <div class="text-right">
          <div class="text-[9px] uppercase text-slate-500 font-bold">Execution</div>
          <div class="text-[11px] font-black text-amber-300">PREPARE → APPROVE</div>
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

      <div id="top4-preview" class="hidden rounded-lg border border-amber-500/30 bg-amber-950/10 p-2 mb-3"></div>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
        <button id="top4-prepare-btn"
          class="rounded-lg bg-fuchsia-700 hover:bg-fuchsia-600 px-3 py-2 font-black text-white">
          PREPARE TOP‑4
        </button>
        <button id="top4-approve-btn" disabled
          class="rounded-lg bg-emerald-800 disabled:bg-slate-800 disabled:text-slate-500 px-3 py-2 font-black text-white">
          APPROVE
        </button>
        <button id="top4-reject-btn" disabled
          class="rounded-lg bg-rose-800 disabled:bg-slate-800 disabled:text-slate-500 px-3 py-2 font-black text-white">
          REJECT
        </button>
      </div>

      <div class="grid grid-cols-2 gap-2 mt-2">
        <button id="top4-refresh-btn"
          class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-bold text-slate-200">
          Refresh basket status
        </button>
        <button id="top4-export-btn"
          class="rounded-lg border border-cyan-700 bg-cyan-950/30 px-3 py-2 font-bold text-cyan-200">
          Export Top‑4 JSON
        </button>
      </div>

      <div id="top4-note" class="mt-2 text-[10px] text-slate-400">
        Top 4 are frozen from one V1 ranking snapshot. No next basket is opened automatically.
      </div>
    `;

    host.appendChild(panel);

    const stake = panel.querySelector('#top4-basket-stake');
    stake.addEventListener('input', () => {
      const total = Number(stake.value || 0);
      document.getElementById('top4-per-leg').textContent =
        Number.isFinite(total) && total > 0 ? money(total / 4) : '—';
    });

    panel.querySelector('#top4-prepare-btn').addEventListener('click', prepare);
    panel.querySelector('#top4-approve-btn').addEventListener('click', approve);
    panel.querySelector('#top4-reject-btn').addEventListener('click', reject);
    panel.querySelector('#top4-refresh-btn').addEventListener('click', refreshStatus);
    panel.querySelector('#top4-export-btn').addEventListener('click', exportData);

    return panel;
  }

  function renderRanking() {
    const panel = ensurePanel();
    if (!panel) return;

    const st = sessionState();
    const score = st?.digit_score || {};
    const ranking = Array.isArray(score.ranking)
      ? [...score.ranking].sort((a,b) => Number(b.score||0) - Number(a.score||0))
      : [];
    const top4 = ranking.slice(0,4);

    document.getElementById('top4-mode').textContent =
      String(st?.account_mode || '—').toUpperCase();

    document.getElementById('top4-ranks').innerHTML = top4.map((row, idx) => `
      <div class="rounded-lg border ${idx === 0 ? 'border-fuchsia-400' : 'border-slate-700'} bg-slate-900/70 p-2">
        <div class="text-[9px] uppercase text-fuchsia-400 font-black">Rank #${idx + 1}</div>
        <div class="text-2xl font-black text-white">${Number(row.digit)}</div>
        <div class="text-[10px] text-slate-400">score ${Number(row.score||0).toFixed(3)}</div>
      </div>
    `).join('');

    if (!top4.length) {
      document.getElementById('top4-basket-status').textContent =
        'Waiting for V1 ranking…';
    } else if (!state.pending) {
      document.getElementById('top4-basket-status').textContent =
        `Ready · ${top4.map(x => x.digit).join(' · ')}`;
    }

    const pnl = Number(state.latest?.net_profit);
    const pnlEl = document.getElementById('top4-last-pnl');
    if (Number.isFinite(pnl)) {
      pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${money(pnl)}`;
      pnlEl.className =
        `mt-1 text-lg font-black ${pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'}`;
    } else {
      pnlEl.textContent = '—';
      pnlEl.className = 'mt-1 text-lg font-black text-white';
    }
  }

  function renderPending() {
    const preview = document.getElementById('top4-preview');
    const approveBtn = document.getElementById('top4-approve-btn');
    const rejectBtn = document.getElementById('top4-reject-btn');

    if (!preview || !approveBtn || !rejectBtn) return;

    if (!state.pending) {
      preview.classList.add('hidden');
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      return;
    }

    const p = state.pending;
    preview.classList.remove('hidden');
    preview.innerHTML = `
      <div class="font-black text-amber-200 mb-1">AWAITING YOUR APPROVAL</div>
      <div>Basket <b>${String(p.basket_id).slice(0,10)}</b> · ${p.account_mode}</div>
      <div>Digits: <b>${p.legs.map(x => `#${x.rank}:${x.digit}`).join(' · ')}</b></div>
      <div>Total: <b>${money(p.basket_stake)} ${p.currency}</b></div>
      <div>Prepared from epoch: <b>${p.source_epoch || '—'}</b></div>
      <div class="mt-1 text-[10px] text-slate-400">
        ${p.account_mode === 'REAL'
          ? 'REAL approval is recorded but no real-money BUY is transmitted by this build.'
          : 'DEMO approval submits the four prepared DEMO contracts together.'}
      </div>
    `;
    approveBtn.disabled = false;
    rejectBtn.disabled = false;
  }

  async function prepare() {
    const sid = sessionId();
    if (!sid) {
      alert('Create/select a server session first.');
      return;
    }

    const stake = Number(document.getElementById('top4-basket-stake')?.value || 0);
    if (!Number.isFinite(stake) || stake <= 0) {
      alert('Enter a valid basket stake.');
      return;
    }

    if (!confirm(`Prepare a Top‑4 basket with total stake ${money(stake)}? No trade is placed yet.`)) {
      return;
    }

    try {
      state.busy = true;
      const p = await api(`/sessions/${sid}/top4/prepare`, {
        method: 'POST',
        body: { basket_stake: stake }
      });
      state.pending = p;
      renderPending();
      document.getElementById('top4-basket-status').textContent =
        'Prepared · waiting for approval';
    } catch (e) {
      alert(e.message);
    } finally {
      state.busy = false;
    }
  }

  async function approve() {
    const sid = sessionId();
    const p = state.pending;
    if (!sid || !p) return;

    const real = String(p.account_mode).toUpperCase() === 'REAL';
    const message = real
      ? `Approve REAL Top‑4 preview for digits ${p.legs.map(x=>x.digit).join(', ')}? This build records approval but does NOT send a real-money order.`
      : `Approve and submit the four DEMO contracts for digits ${p.legs.map(x=>x.digit).join(', ')}?`;

    if (!confirm(message)) return;

    try {
      state.busy = true;
      const result = await api(`/sessions/${sid}/top4/approve`, {
        method: 'POST',
        body: { basket_id: p.basket_id }
      });
      state.pending = null;
      state.latest = result;
      renderPending();
      renderRanking();
      document.getElementById('top4-basket-status').textContent =
        result.status || 'Approved';
    } catch (e) {
      alert(e.message);
    } finally {
      state.busy = false;
    }
  }

  async function reject() {
    const sid = sessionId();
    if (!sid || !state.pending) return;
    try {
      await api(`/sessions/${sid}/top4/reject`, { method: 'POST' });
      state.pending = null;
      renderPending();
      document.getElementById('top4-basket-status').textContent = 'Rejected';
    } catch (e) {
      alert(e.message);
    }
  }

  async function refreshStatus() {
    const sid = sessionId();
    if (!sid) {
      renderRanking();
      return;
    }
    try {
      const data = await api(`/sessions/${sid}/top4/status`);
      state.pending = data.pending || null;
      state.latest = data.latest || null;
      renderPending();
      renderRanking();
    } catch (_) {
      renderRanking();
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
        {type: 'application/json'}
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
    renderRanking();
    refreshStatus();
    state.pollTimer = setInterval(() => {
      renderRanking();
      if (!state.busy) refreshStatus();
    }, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, {once:true});
  } else {
    start();
  }

  window.DMSTop4BasketUI = {
    state,
    refreshStatus
  };
})();
