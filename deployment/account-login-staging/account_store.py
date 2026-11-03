"""Local account/session store for the Qunyi gateway. No public registration."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

_ITERATIONS = 600_000
_LIFETIME = 30 * 24 * 3600

class AccountStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                  salt BLOB NOT NULL, digest BLOB NOT NULL,
                  workspace TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash BLOB PRIMARY KEY, account_id INTEGER NOT NULL,
                  expires INTEGER NOT NULL,
                  FOREIGN KEY(account_id) REFERENCES accounts(id));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def digest(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _ITERATIONS)

    def create_account(self, username, password, workspace):
        if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username):
            raise ValueError('账号须为3–32位字母、数字或下划线')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError('密码长度须为12–128位')
        if not workspace:
            raise ValueError('必须指定经授权的数据空间')
        salt = secrets.token_bytes(16)
        with self.connect() as db:
            db.execute('INSERT INTO accounts(username,salt,digest,workspace) VALUES (?,?,?,?)',
                       (username, salt, self.digest(password, salt), workspace))

    def login(self, username, password, remember=False):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 128:
            return None
        with self.connect() as db:
            row = db.execute('SELECT id,salt,digest FROM accounts WHERE username=? AND active=1', (username,)).fetchone()
            salt = row[1] if row else bytes(16)
            calculated = self.digest(password, salt)
            if not row or not hmac.compare_digest(calculated, row[2]):
                return None
            token = secrets.token_urlsafe(32)
            expires = int(time.time()) + (_LIFETIME if remember else 12 * 3600)
            db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).digest(), row[0], expires))
            return token, expires

    def lookup(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        hashed = hashlib.sha256(token.encode()).digest()
        with self.connect() as db:
            return db.execute('''SELECT a.username,a.workspace FROM sessions s JOIN accounts a ON a.id=s.account_id
                 WHERE s.token_hash=? AND s.expires>? AND a.active=1''', (hashed, int(time.time()))).fetchone()

    def logout(self, token):
        if isinstance(token, str) and len(token) < 256:
            with self.connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).digest(),))

    def configured(self):
        with self.connect() as db:
            return db.execute('SELECT EXISTS(SELECT 1 FROM accounts)').fetchone()[0] == 1

    def setup_owner(self, username, password, workspace):
        if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username):
            raise ValueError('账号须为3–32位字母、数字或下划线')
        if not isinstance(password, str) or not 12 <= len(password) <= 128 or not workspace:
            raise ValueError('密码须为12–128位，并配置数据空间')
        salt = secrets.token_bytes(16)
        digest = self.digest(password, salt)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT EXISTS(SELECT 1 FROM accounts)').fetchone()[0]:
                raise ValueError('账号已设置')
            db.execute('INSERT INTO accounts(username,salt,digest,workspace) VALUES(?,?,?,?)', (username,salt,digest,workspace))
