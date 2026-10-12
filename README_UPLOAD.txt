TRADEIN TOP-4 FULL REPLACEMENT FILES

These are complete replacement files, not patch scripts.
Copy each file into the same path in BasilOkoth/tradein and redeploy frontend and Render backend.

Changes:
- Tick-scored ranking snapshots include a canonical tick epoch.
- Reading ranking status no longer repeatedly overwrites tick provenance.
- Top-N basket uses last tick-scored ranking snapshot.
- Top-N interface automatically arms server tick feed when session exists, refreshes shared server rankings, and updates while basket is processing.
- Concurrency guard avoids overlapping status polls; errors are logged.
- Frontend cache-busting versions updated.

Safety: does NOT remove explicit real-money buy confirmation and does NOT suppress duplicate-order/settlement checks.

Validation: Python compileall and Node.js --check passed. No live Deriv deployment or purchase testing was performed.

Testing: Set DEMO, establish server session, verify ranking after >= 10 server ticks, leave open without clicking Refresh, confirm rank changes and Top-N status updates. Run a small demo cycle and inspect browser/network and Render logs if slow.
