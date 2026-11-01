import sqlite3, json, threading, uuid
from datetime import datetime, timezone

def uid(prefix): return prefix+'_'+uuid.uuid4().hex

def now(): return datetime.now(timezone.utc).isoformat()

def dump(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'))

class Store:
    def __init__(self,path):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(path),check_same_thread=False,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,idem TEXT,digest TEXT,result TEXT,created TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS batch_idem ON batches(workspace,idem) WHERE idem IS NOT NULL;
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,batch TEXT,group_label TEXT NOT NULL,
          content TEXT NOT NULL,sent_at TEXT,imported_at TEXT,metadata TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS messages_space ON messages(workspace);
        CREATE TABLE IF NOT EXISTS search_index(message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
          workspace TEXT NOT NULL,tokens TEXT NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,conditions TEXT NOT NULL,version INTEGER NOT NULL,created TEXT);
        CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          question TEXT NOT NULL,result TEXT NOT NULL,created TEXT);
        CREATE TABLE IF NOT EXISTS request_logs(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,route TEXT,status INTEGER,
          duration_ms INTEGER,metadata TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS groups(workspace TEXT NOT NULL,group_key TEXT NOT NULL,
          label TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',authorized INTEGER NOT NULL DEFAULT 0,
          last_received TEXT,last_event_id TEXT,created TEXT,updated TEXT,
          PRIMARY KEY(workspace,group_key));
        CREATE TABLE IF NOT EXISTS subscriptions(workspace TEXT NOT NULL,group_key TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,cursor TEXT,created TEXT,updated TEXT,
          PRIMARY KEY(workspace,group_key));
        CREATE TABLE IF NOT EXISTS sync_events(id TEXT PRIMARY KEY,source TEXT NOT NULL,
          group_key TEXT,received TEXT,status TEXT NOT NULL,message_id TEXT,error_code TEXT);
        CREATE INDEX IF NOT EXISTS sync_events_group ON sync_events(group_key,received);
        CREATE TABLE IF NOT EXISTS notification_changes(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL,
          group_key TEXT NOT NULL, message_id TEXT NOT NULL,
          kind TEXT NOT NULL, changed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS notification_changes_space_seq ON notification_changes(workspace,seq);
        CREATE TRIGGER IF NOT EXISTS notification_insert AFTER INSERT ON messages BEGIN
          INSERT INTO notification_changes(workspace,group_key,message_id,kind,changed_at)
          VALUES(NEW.workspace,NEW.group_label,NEW.id,'created',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
        END;
        CREATE TRIGGER IF NOT EXISTS notification_update AFTER UPDATE ON messages BEGIN
          INSERT INTO notification_changes(workspace,group_key,message_id,kind,changed_at)
          VALUES(OLD.workspace,OLD.group_label,OLD.id,CASE WHEN OLD.group_label != NEW.group_label THEN 'removed' ELSE 'updated' END,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
          INSERT INTO notification_changes(workspace,group_key,message_id,kind,changed_at)
          SELECT NEW.workspace,NEW.group_label,NEW.id,'created',strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE OLD.group_label != NEW.group_label;
        END;
        CREATE TRIGGER IF NOT EXISTS notification_delete AFTER DELETE ON messages BEGIN
          INSERT INTO notification_changes(workspace,group_key,message_id,kind,changed_at)
          VALUES(OLD.workspace,OLD.group_label,OLD.id,'deleted',strftime('%Y-%m-%dT%H:%M:%fZ','now'));
        END;
        ''')
        # One-time backfill for existing databases; do not duplicate it on restart.
        if not self.one('SELECT version FROM schema_version WHERE version=2'):
            self.db.execute("INSERT INTO notification_changes(workspace,group_key,message_id,kind,changed_at) SELECT workspace,group_label,id,'created',COALESCE(imported_at,?) FROM messages ORDER BY imported_at,id",(now(),))
            self.db.execute('INSERT INTO schema_version VALUES(2)')
        self.db.commit()

    def rows(self,sql,args=()):return [dict(x) for x in self.db.execute(sql,args).fetchall()]
    def one(self,sql,args=()):
        x=self.db.execute(sql,args).fetchone()
        return dict(x) if x else None
    def commit(self):self.db.commit()
    def close(self):self.db.close()
    def invalidate(self,workspace):
        # Queries can echo source text: clear all derived conversations, not just links.
        self.db.execute('DELETE FROM turns WHERE workspace=?',(workspace,))
        self.db.execute('DELETE FROM sessions WHERE workspace=?',(workspace,))
        # Batch results may contain metadata derived from deleted material.
        self.db.execute('DELETE FROM batches WHERE workspace=?',(workspace,))
    def log(self,rid,workspace,route,status,ms,metadata):
        self.db.execute('INSERT INTO request_logs VALUES(?,?,?,?,?,?,?)',(rid,workspace,route,status,ms,dump(metadata),now()))
        self.commit()
    def backup(self,target):
        with self.lock:
            dest=sqlite3.connect(str(target))
            try:self.db.backup(dest)
            finally:dest.close()
