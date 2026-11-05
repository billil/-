"""Identity-binding prototype. This module NEVER grants group privileges.

Callers must authenticate browser sessions and QQ event transport. Matching QQ
identifiers is an experimental integration assumption, not a role proof.
"""
import hashlib, hmac, secrets, sqlite3, time

class ClaimError(Exception): pass
class ClaimWorkflow:
    def __init__(self, db_path, secret, now=time.time):
        if not isinstance(secret,bytes) or len(secret)<32: raise ValueError('secret must have >=32 bytes')
        self.path=str(db_path);self.secret=secret;self.now=now
        with self._db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS claim_requests(
             id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, code_hash BLOB UNIQUE NOT NULL,
             created INTEGER NOT NULL, expires INTEGER NOT NULL, state TEXT NOT NULL,
             event_id TEXT UNIQUE, group_id TEXT);
            CREATE TABLE IF NOT EXISTS claim_events(
             event_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, operator_hash BLOB NOT NULL,
             received INTEGER NOT NULL, expires INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS claim_event_operator ON claim_events(operator_hash, received);
            ''')
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5);db.execute('PRAGMA foreign_keys=ON');return db
    def _mac(self, domain, value):return hmac.new(self.secret,(domain+'\0'+value).encode(),hashlib.sha256).digest()
    @staticmethod
    def _id(value):
        if not isinstance(value,str) or not value.strip() or len(value)>200:raise ClaimError('invalid identifier')
        return value.strip()
    def begin(self, authenticated_account):
        account=self._id(authenticated_account);now=int(self.now());code='QY-'+secrets.token_urlsafe(18)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # One active request per account; old codes become unusable.
            db.execute("UPDATE claim_requests SET state='superseded' WHERE account_id=? AND state='pending'",(account,))
            db.execute("INSERT INTO claim_requests(account_id,code_hash,created,expires,state) VALUES(?,?,?,?, 'pending')",(account,self._mac('code',code),now,now+600))
        return code
    def record_authenticated_join(self,event_id,group_id,operator_id,event_timestamp):
        # TRUST BOUNDARY: only authenticated server-side QQ bot transport may call this.
        event,group,operator=map(self._id,(event_id,group_id,operator_id));now=int(self.now())
        if type(event_timestamp) is not int or event_timestamp>now+30 or event_timestamp<now-300: raise ClaimError('stale or future join')
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO claim_events VALUES(?,?,?,?,?)',(event,group,self._mac('qq-id',operator),event_timestamp,now+600))
    def bind_authenticated_private_message(self,code,private_sender_id):
        # TRUST BOUNDARY: caller authenticates QQ event; never expose as browser API.
        code,sender=map(self._id,(code,private_sender_id));now=int(self.now())
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            claim=db.execute("SELECT id,created FROM claim_requests WHERE code_hash=? AND state='pending' AND expires>?",(self._mac('code',code),now)).fetchone()
            if not claim: raise ClaimError('invalid or expired code')
            candidates=db.execute('''SELECT event_id,group_id FROM claim_events
                WHERE operator_hash=? AND received>? AND expires>?
                AND event_id NOT IN (SELECT event_id FROM claim_requests WHERE event_id IS NOT NULL)
                ORDER BY received,event_id''',(self._mac('qq-id',sender),claim[1],now)).fetchall()
            # Multiple joins are ambiguous: never guess which group user owns.
            if len(candidates)!=1:raise ClaimError('join event missing or ambiguous')
            event,group=candidates[0]
            db.execute("UPDATE claim_requests SET state='identity_observed',event_id=?,group_id=? WHERE id=?",(event,group,claim[0]))
        return {'state':'identity_observed'}
    def status(self, authenticated_account):
        account=self._id(authenticated_account)
        with self._db() as db:
            row=db.execute('SELECT state,created,expires FROM claim_requests WHERE account_id=? ORDER BY id DESC LIMIT 1',(account,)).fetchone()
        if not row:return {'state':'none'}
        state,created,expires=row
        if state=='pending' and expires<=int(self.now()):state='expired'
        return {'state':state,'created':created,'expires':expires,'admin_granted':False}
    def prune(self):
        now=int(self.now())
        with self._db() as db:
            db.execute('DELETE FROM claim_events WHERE expires<=?',(now,))
            db.execute("DELETE FROM claim_requests WHERE expires<=? AND state IN ('pending','superseded')",(now,))
