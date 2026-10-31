#!/usr/bin/env python3
"""DigitMatchStar targeted DEMO lock reset (does not erase history).

Copy to repository root; run against the same DATABASE_URL as Render API:
  python reset_stuck_demo_1696.py                 # inspect only
  python reset_stuck_demo_1696.py --reset-demo    # archive unknown Demo attempt

Do not use for a REAL account. An archived uncertain Demo purchase remains
unreconciled historical evidence; no claim about its actual settlement is made.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from app.db import SessionLocal
from app.models import DerivAccount, TradeLog, TradingSession

LOG_ID = 1696
UNRESOLVED = {'OPEN', 'SUBMITTING', 'UNCERTAIN'}
ARCHIVED = 'DEMO_ARCHIVED_UNRECONCILED'


def snapshot(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reset-demo', action='store_true', help='Archive only uncertain Demo log 1696 and reset its Demo session')
    args = parser.parse_args()
    db = SessionLocal()
    try:
        log = db.get(TradeLog, LOG_ID)
        if log is None:
            raise SystemExit('Log 1696 not found; no changes.')
        session = db.get(TradingSession, log.trading_session_id)
        if not session:
            raise SystemExit('Owning session missing; no changes.')
        account = db.query(DerivAccount).filter(
            DerivAccount.user_id == log.user_id,
            DerivAccount.account_id == log.account_id,
        ).first()
        if not account:
            raise SystemExit('Account record missing; no changes.')
        if (str(log.account_mode).upper() != 'DEMO' or
            str(session.account_mode).upper() != 'DEMO' or
            str(account.account_type).upper() != 'DEMO' or
            log.user_id != session.user_id or
            log.account_id != session.account_id):
            raise SystemExit('REFUSED: record is not consistently associated with a DEMO account/session.')
        print(json.dumps({
            'log_id': log.id, 'session_id': session.id,
            'account_mode': session.account_mode, 'log_status': log.status,
            'contract_id': log.contract_id, 'open_contract_id': session.open_contract_id,
            'running': session.running, 'stake': log.stake,
        }, indent=2, default=str))
        if not args.reset_demo:
            print('Read-only inspection. To clear the DEMO-only lock: python reset_stuck_demo_1696.py --reset-demo')
            return
        if str(log.status).upper() not in UNRESOLVED:
            raise SystemExit('REFUSED: log 1696 is not in an unresolved state; nothing changed.')
        if log.contract_id or session.open_contract_id:
            raise SystemExit('REFUSED: contract ID exists; use normal settlement reconciliation.')
        if session.running:
            raise SystemExit('REFUSED: session is running; press STOP BOT first.')
        unresolved = db.query(TradeLog).filter(
            TradeLog.trading_session_id == session.id,
            TradeLog.status.in_(sorted(UNRESOLVED))
        ).all()
        if len(unresolved) != 1 or unresolved[0].id != LOG_ID:
            raise SystemExit('REFUSED: Other unresolved trades exist. No changes made.')

        now = datetime.now(timezone.utc)
        backup = Path(f'demo_log_{LOG_ID}_pre_reset_{now:%Y%m%dT%H%M%SZ}.json')
        with backup.open('x', encoding='utf-8') as f:
            json.dump({
                'archived_at_utc': now.isoformat(),
                'purpose': 'Demo-only operational reset, actual purchase outcome NOT established',
                'trade_log': snapshot(log), 'trading_session': snapshot(session),
            }, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        try:
            raw = json.loads(log.raw_json or '{}')
        except (ValueError, TypeError):
            raw = {'original_raw_json': log.raw_json}
        if not isinstance(raw, dict):
            raw = {'original_raw_json': raw}
        raw['demo_operational_reset'] = {
            'archived_at_utc': now.isoformat(),
            'previous_status': log.status,
            'outcome': 'UNKNOWN',
            'note': 'Demo-only lock archived without asserting a settlement or altering P/L',
        }
        log.raw_json = json.dumps(raw)
        log.status = ARCHIVED
        session.running = False
        session.paused = False
        session.pending_real_confirmation = False
        session.pending_trade_json = None
        session.phase = 'STOPPED'
        session.last_error = None
        session.updated_at = datetime.utcnow()
        db.commit()
        print(f'DONE: demo-only Log {LOG_ID} archived as {ARCHIVED}; START lock cleared.')
        print(f'Backup: {backup.resolve()}')
        print('No Real account touched; unknown Demo contract outcome remains in audit record.')
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == '__main__':
    main()
