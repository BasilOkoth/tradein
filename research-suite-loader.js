/*
 * DigitMatchStar Research Add-on Loader
 * Current main add-ons + Top-4 manual basket branch overlay.
 */
(() => {
  'use strict';

  const scripts = [
    '/research-cloud-sync.js?v=3.7-cross-device',
    '/tail-risk-model-v1.2-combined.js?v=tail-risk-v1-8',
    '/tick-dna-tail-validation-v2.js?v=2.6-hardstop',
    '/candidate-tick-dna-v3-shadow.js?v=3.7-exact-aligned-cloud',
    '/passive-forward-readiness-v1.js?v=1.0',
    '/trade-alignment-export.js?v=1.0',
    '/tick-rank-sync-v1.1.js?v=1.1',
    '/top4-basket-ui.js?v=1.0'
  ];

  function alreadyLoaded(src) {
    const requested = new URL(src, location.href);
    return Array.from(document.scripts).some(s => {
      try {
        const existing = new URL(s.src, location.href);
        return (
          existing.pathname === requested.pathname &&
          existing.search === requested.search
        );
      } catch (_) {
        return false;
      }
    });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (alreadyLoaded(src)) return resolve();

      const el = document.createElement('script');
      el.src = src;
      el.async = false;
      el.dataset.dmsResearchAddon = '1';
      el.onload = resolve;
      el.onerror = () => { console.warn(`[DMS Add-on] optional script unavailable: ${src}`); resolve(); };
      document.head.appendChild(el);
    });
  }

  async function start() {
    for (const src of scripts) {
      await loadScript(src);
    }

    console.info(
      '[DigitMatchStar] Existing research suite + Top-4 manual basket UI loaded.'
    );
  }

  if (document.readyState === 'loading') {
    document.addEventListener(
      'DOMContentLoaded',
      () => start().catch(err => console.error('[DMS Add-on]', err)),
      { once: true }
    );
  } else {
    start().catch(err => console.error('[DMS Add-on]', err));
  }
})();
