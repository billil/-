"""B facade with D workspace-scoped SQLite candidates and persistent sessions."""
import dataclasses, json, os
from datetime import datetime
from .providers import ProviderError
from .store import dump

class SessionStore:
    def __init__(self, db, workspace, sid, cls):
        self.db, self.workspace, self.sid, self.cls = db, workspace, sid, cls
    def get(self, sid):
        if sid != self.sid:return None
        row=self.db.execute('SELECT data FROM b_sessions WHERE workspace=? AND id=?',(self.workspace,self.sid)).fetchone()
        if not row:return None
        data=json.loads(row['data']);data['updated_at']=datetime.fromisoformat(data['updated_at']);session=self.cls(**data)
        return None if session.is_expired() else session
    def save(self, session):
        session.session_id=self.sid;session.updated_at=datetime.now()
        data=dataclasses.asdict(session);data['updated_at']=session.updated_at.isoformat()
        self.db.execute('INSERT INTO b_sessions VALUES(?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET data=excluded.data',(self.workspace,self.sid,dump(data)))
    def touch(self, sid):
        session=self.get(sid)
        if session:self.save(session)

def run(service, workspace, sid, question, conditions, stats):
    cfg=service.cfg
    if cfg.mode!='live' or not cfg.endpoint or not cfg.model or not cfg.api_key:raise ProviderError('MODEL_NOT_CONFIGURED')
    base=os.environ.get('LLM_BASE_URL','').rstrip('/')
    endpoint=base if base.endswith('/chat/completions') else base+'/chat/completions'
    if os.environ.get('LLM_PROVIDER','openai').lower() not in ('openai','openai-compatible','compatible') or endpoint!=cfg.endpoint.rstrip('/') or os.environ.get('LLM_MODEL')!=cfg.model or os.environ.get('LLM_API_KEY')!=cfg.api_key:raise ProviderError('B_MODEL_CONFIG_MISMATCH')
    try:
        from app import facade
        from app.session import Session
        from app.llm import CALL_STATS
    except ImportError:raise ProviderError('B_MODULE_UNAVAILABLE') from None
    store=SessionStore(service.store.db,workspace,sid,Session)
    scope=[row['group_key'] for row in service.store.rows('SELECT group_key FROM groups WHERE workspace=? AND authorized=1',(workspace,))]
    if not scope:return {'status':'uncertain','answer':'当前工作空间没有已授权的消息来源。','fields':[],'citations':[]},stats
    prior=store.get(sid);prior_dict=dataclasses.asdict(prior) if prior else None
    allowed={}
    def candidates(filters):
        # Do not silently drop unsupported hard filters.
        if filters.get('category') or filters.get('file_hint') or filters.get('time_range'):raise ProviderError('B_FILTER_UNSUPPORTED')
        opts=dict(conditions);group=filters.get('group_id')
        if group and opts.get('group_label') and group!=opts['group_label']:return []
        if group:opts['group_label']=group
        for name,target in (('start','sent_after'),('end','sent_before')):
            val=(filters.get('time_range') or {}).get(name)
            if val:opts[target]=(max(opts[target],val) if name=='start' else min(opts[target],val)) if opts.get(target) else val
        query=' '.join(filters.get('keywords') or []) or question
        rows=service.search.search(service.store,workspace,query,opts,limit=12,allowed_groups=scope)
        out=[]
        for row in rows:
            allowed[row['id']]=row
            out.append({'msg_id':row['id'],'content':row['content'],'sender':None,'send_time':row['sent_at'],'group_id':row['group_label'],'score':row.get('score',0)})
        return out
    marker=CALL_STATS.set(stats)
    try:result=facade.process(question,prior_dict,candidates,store,db_path=None,limit=12,group_scope=scope)
    except ProviderError:raise
    except Exception:raise ProviderError('B_PIPELINE_ERROR') from None
    finally:CALL_STATS.reset(marker)
    status=result.get('status')
    if status=='ask':return {'status':'clarify','question':(result.get('question') or '请补充查询条件')+' 请直接输入补充信息。','options':[],'fields':[],'citations':[]},stats
    if status in ('no_result','no_data'):return {'status':'not_found','answer':'当前可检索的消息中没有找到匹配内容。','fields':[],'citations':[]},stats
    evidence=list(result.get('evidence') or [])
    for event in (result.get('answer') or {}).get('events',[]):evidence.extend(event.get('sources') or [])
    cites=[]
    for item in evidence:
        mid,quote=item.get('msg_id'),item.get('quote');row=allowed.get(mid)
        if row and isinstance(quote,str) and quote and quote in row['content']:
            cites.append({'message_id':mid,'group_label':row['group_label'],'sent_at':row['sent_at'],'quote':quote})
    return {'status':'uncertain','answer':'找到相关原文，请核对来源。' if cites else '现有消息不足以核验结论，请补充条件。','fields':[],'citations':cites},stats
