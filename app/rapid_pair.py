"""Two-purchase threshold batch for DEMO and explicitly authorized REAL accounts.

Real mode uses explicit authorization, durable purchase journals and fail-closed checks.
Experimental: not audited with live Deriv real execution.
"""
import asyncio
import json
from datetime import datetime
from .db import SessionLocal
from .models import TradingSession, TradeLog


class RapidPair:
    def __init__(self, engine):
        self.engine = engine
        self.inflight = set()
        self.outstanding = {}
        self.tasks = {}

    async def reconcile_outstanding(self, sid, user_id, account_id):
        """Read Deriv's authoritative contract state for known OPEN contract IDs.

        Never guess the outcome of a SUBMITTING/UNCERTAIN BUY. The server may
        have sent it even when its response was lost. These require manual
        transaction-history verification.
        """
        db = SessionLocal()
        try:
            records = (db.query(TradeLog)
                .filter(TradeLog.trading_session_id == sid,
                        TradeLog.status.in_(['OPEN','SUBMITTING','UNCERTAIN']))
                .order_by(TradeLog.id).all())
            if not records:
                return {'resolved': 0, 'remaining': 0}
            unknown = [x for x in records if x.status != 'OPEN' or not x.contract_id]
            if unknown:
                return {'resolved': 0, 'remaining': len(records),
                        'reason': 'Purchase outcome unknown: verify Deriv transaction history manually',
                        'log_ids': [x.id for x in unknown]}
            client = await self.engine._client(user_id, account_id)
            resolved = 0
            for log in records:
                try:
                    response = await client.contract_status(str(log.contract_id))
                    contract = response.get('proposal_open_contract') or {}
                    if not contract.get('is_sold'):
                        continue
                    profit = contract.get('profit')
                    if profit is None:
                        continue
                    profit = float(profit)
                    log.status = str(contract.get('status') or 'SOLD').upper()
                    log.profit = profit
                    log.settled_at = datetime.utcnow()
                    session = db.get(TradingSession, sid)
                    if session:
                        session.pnl = float(session.pnl or 0) + profit
                        if str(contract.get('status') or '').lower() == 'won' or profit > 0:
                            session.running = False
                            session.paused = False
                            session.phase = 'RAPID_PAIR_WON_STOPPED'
                            session.pending_trade_json = None
                        session.updated_at = datetime.utcnow()
                    db.commit()  # commit each settlement for restart safety
                    resolved += 1
                except Exception as exc:
                    db.rollback()
                    return {'resolved': resolved, 'remaining': len(records)-resolved,
                            'reason': 'Deriv contract status check failed; no purchases permitted',
                            'detail': str(exc)}
            remaining = (db.query(TradeLog).filter(
                TradeLog.trading_session_id == sid,
                TradeLog.status.in_(['OPEN','SUBMITTING','UNCERTAIN'])).count())
            return {'resolved': resolved, 'remaining': remaining,
                    'reason': 'Known contract(s) still open on Deriv' if remaining else None}
        finally:
            db.close()

    async def try_batch(self, db, session, client):
        sid = session.id
        if sid in self.inflight or self.outstanding.get(sid):
            return
        if session.open_contract_id:
            session.phase = 'RAPID_PAIR_RECONCILE_EXISTING'
            db.commit()
            return
        from .config import settings
        mode = str(session.account_mode).upper()
        if mode not in ('DEMO', 'REAL'):
            session.phase = 'RAPID_PAIR_INVALID_MODE'
            db.commit()
            return
        if mode == 'REAL':
            if not settings.allow_real_mode:
                session.phase = 'REAL_DISABLED'
                db.commit()
                return
            grant = self.engine._pending_json(session.pending_trade_json)
            if (grant.get('kind') != 'REAL_RAPID_PAIR_AUTH' or
                int(grant.get('remaining') or 0) != 2 or
                int(grant.get('authorized_for_session') or -1) != int(sid) or
                float(grant.get('threshold') or -1) != float(session.v1_gate_threshold) or
                not grant.get('expires_at') or
                datetime.fromisoformat(grant['expires_at']) <= datetime.utcnow()):
                session.phase = 'REAL_PAIR_CONFIRMATION_REQUIRED'
                db.commit()
                return
        # Never buy when unresolved contract records exist, including after restarts.
        if db.query(TradeLog).filter(
            TradeLog.trading_session_id == sid,
            TradeLog.status.in_(['OPEN', 'SUBMITTING', 'UNCERTAIN'])
        ).count():
            session.phase='RAPID_PAIR_RECONCILIATION_REQUIRED'
            db.commit()
            return
        if session.current_trade + 2 > session.max_trades:
            session.phase = 'RAPID_PAIR_LIMIT'
            db.commit()
            return
        from .v1_gate import transition
        snap = self.engine._score_all_digits(sid)
        ranking = snap.get('ranking') or []
        score = float(ranking[0]['score']) if snap.get('ready') and ranking else None
        threshold = session.v1_gate_threshold
        if threshold is None:
            return
        state,count,allowed=transition(session.v1_gate_state or 'WAIT_HIGH',int(session.v1_gate_trades or 0),score,float(threshold))
        session.v1_gate_state = state
        if not allowed or state != 'ACTIVE':
            session.phase='V1_GATE_WAITING'
            db.commit()
            return
        digit=int(ranking[0]['digit'])
        session.candidate_digit=digit
        session.phase='RAPID_PAIR_BUYING'
        db.commit()
        self.inflight.add(sid)
        purchased=[]
        try:
            currency=await self.engine._currency_for(db, session)
            # Each buy must get a fresh proposal. Never repurchase if a BUY reply is uncertain.
            for index in range(2):
                db.refresh(session)
                if not session.running or session.paused:
                    break
                stake=round(float(session.current_stake),2)
                payload=await self.engine._request_proposal_payload(client,symbol=session.symbol,digit=digit,stake=stake,trade_no=int(session.current_trade)+1,currency=currency)
                # Persist intent before the external BUY; do not retry uncertain purchases.
                pending_log = TradeLog(
                    user_id=session.user_id, trading_session_id=sid,
                    trade_no=int(session.current_trade)+1, account_mode=session.account_mode,
                    account_id=session.account_id, symbol=session.symbol, digit=digit,
                    stake=stake, status='SUBMITTING',
                    raw_json=json.dumps({'rapid_pair':True, 'trigger_score':score,
                                         'trigger_threshold':threshold, 'proposal_id':str(payload['proposal_id'])})
                )
                db.add(pending_log)
                if mode == 'REAL':
                    # Consume one of two authorizations BEFORE contacting Deriv.
                    grant = self.engine._pending_json(session.pending_trade_json)
                    if grant.get('kind') != 'REAL_RAPID_PAIR_AUTH' or int(grant.get('remaining') or 0) <= 0:
                        raise RuntimeError('Real batch authorization exhausted')
                    grant['remaining'] -= 1
                    session.pending_trade_json = json.dumps(grant)
                db.commit()
                try:
                    if mode == 'REAL':
                        receipt = await client.buy_real_confirmed(
                            payload['proposal_id'], payload['ask_price'], authorization=True)
                    else:
                        receipt = await client.buy(payload['proposal_id'], payload['ask_price'], demo=True)
                except Exception as exc:
                    pending_log.status='UNCERTAIN'
                    session.running=False
                    session.phase='RAPID_PAIR_BUY_UNCERTAIN'
                    session.last_error=f'Buy outcome uncertain. Reconcile with Deriv account history: {exc}'
                    db.commit()
                    break
                item=receipt.get('buy') or {}
                cid=item.get('contract_id')
                if not cid:
                    pending_log.status='UNCERTAIN'
                    session.running=False
                    session.phase='RAPID_PAIR_BUY_UNCERTAIN'
                    session.last_error='Missing contract ID: reconcile transaction history before restarting'
                    db.commit()
                    break
                cid=str(cid)
                session.current_trade=int(session.current_trade)+1
                session.v1_gate_trades=int(session.v1_gate_trades or 0)+1
                purchased.append(cid)
                self.outstanding.setdefault(sid,set()).add(cid)
                pending_log.contract_id=cid
                pending_log.status='OPEN'
                pending_log.buy_price=float(item.get('buy_price') or payload['ask_price'])
                pending_log.payout=payload.get('payout')
                pending_log.raw_json=json.dumps({'rapid_pair':True,'deriv_buy':receipt,'trigger_score':score,'trigger_threshold':threshold})
                session.current_stake=round(stake*float(session.multiplier),2)
                session.phase='RAPID_PAIR_SETTLING_IN_BACKGROUND'
                db.commit()
                # Background settlement begins immediately, not a prerequisite for second purchase.
                task=asyncio.create_task(self._settle(sid,cid,session.user_id,session.account_id))
                self.tasks[(sid,cid)]=task
            if mode == 'REAL' and len(purchased) == 2:
                session.pending_trade_json = None
            if session.v1_gate_trades>=2:
                session.v1_gate_state='WAIT_LOW'
                self.engine.v1_gate_waiting_threshold[sid]=float(threshold)
                self.engine.v1_gate_low_observed.discard(sid)
            if not session.running:
                pass
            elif len(purchased)<2:
                session.phase='RAPID_PAIR_PARTIAL'
            else:
                session.phase='RAPID_PAIR_TWO_PURCHASED'
            db.commit()
        finally:
            self.inflight.discard(sid)

    async def _settle(self,sid,cid,user_id,account_id):
        try:
            client=await self.engine._client(user_id,account_id)
            for _ in range(180):
                await asyncio.sleep(0.5)
                try:
                    data=await client.contract_status(cid)
                except Exception:
                    continue
                poc=data.get('proposal_open_contract') or {}
                if not poc.get('is_sold'):
                    continue
                db=SessionLocal()
                try:
                    log=(db.query(TradeLog).filter(TradeLog.trading_session_id==sid,TradeLog.contract_id==cid).order_by(TradeLog.id.desc()).first())
                    s=db.get(TradingSession,sid)
                    if log and log.status=='OPEN':
                        profit=poc.get('profit')
                        if profit is None: break
                        log.status=str(poc.get('status') or 'SOLD').upper()
                        log.profit=float(profit)
                        log.settled_at=datetime.utcnow()
                        if s:
                            s.pnl=float(s.pnl or 0)+float(profit)
                            if str(poc.get('status') or '').lower() == 'won' or float(profit) > 0:
                                s.running=False
                                s.paused=False
                                s.phase='RAPID_PAIR_WON_STOPPED'
                                s.pending_trade_json=None
                        db.commit()
                    break
                finally:db.close()
        finally:
            self.outstanding.get(sid,set()).discard(cid)
            self.tasks.pop((sid,cid),None)
            # The OPEN database journal persists if settlement could not be confirmed.
