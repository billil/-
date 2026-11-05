"""Prototype: search using the existing backend over an authorized temporary snapshot.

Not wired to the gateway or production. A separate service instance is created
per independent search; old sessions are deliberately unavailable.
"""
from dataclasses import replace
from pathlib import Path
import secrets
import tempfile
import json
from datetime import datetime, timezone


class AuthorizationChanged(Exception):
    pass


class GuardedProvider:
    def __init__(self, provider, access, account, groups):
        self.provider, self.access, self.account, self.groups = provider, access, account, groups

    def complete(self, context, timeout):
        self.check()
        result = self.provider.complete(context, timeout)
        self.check()
        return result

    def check(self):
        if self.access.allowed_groups(self.account) != self.groups:
            raise AuthorizationChanged('group permissions changed during search')


def search_authorized(reader, access, account, cfg, provider, request, service_class, group_key=None):
    if not isinstance(request, dict) or request.get('session_id'):
        raise ValueError('only independent searches are supported')
    if cfg.mode != 'live':
        raise ValueError('live provider required')
    groups, rows = reader.snapshot(account)
    if group_key is not None:
        if not isinstance(group_key,str) or group_key not in groups:
            raise PermissionError('group unavailable')
        rows=[row for row in rows if row['group_label']==group_key]
    guarded = GuardedProvider(provider, access, account, groups)
    guarded.check()
    with tempfile.TemporaryDirectory(prefix='qunyi-authorized-') as tmp:
        token=secrets.token_urlsafe(32)
        isolated_cfg=replace(cfg,state=Path(tmp),tokens={token:'authorized'},qq_workspace='',qq_bot_secret='',qq_ingest_token='')
        service=service_class(isolated_cfg,provider=guarded)
        try:
            for row in rows:
                service.store.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',(
                    row['id'],'authorized','authorized-snapshot',row['group_label'],row['content'],
                    row['sent_at'],row['imported_at'],row['metadata'],row['revision']))
                item=service.store.one('SELECT * FROM messages WHERE id=? AND workspace=?',(row['id'],'authorized'))
                service.search.index(service.store,item)
            service.store.commit()
            status, result=service.dispatch('POST','/v1/query',request,'Bearer '+token)
            guarded.check()
            if status==200 and result.get('status') in ('uncertain','not_found') and not result.get('citations'):
                # Model uncertainty must not hide matching, authorized source records.
                # These are explicitly search candidates, not AI-verified answers.
                candidates=service.search.search(service.store,'authorized',request['question'],request.get('condition_updates',{}),limit=5)
                if candidates:
                    result['status']='uncertain'
                    result['answer']='找到可能相关的原文，请查看并核实。'
                    result['citations']=[{'message_id':r['id'],'group_label':r['group_label'],'sent_at':r['sent_at'],'quote':r['content'][:500]} for r in candidates]
                    result['candidate_source']='authorized_search_not_ai_answer'
            if status==200 and isinstance(result.get('options'),list):
                for option in result['options']:
                    if not isinstance(option,dict):continue
                    option.pop('sent_at',None);option.pop('group_label',None)
                    choices=[]
                    for row in rows:
                        meta=json.loads(row['metadata'])
                        if meta.get('event_key',row['id'])!=option.get('event_key'):continue
                        try:
                            date=datetime.fromisoformat((row.get('sent_at') or '').replace('Z','+00:00'))
                            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                            stamp=date.timestamp()
                        except (ValueError,TypeError,OverflowError,OSError):stamp=float('-inf')
                        choices.append((stamp,row))
                    if choices:
                        latest=max(choices,key=lambda x:x[0])[1]
                        option['sent_at']=latest.get('sent_at')
                        option['group_label']=latest.get('group_label')
            guarded.check()
            # Snapshot storage is destroyed after the request: it cannot support session lookup.
            result.pop('session_id',None)
            return status,result
        finally:
            service.store.close()
