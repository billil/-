"""Read-only shared QQ records filtered by group grants; not wired into live AI."""
import sqlite3
import json
from pathlib import Path


class AuthorizedReader:
    def __init__(self, messages_path, authorization_path, bot_workspace):
        self.messages_path = str(Path(messages_path).resolve())
        self.authorization_path = str(Path(authorization_path).resolve())
        if not isinstance(bot_workspace, str) or not bot_workspace:
            raise ValueError('missing bot workspace')
        self.bot_workspace = bot_workspace

    def _read(self, account, group=None, message_id=None, limit=30):
        if not isinstance(account, str) or not account or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('invalid input')
        # Both databases are opened read-only; the grant is applied inside SQL,
        # before message text is returned to the caller or any model.
        db = sqlite3.connect(Path(self.messages_path).as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('ATTACH DATABASE ? AS acl', (Path(self.authorization_path).as_uri() + '?mode=ro',))
            predicates=['m.workspace=?','a.account_id=?','a.active=1','g.enabled=1']
            args=[self.bot_workspace, account]
            if group is not None:
                if not isinstance(group,str) or not group: raise ValueError('invalid group')
                predicates.append('m.group_label=?');args.append(group)
            if message_id is not None:
                if not isinstance(message_id,str) or not message_id: raise ValueError('invalid id')
                predicates.append('m.id=?');args.append(message_id)
            args.append(limit)
            return [dict(row) for row in db.execute('''SELECT m.id,m.group_label,m.content,m.sent_at,m.imported_at
                FROM main.messages m JOIN acl.group_access_members a ON a.group_key=m.group_label
                JOIN acl.group_access_groups g ON g.group_key=a.group_key
                WHERE '''+' AND '.join(predicates)+' ORDER BY m.imported_at DESC,m.id DESC LIMIT ?',args)]
        finally:
            db.close()

    def list_messages(self, account, group=None, limit=30):
        return self._read(account,group=group,limit=limit)

    def message(self, account, message_id):
        rows=self._read(account,message_id=message_id,limit=1)
        return rows[0] if rows else None

    def combined(self, account, limit=20, offset=0, message_id=None, order='desc'):
        """Own records plus explicitly granted bot groups, filtered in SQL."""
        if not isinstance(account,str) or not account or type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0 or order not in ('asc','desc'):
            raise ValueError('invalid pagination')
        db=sqlite3.connect(Path(self.messages_path).as_uri()+'?mode=ro',uri=True)
        try:
            db.row_factory=sqlite3.Row
            db.execute('ATTACH DATABASE ? AS acl',(Path(self.authorization_path).as_uri()+'?mode=ro',))
            where='''(m.workspace=? OR (m.workspace=? AND EXISTS (
                SELECT 1 FROM acl.group_access_members a JOIN acl.group_access_groups g ON g.group_key=a.group_key
                WHERE a.account_id=? AND a.group_key=m.group_label AND a.active=1 AND g.enabled=1)))'''
            args=[account,self.bot_workspace,account]
            if message_id is not None:
                if not isinstance(message_id,str) or not message_id:raise ValueError('invalid message')
                where+=' AND m.id=?';args.append(message_id)
            total=db.execute('SELECT count(*) FROM main.messages m WHERE '+where,args).fetchone()[0]
            ordering='ASC' if order=='asc' else 'DESC'
            rows=db.execute('''SELECT m.id,m.batch,m.group_label,m.content,m.sent_at,m.imported_at,m.metadata,m.revision,
                (SELECT g.display_name FROM acl.group_access_members a JOIN acl.group_access_groups g ON g.group_key=a.group_key
                 WHERE a.account_id=? AND a.group_key=m.group_label AND a.active=1 AND g.enabled=1 AND m.workspace=? LIMIT 1) AS display_name
                FROM main.messages m WHERE '''+where+f' ORDER BY m.imported_at {ordering},m.id {ordering} LIMIT ? OFFSET ?',
                [account,self.bot_workspace,*args,limit,offset]).fetchall()
            output=[]
            for row in rows:
                row=dict(row);display=row.pop('display_name');row['group_label']=display or row['group_label']
                if isinstance(row.get('metadata'),str):row['metadata']=json.loads(row['metadata'])
                output.append(row)
            return output,total
        finally:db.close()

    def combined_coverage(self, account):
        db=sqlite3.connect(Path(self.messages_path).as_uri()+'?mode=ro',uri=True)
        try:
            db.execute('ATTACH DATABASE ? AS acl',(Path(self.authorization_path).as_uri()+'?mode=ro',))
            where='''(m.workspace=? OR (m.workspace=? AND EXISTS (
                SELECT 1 FROM acl.group_access_members a JOIN acl.group_access_groups g ON g.group_key=a.group_key
                WHERE a.account_id=? AND a.group_key=m.group_label AND a.active=1 AND g.enabled=1)))'''
            args=[account,self.bot_workspace,account]
            count,start,end,unknown=db.execute('SELECT count(*),min(m.sent_at),max(m.sent_at),sum(m.sent_at IS NULL) FROM messages m WHERE '+where,args).fetchone()
            groups=[r[0] for r in db.execute('''SELECT DISTINCT COALESCE((SELECT g.display_name FROM acl.group_access_members a
                JOIN acl.group_access_groups g ON g.group_key=a.group_key WHERE a.account_id=? AND a.group_key=m.group_label
                AND a.active=1 AND g.enabled=1 AND m.workspace=? LIMIT 1),m.group_label)
                FROM messages m WHERE '''+where+' ORDER BY 1',[account,self.bot_workspace,*args])]
            return {'imported_count':count,'indexed_count':count,'sent_start':start,'sent_end':end,'unknown_time_count':unknown or 0,'groups':groups,'scope':'个人消息及已授权群消息'}
        finally:db.close()

    def snapshot(self, account, max_messages=1000):
        """Read all current grants and matching records together, or fail closed."""
        if not isinstance(account, str) or not account or type(max_messages) is not int or not 1 <= max_messages <= 10000:
            raise ValueError('invalid input')
        db = sqlite3.connect(Path(self.messages_path).as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('ATTACH DATABASE ? AS acl', (Path(self.authorization_path).as_uri() + '?mode=ro',))
            db.execute('BEGIN')
            groups = {r[0] for r in db.execute('''SELECT a.group_key FROM acl.group_access_members a
                JOIN acl.group_access_groups g ON g.group_key=a.group_key
                WHERE a.account_id=? AND a.active=1 AND g.enabled=1''',(account,))}
            rows = [dict(r) for r in db.execute('''SELECT m.id,m.group_label,m.content,m.sent_at,m.imported_at,m.metadata,m.revision
                FROM main.messages m WHERE m.workspace=? OR (m.workspace=? AND EXISTS (
                SELECT 1 FROM acl.group_access_members a JOIN acl.group_access_groups g ON g.group_key=a.group_key
                WHERE a.account_id=? AND a.group_key=m.group_label AND a.active=1 AND g.enabled=1))
                ORDER BY m.imported_at DESC,m.id DESC LIMIT ?''',(account,self.bot_workspace,account,max_messages+1))]
            if len(rows)>max_messages:
                raise ValueError('too many messages for isolated search')
            return groups, rows
        finally:
            db.close()
