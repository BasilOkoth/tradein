#!/usr/bin/env python3
"""Patch BasilOkoth/tradein checkout. Run from repository root. Creates backups."""
from pathlib import Path
import re, sys, shutil, datetime, py_compile
root=Path.cwd()
required=['app/models.py','app/db.py','app/main.py','app/engine.py','bot.html']
if any(not (root/p).exists() for p in required):
    sys.exit('ERROR: Run this script from the root of a downloaded tradein repository (where bot.html lives).')
paths={p:root/p for p in required}
files={p:paths[p].read_text('utf-8') for p in required}
if 'v1_gate_threshold' in files['app/models.py']:
    sys.exit('Already patched. No changes made.')
def replace(p, old,new):
    if files[p].count(old)!=1: raise RuntimeError(f'Expected one occurrence in {p}, found {files[p].count(old)}: {old[:110]!r}')
    files[p]=files[p].replace(old,new,1)
replace('app/models.py', '    current_trade = Column(Integer, nullable=False, default=0)\n', '''    current_trade = Column(Integer, nullable=False, default=0)
    # NULL means legacy continuous trading; positive values enable the V1 gate.
    v1_gate_threshold = Column(Float, nullable=True)
    v1_gate_state = Column(String(20), nullable=False, default="WAIT_HIGH")
    v1_gate_trades = Column(Integer, nullable=False, default=0)
''')
# Migration is idempotent for PostgreSQL / SQLite. Do NOT drop or recreate existing tables.
p='app/db.py'
files[p]+='''

# Lightweight additive migration for existing installations. No user data is deleted.
from sqlalchemy import inspect, text
with engine.begin() as conn:
    columns = {c['name'] for c in inspect(conn).get_columns('trading_sessions')}
    for col, sqltype in (
        ('v1_gate_threshold', 'FLOAT'),
        ('v1_gate_state', "VARCHAR(20) DEFAULT 'WAIT_HIGH'"),
        ('v1_gate_trades', 'INTEGER DEFAULT 0'),
    ):
        if col not in columns:
            conn.execute(text(f'ALTER TABLE trading_sessions ADD COLUMN {col} {sqltype}'))
'''
replace('app/main.py','    max_trades: int = 15\n\n\nclass Candidate', '    max_trades: int = 15\n    v1_gate_threshold: float | None = None\n\n\nclass Candidate')
replace('app/main.py','    if body.max_trades < 1:\n        raise HTTPException(status_code=400, detail="max_trades must be >= 1")\n', '''    if body.max_trades < 1:
        raise HTTPException(status_code=400, detail="max_trades must be >= 1")
    if body.v1_gate_threshold is not None and not (0 < body.v1_gate_threshold <= 1000):
        raise HTTPException(status_code=400, detail="v1_gate_threshold must be > 0 and <= 1000, or blank")
''')
replace('app/main.py','        s.max_trades = int(body.max_trades)\n\n        s.current_trade = 0', '''        s.max_trades = int(body.max_trades)
        s.v1_gate_threshold = (float(body.v1_gate_threshold)
                               if body.v1_gate_threshold is not None else None)
        s.v1_gate_state = "WAIT_HIGH"
        s.v1_gate_trades = 0

        s.current_trade = 0''')
# Include gate data in returned session and live status when convenient.
replace('app/main.py','        s.current_trade = 0\n        s.pnl = 0.0', '        s.current_trade = 0\n        s.pnl = 0.0')
# Step sees canonical snapshots only on tick-coordinated wakeups. Gate is checked before proposals.
needle='''                if s.candidate_digit is None:
                    s.phase = "WAITING_CANDIDATE"
                    db.commit()
                    return

                currency = await self._currency_for(db, s)
'''
replacement='''                # OPTIONAL V1 GATE. Blank threshold = exact legacy execution path.
                if s.v1_gate_threshold is not None:
                    from .v1_gate import transition
                    snap = self._score_all_digits(s.id)
                    rank = snap.get("ranking") or []
                    # The first ranked digit is the V1 candidate (not the gap).
                    score = float(rank[0]["score"]) if snap.get("ready") and rank else None
                    state, count, allowed = transition(
                        s.v1_gate_state or "WAIT_HIGH",
                        int(s.v1_gate_trades or 0),
                        score,
                        float(s.v1_gate_threshold),
                    )
                    s.v1_gate_state = state
                    s.v1_gate_trades = count
                    if not allowed:
                        s.phase = "V1_GATE_WAITING"
                        s.updated_at = datetime.utcnow()
                        db.commit()
                        return
                    if count == 0 and rank:
                        s.candidate_digit = int(rank[0]["digit"])
                        self._set_live_next_target(s.id, s.candidate_digit)
                    s.updated_at = datetime.utcnow()
                    db.commit()

                if s.candidate_digit is None:
                    s.phase = "WAITING_CANDIDATE"
                    db.commit()
                    return

                currency = await self._currency_for(db, s)
'''
replace('app/engine.py',needle,replacement)
# Enforce at database purchase-claim boundary, including prearmed recovery buys.
needle='''        if abs(float(row.current_stake or 0) - stake) > 0.005:
            db.rollback()
            return None

        pending = self._pending_json(row.pending_trade_json)
'''
replacement='''        if abs(float(row.current_stake or 0) - stake) > 0.005:
            db.rollback()
            return None

        if row.v1_gate_threshold is not None:
            if row.v1_gate_state != "ACTIVE" or int(row.v1_gate_trades or 0) >= 2:
                db.rollback()
                return None

        pending = self._pending_json(row.pending_trade_json)
'''
replace('app/engine.py',needle,replacement)
needle='''        row.phase = f"BUY_CLAIMED_{trade_no}"
        row.updated_at = datetime.utcnow()
        db.commit()
'''
replacement='''        if row.v1_gate_threshold is not None:
            row.v1_gate_trades = int(row.v1_gate_trades or 0) + 1
            if row.v1_gate_trades >= 2:
                row.v1_gate_state = "WAIT_LOW"
        row.phase = f"BUY_CLAIMED_{trade_no}"
        row.updated_at = datetime.utcnow()
        db.commit()
'''
replace('app/engine.py',needle,replacement)
# GUI: append control adjacent to regular maxTrades input. Empty means disabled.
p='bot.html'
match=re.search(r'(<input\b[^>]*\bid="maxTrades"[^>]*>)',files[p],re.I)
if not match:
    raise RuntimeError('Cannot find #maxTrades control in bot.html')
widget='''
<div class="mt-3 rounded-lg border border-cyan-700 bg-slate-900/80 p-3" id="v1-gate-controls">
  <label for="v1GateThreshold" class="block text-sm font-bold text-cyan-300">Optional V1 Entry Threshold</label>
  <input id="v1GateThreshold" type="number" min="0.01" max="1000" step="0.01" placeholder="Blank = normal trading" class="mt-2 w-full rounded-md bg-gray-700 border border-gray-600 text-white px-3 py-2" />
  <div class="text-xs text-slate-400 mt-2">When enabled, wait for the rank #1 V1 score to reach the value, then allow at most TWO purchases. A fresh below-to-above crossing is needed for another batch. Leave blank for unchanged trading.</div>
</div>
'''
files[p]=files[p][:match.end()]+widget+files[p][match.end():]
needle='''                        max_trades: cfg.max_trades
'''
replacement='''                        max_trades: cfg.max_trades,
                        v1_gate_threshold: (() => {
                            const raw = document.getElementById('v1GateThreshold')?.value?.trim();
                            return raw ? Number(raw) : null;
                        })()
'''
replace(p,needle,replacement)
# Apply and compile. All originals are copied before modifications.
stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
backup=root/f'v1_gate_backup_{stamp}'
backup.mkdir()
for p in required:
    target=backup/p
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(paths[p],target)
for p,content in files.items():paths[p].write_text(content,'utf-8')
src=Path(__file__).parent/'v1_gate.py'
shutil.copy2(src,root/'app/v1_gate.py')
for p in ['app/engine.py','app/main.py','app/models.py','app/db.py','app/v1_gate.py']:
    py_compile.compile(str(root/p),doraise=True)
print('PATCH APPLIED. Backup:',backup)
print('No live trades started. Commit the changes to GitHub and test with DEMO before deployment.')
