"""Group access requests: independent prototype, not an API or enforcement layer."""
import sqlite3
import time
from contextlib import contextmanager


class GroupAccess:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS group_access_groups (
              group_key TEXT PRIMARY KEY, display_name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS group_access_admins (
              group_key TEXT NOT NULL REFERENCES group_access_groups(group_key),
              account_id TEXT NOT NULL, PRIMARY KEY(group_key,account_id));
            CREATE TABLE IF NOT EXISTS group_access_aliases (
              alias TEXT PRIMARY KEY,
              group_key TEXT NOT NULL REFERENCES group_access_groups(group_key));
            CREATE TABLE IF NOT EXISTS group_access_requests (
              id INTEGER PRIMARY KEY, group_key TEXT NOT NULL REFERENCES group_access_groups(group_key),
              account_id TEXT NOT NULL, note TEXT NOT NULL, status TEXT NOT NULL,
              created_at INTEGER NOT NULL, decided_at INTEGER, decided_by TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS group_access_pending
              ON group_access_requests(group_key,account_id) WHERE status='pending';
            CREATE TABLE IF NOT EXISTS group_access_members (
              group_key TEXT NOT NULL REFERENCES group_access_groups(group_key),
              account_id TEXT NOT NULL, active INTEGER NOT NULL,
              granted_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
              PRIMARY KEY(group_key, account_id));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _check(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError('invalid identifier')
        return value.strip()

    def bootstrap(self, group_key, name, admin):
        """Trusted operator only: caller MUST verify bot group and admin offline."""
        group_key, name, admin = map(self._check, (group_key, name, admin))
        with self._db() as db:
            db.execute('INSERT INTO group_access_groups VALUES(?,?,1)', (group_key, name))
            db.execute('INSERT INTO group_access_admins VALUES(?,?)', (group_key, admin))

    def apply(self, group_key, account_id, note=''):
        group_key, account_id = map(self._check, (group_key, account_id))
        if not isinstance(note, str) or len(note) > 300:
            raise ValueError('invalid note')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            alias = db.execute('SELECT group_key FROM group_access_aliases WHERE alias=?',(group_key,)).fetchone()
            if alias:
                group_key=alias[0]
            if not db.execute('SELECT 1 FROM group_access_groups WHERE group_key=? AND enabled=1', (group_key,)).fetchone():
                raise ValueError('group unavailable')
            if db.execute('SELECT 1 FROM group_access_members WHERE group_key=? AND account_id=? AND active=1',(group_key,account_id)).fetchone():
                raise ValueError('already authorized')
            cur = db.execute("INSERT INTO group_access_requests(group_key,account_id,note,status,created_at) VALUES(?,?,?,'pending',?)",(group_key,account_id,note,int(time.time())))
            return cur.lastrowid

    def decide(self, request_id, admin, approve):
        admin = self._check(admin)
        if type(approve) is not bool:
            raise ValueError('invalid decision')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT r.group_key,r.account_id FROM group_access_requests r JOIN group_access_groups g ON g.group_key=r.group_key WHERE r.id=? AND r.status='pending' AND g.enabled=1",(request_id,)).fetchone()
            if not row or not db.execute('SELECT 1 FROM group_access_admins WHERE group_key=? AND account_id=?',(row[0],admin)).fetchone():
                raise PermissionError('request unavailable')
            now = int(time.time())
            db.execute('UPDATE group_access_requests SET status=?,decided_at=?,decided_by=? WHERE id=?',('approved' if approve else 'rejected',now,admin,request_id))
            if approve:
                db.execute('''INSERT INTO group_access_members VALUES(?,?,1,?,?)
                  ON CONFLICT(group_key,account_id) DO UPDATE SET active=1,granted_by=excluded.granted_by,updated_at=excluded.updated_at''',(*row,admin,now))

    def revoke(self, group_key, account_id, admin):
        group_key, account_id, admin = map(self._check,(group_key,account_id,admin))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM group_access_admins WHERE group_key=? AND account_id=?',(group_key,admin)).fetchone():
                raise PermissionError('not an admin')
            db.execute('UPDATE group_access_members SET active=0,updated_at=? WHERE group_key=? AND account_id=?',(int(time.time()),group_key,account_id))

    def allowed_groups(self, account_id):
        account_id = self._check(account_id)
        with self._db() as db:
            return {row[0] for row in db.execute('''SELECT m.group_key FROM group_access_members m
                JOIN group_access_groups g ON g.group_key=m.group_key
                WHERE m.account_id=? AND m.active=1 AND g.enabled=1''',(account_id,))}

    def my_groups(self, account_id):
        account_id = self._check(account_id)
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('''SELECT g.group_key,g.display_name FROM group_access_members m
                JOIN group_access_groups g ON g.group_key=m.group_key
                WHERE m.account_id=? AND m.active=1 AND g.enabled=1 ORDER BY g.display_name,g.group_key''',(account_id,))]

    def my_requests(self, account_id):
        account_id = self._check(account_id)
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('''SELECT r.id,r.group_key,g.display_name,r.status,r.created_at,r.decided_at
                FROM group_access_requests r JOIN group_access_groups g USING(group_key)
                WHERE r.account_id=? ORDER BY r.id DESC LIMIT 100''',(account_id,))]

    def admin_requests(self, admin):
        admin = self._check(admin)
        with self._db() as db:
            db.row_factory = sqlite3.Row
            has_accounts = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone()
            join = 'LEFT JOIN accounts u ON u.workspace=r.account_id' if has_accounts else ''
            display = 'COALESCE(u.username,r.account_id)' if has_accounts else 'r.account_id'
            return [dict(r) for r in db.execute('''SELECT r.id,r.group_key,g.display_name,r.account_id,r.note,r.created_at, '''+display+''' AS account_name
                FROM group_access_requests r JOIN group_access_groups g USING(group_key)
                JOIN group_access_admins a ON a.group_key=r.group_key
                '''+join+''' WHERE a.account_id=? AND r.status='pending' AND g.enabled=1 ORDER BY r.id LIMIT 100''',(admin,))]

    def admin_members(self, admin):
        admin = self._check(admin)
        with self._db() as db:
            db.row_factory = sqlite3.Row
            has_accounts = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone()
            join = 'LEFT JOIN accounts u ON u.workspace=m.account_id' if has_accounts else ''
            display = 'COALESCE(u.username,m.account_id)' if has_accounts else 'm.account_id'
            return [dict(r) for r in db.execute('''SELECT m.group_key,g.display_name,m.account_id, '''+display+''' AS account_name
                FROM group_access_members m JOIN group_access_groups g ON g.group_key=m.group_key
                JOIN group_access_admins a ON a.group_key=m.group_key '''+join+'''
                WHERE a.account_id=? AND m.active=1 AND g.enabled=1 ORDER BY g.display_name,account_name LIMIT 200''',(admin,))]
