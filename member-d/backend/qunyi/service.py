import hashlib, json, re, sqlite3, time
from datetime import datetime, timezone, timedelta
from .store import Store,uid,now,dump
from .search import LocalSearch
from .providers import ChatProvider,ProviderError

class ApiError(Exception):
    def __init__(self,status,code,message):self.status,self.code,self.message=status,code,message

def require(ok,code='INVALID_INPUT',message='请求字段不正确',status=400):
    if not ok:raise ApiError(status,code,message)
def text(value,name,limit=10000,empty=False):
    require(isinstance(value,str) and (empty or bool(value.strip())) and len(value)<=limit,message=name+' 格式或长度不正确')
    return value.strip()
def timestamp(value,end=False):
    if value is None:return None
    require(isinstance(value,str),message='时间应为 ISO 日期或带时区时间')
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            dt=datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            if end:dt+=timedelta(days=1,microseconds=-1)
        else:
            dt=datetime.fromisoformat(value.replace('Z','+00:00'))
            require(dt.tzinfo is not None,message='时间必须带时区')
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:raise ApiError(400,'INVALID_INPUT','日期不合法') from None

class Service:
    def __init__(self,cfg,provider=None):
        self.cfg=cfg.validate();cfg.state.mkdir(parents=True,exist_ok=True)
        self.store=Store(cfg.state/'qunyi.sqlite3');self.search=LocalSearch()
        self.provider=provider or ChatProvider(cfg)
    def workspace(self,authorization):
        token=authorization[7:] if isinstance(authorization,str) and authorization.startswith('Bearer ') else ''
        import hmac
        if not token.isascii():raise ApiError(401,'UNAUTHORIZED','无效的访问令牌')
        for key,space in self.cfg.tokens.items():
            if hmac.compare_digest(key,token):return space
        raise ApiError(401,'UNAUTHORIZED','缺少或无效的访问令牌')
    def public(self,row):
        if not row:return None
        row=dict(row);row.pop('workspace',None)
        if isinstance(row.get('metadata'),str):row['metadata']=json.loads(row['metadata'])
        return row
    def message(self,space,mid):
        require(isinstance(mid,str),message='消息标识不正确')
        r=self.store.one('SELECT * FROM messages WHERE workspace=? AND id=?',(space,mid))
        require(bool(r),'NOT_FOUND','消息不存在',404);return r
    def dispatch(self,method,path,body=None,authorization='',params=None):
        rid=uid('req');start=time.monotonic();space=None
        with self.store.lock:
            try:
                if method=='GET' and path=='/health':
                    self.store.one('SELECT 1 AS ok')
                    return 200,{'ok':True,'version':'0.1.0','request_id':rid}
                space=self.workspace(authorization)
                require(body is None or isinstance(body,dict),message='JSON 顶层必须为对象')
                status,data=self.route(method,path,body or {},space,params or {},rid,start)
                self.store.commit()
            except ApiError as e:
                self.store.db.rollback();status=e.status
                data={'status':'error','error':{'code':e.code,'message':e.message}}
            except Exception as internal:
                self.store.db.rollback();status=500
                data={'status':'error','error':{'code':'INTERNAL_ERROR','message':'服务处理失败，请用请求标识定位'}}
                internal_type=type(internal).__name__
            data['request_id']=rid
            if space:
                # No text, tokens, model URLs, or raw exception messages in logs.
                meta={'internal_type':locals().get('internal_type'),'error_code':data.get('error',{}).get('code'), 'ai':data.get('ai',{}), 'status':data.get('status')}
                try:self.store.log(rid,space,method+' '+self.log_route(path),status,int((time.monotonic()-start)*1000),meta)
                except sqlite3.Error:self.store.db.rollback()
            return status,data
    @staticmethod
    def log_route(path):
        if path.startswith('/v1/messages/'):return '/v1/messages/:id'
        if path.startswith('/v1/sessions/'):return '/v1/sessions/:id'
        if path.startswith('/v1/requests/'):return '/v1/requests/:id'
        return path if path in ('/v1/imports','/v1/messages','/v1/coverage','/v1/query','/v1/events','/v1/index/rebuild','/v1/capabilities') else '/unknown'
    def route(self,m,p,b,s,q,rid,start):
        if m=='GET' and p=='/v1/capabilities':
            return 200,{'mode':self.cfg.mode,'simulation':self.cfg.mode=='mock','model_configured':bool(self.cfg.endpoint and self.cfg.model and self.cfg.api_key),'search':self.search.name,'true_semantic_model':False,'workbuddy_certified':False,'max_calls':self.cfg.max_calls}
        if m=='POST' and p=='/v1/imports':return 200,self.imports(s,b)
        if m=='GET' and p=='/v1/messages':
            try:limit=int(q.get('limit','20'));offset=int(q.get('offset','0'))
            except (ValueError,TypeError):raise ApiError(400,'INVALID_INPUT','分页必须为整数')
            require(1<=limit<=100 and offset>=0,message='分页超出范围')
            rows=self.store.rows('SELECT * FROM messages WHERE workspace=? ORDER BY imported_at,id LIMIT ? OFFSET ?',(s,limit,offset))
            return 200,{'items':[self.public(x) for x in rows],'limit':limit,'offset':offset,'total':self.store.one('SELECT count(*) AS n FROM messages WHERE workspace=?',(s,))['n']}
        if p.startswith('/v1/messages/'):
            mid=p.split('/')[-1];row=self.message(s,mid)
            if m=='GET':return 200,self.public(row)
            if m=='DELETE':
                self.store.db.execute('DELETE FROM messages WHERE workspace=? AND id=?',(s,mid));self.store.invalidate(s)
                return 200,{'deleted':True,'message_id':mid,'derived_sessions_cleared':True,'index_status':'removed','backup_purge_required':True}
            if m=='PATCH':
                require(set(b)<= {'content','group_label','sent_at','metadata'} and bool(b),message='修订字段不正确')
                new={'content':b.get('content',row['content']),'group_label':b.get('group_label',row['group_label']),'sent_at':b.get('sent_at',row['sent_at']),'metadata':b.get('metadata',json.loads(row['metadata']))}
                new=self.validate_message(s,new,mid)
                self.store.db.execute('UPDATE messages SET content=?,group_label=?,sent_at=?,metadata=?,revision=revision+1 WHERE id=? AND workspace=?',(new['content'],new['group_label'],new['sent_at'],dump(new['metadata']),mid,s))
                self.search.index(self.store,self.message(s,mid));self.store.invalidate(s)
                return 200,self.public(self.message(s,mid))
        if m=='GET' and p=='/v1/coverage':return 200,self.coverage(s)
        if m=='POST' and p=='/v1/index/rebuild':
            self.store.db.execute('DELETE FROM search_index WHERE workspace=?',(s,))
            rows=self.store.rows('SELECT * FROM messages WHERE workspace=?',(s,))
            for row in rows:self.search.index(self.store,row)
            return 200,{'indexed':len(rows),'search':self.search.name}
        if m=='GET' and p=='/v1/events':
            rows=self.store.rows('SELECT * FROM messages WHERE workspace=? ORDER BY imported_at,id',(s,))
            groups={}
            for r in rows:
                key=json.loads(r['metadata']).get('event_key')
                if key:groups.setdefault(key,[]).append(r)
            return 200,{'items':[self.versions(s,key) for key in groups]}
        if m=='POST' and p=='/v1/query':
            result=self.query(s,b,rid,start)
            code=result.get('error',{}).get('code')
            http=503 if code=='MODEL_NOT_CONFIGURED' else 504 if code in ('MODEL_TIMEOUT','QUERY_DEADLINE') else 502 if result['status']=='error' else 200
            return http,result
        if m=='GET' and p.startswith('/v1/sessions/'):
            sid=p.split('/')[-1];session=self.session(s,sid)
            turns=self.store.rows('SELECT id,question,result,created FROM turns WHERE workspace=? AND session_id=? ORDER BY created,id',(s,sid))
            for t in turns:t['result']=json.loads(t['result'])
            return 200,{'id':sid,'conditions':json.loads(session['conditions']),'version':session['version'],'turns':turns}
        if m=='GET' and p.startswith('/v1/requests/'):
            row=self.store.one('SELECT * FROM request_logs WHERE workspace=? AND id=?',(s,p.split('/')[-1]))
            require(bool(row),'NOT_FOUND','请求记录不存在',404)
            row.pop('workspace');row['metadata']=json.loads(row['metadata']);return 200,row
        raise ApiError(404,'NOT_FOUND','接口不存在')
    def validate_message(self,s,item,mid=None):
        require(isinstance(item,dict),message='每条消息必须为对象')
        require(set(item)<={'content','group_label','sent_at','metadata'},message='消息包含未知字段')
        content=text(item.get('content'),'content',20000)
        group=text(item.get('group_label','未知来源'),'group_label',100)
        sent=timestamp(item.get('sent_at'))
        meta=item.get('metadata',{})
        require(isinstance(meta,dict) and set(meta)<={'event_key','title','fields','revision_kind','supersedes','source_confirmed'},message='metadata 字段不正确')
        if 'event_key' in meta:text(meta['event_key'],'event_key',100)
        if 'title' in meta:text(meta['title'],'title',200)
        require(meta.get('revision_kind','initial') in ('initial','correction','cancellation'),message='revision_kind 不正确')
        require(isinstance(meta.get('source_confirmed',False),bool),message='source_confirmed 应为布尔值')
        fields=meta.get('fields',{})
        require(isinstance(fields,dict) and len(fields)<=20,message='fields 应为最多20项的对象')
        for key,val in fields.items():
            text(key,'字段名',50);text(val,'字段值',1000)
            require(val in content,message='字段值必须逐字出现在原文中')
        supers=meta.get('supersedes',[])
        require(isinstance(supers,list) and len(supers)<=20 and all(isinstance(x,str) for x in supers),message='supersedes 应为消息标识数组')
        for old in supers:
            require(old!=mid,message='不能更正自己')
            target=self.message(s,old)
            if mid:
                def reaches(node,seen):
                    if node==mid:return True
                    if node in seen:return False
                    seen=seen|{node}
                    r=self.message(s,node)
                    return any(reaches(x,seen) for x in json.loads(r['metadata']).get('supersedes',[]))
                require(not reaches(old,set()),message='更正关联不能成环')
            require(meta.get('event_key') and json.loads(target['metadata']).get('event_key')==meta['event_key'],message='更正关系必须属于同一已标注事件')
        return {'content':content,'group_label':group,'sent_at':sent,'metadata':meta}
    def imports(self,s,b):
        require(set(b)<= {'messages','idempotency_key'},message='导入包含未知字段')
        items=b.get('messages');require(isinstance(items,list) and 1<=len(items)<=200,message='messages 数量必须为1至200')
        idem=b.get('idempotency_key')
        if idem is not None:text(idem,'idempotency_key',100)
        digest=hashlib.sha256(dump(items).encode()).hexdigest()
        if idem:
            old=self.store.one('SELECT * FROM batches WHERE workspace=? AND idem=?',(s,idem))
            if old:
                require(old['digest']==digest,'IDEMPOTENCY_CONFLICT','同一幂等键对应不同请求',409)
                result=json.loads(old['result']);result['replayed']=True;return result
        bid=uid('batch');success=[];errors=[]
        for i,item in enumerate(items):
            try:
                new=self.validate_message(s,item);mid=uid('msg')
                self.store.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',(mid,s,bid,new['group_label'],new['content'],new['sent_at'],now(),dump(new['metadata']),1))
                self.search.index(self.store,self.message(s,mid));success.append({'index':i,'message_id':mid,'index_status':'ready'})
            except ApiError as e:errors.append({'index':i,'code':e.code,'message':e.message})
        result={'batch_id':bid,'success_count':len(success),'failure_count':len(errors),'items':success,'errors':errors,'replayed':False}
        self.store.db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(bid,s,idem,digest,dump(result),now()))
        return result
    def coverage(self,s):
        r=self.store.one('SELECT count(*) AS imported_count,min(sent_at) AS sent_start,max(sent_at) AS sent_end,sum(sent_at IS NULL) AS unknown_time_count FROM messages WHERE workspace=?',(s,))
        r['unknown_time_count']=r['unknown_time_count'] or 0
        r['indexed_count']=self.store.one('SELECT count(*) AS n FROM search_index i JOIN messages m ON m.id=i.message_id AND m.revision=i.revision WHERE m.workspace=? AND i.workspace=?',(s,s))['n']
        r['groups']=[x['group_label'] for x in self.store.rows('SELECT DISTINCT group_label FROM messages WHERE workspace=? ORDER BY group_label',(s,))]
        r['scope']='仅覆盖当前令牌所属数据空间内已导入内容；时间为消息发送时间，不代表连续完整采集'
        return r
    def session(self,s,sid):
        require(isinstance(sid,str),message='session_id 不正确')
        r=self.store.one('SELECT * FROM sessions WHERE id=? AND workspace=?',(sid,s))
        require(bool(r),'NOT_FOUND','会话不存在或已因数据修订删除而失效',404);return r
    def versions(self,s,key):
        rows=[r for r in self.store.rows('SELECT * FROM messages WHERE workspace=? ORDER BY imported_at,id',(s,)) if json.loads(r['metadata']).get('event_key')==key]
        require(bool(rows),'NOT_FOUND','事件不存在',404)
        removed=set()
        for r in rows:
            m=json.loads(r['metadata'])
            if m.get('source_confirmed'):
                removed.update(m.get('supersedes',[]))
        active=[r for r in rows if r['id'] not in removed]
        fields={};changes=[]
        byid={r['id']:r for r in rows}
        def effective(r,seen):
            if r['id'] in seen: return {}
            seen=seen|{r['id']};meta=json.loads(r['metadata']);out={}
            if meta.get('source_confirmed'):
                for oldid in meta.get('supersedes',[]):
                    if oldid in byid:
                        for k,entries in effective(byid[oldid],seen).items():out.setdefault(k,[]).extend(entries)
            for name,val in meta.get('fields',{}).items():
                out[name]=[{'value':val,'message_id':r['id'],'quote':r['content']}]
            return out
        for r in active:
            for name,entries in effective(r,set()).items():fields.setdefault(name,[]).extend(entries)
        for r in rows:
            meta=json.loads(r['metadata'])
            for oldid in meta.get('supersedes',[]):
                if oldid in byid:
                    prior=effective(byid[oldid],set())
                    for name,val in meta.get('fields',{}).items():
                        for entry in prior.get(name,[]):
                            before=entry['value']
                            if before!=val:changes.append({'name':name,'before':before,'after':val,'from_message_id':entry['message_id'],'to_message_id':r['id'],'confirmed_by_importer':bool(meta.get('source_confirmed'))})
        conflicts=[k for k,v in fields.items() if len(set(x['value'] for x in v))>1]
        return {'event_key':key,'title':json.loads(rows[0]['metadata']).get('title',key),'messages':[self.public(r) for r in rows],'fields':fields,'changes':changes,'conflicts':conflicts,'cancelled':any(json.loads(r['metadata']).get('revision_kind')=='cancellation' for r in active),'source_note':'关联与来源确认由导入者标注，未完成发布者身份的独立认证'}
    def query(self,s,b,rid,start):
        require(set(b)<= {'question','session_id','condition_updates','clear_conditions','search_mode'},message='查询包含未知字段')
        question=text(b.get('question'),'question',2000)
        sid=b.get('session_id')
        if sid:
            session=self.session(s,sid);conditions=json.loads(session['conditions']);version=session['version']+1
        else:
            sid=uid('session');conditions={};version=1
        allowed={'query_text','event_key','group_label','sent_after','sent_before'}
        clear=b.get('clear_conditions',[]);updates=b.get('condition_updates',{})
        require(isinstance(clear,list) and all(isinstance(x,str) and x in allowed for x in clear),message='clear_conditions 不正确')
        require(isinstance(updates,dict) and set(updates)<=allowed,message='condition_updates 不正确')
        for k in clear:conditions.pop(k,None)
        for k,val in updates.items():
            if val is None:conditions.pop(k,None)
            elif k in ('sent_after','sent_before'):conditions[k]=timestamp(val,end=k=='sent_before')
            else:conditions[k]=text(val,k,1000)
        require(not (conditions.get('sent_after') and conditions.get('sent_before') and conditions['sent_after']>conditions['sent_before']),message='时间范围起止相反')
        # Explicit condition updates are authoritative. Free-text intent is live-provider responsibility;
        # mock mode never claims to understand arbitrary natural language corrections.
        if 'query_text' not in conditions:conditions['query_text']=question
        search_mode=b.get('search_mode','expanded')
        require(search_mode in ('keyword','expanded'),message='search_mode 不正确')
        query=conditions.get('query_text',question)
        candidates=self.search.search(self.store,s,query,conditions,expanded=search_mode=='expanded')
        ai={'mode':self.cfg.mode,'simulation':self.cfg.mode=='mock','attempted':False,'actual_participation':False,'calls':0,'succeeded':False,'error_code':None}
        trace=[{'tool':'search','count':len(candidates),'search':self.search.name}]
        try:
            if self.cfg.mode=='live':
                result=self.live(s,question,conditions,candidates,ai,trace,start)
            else:result=self.mock(s,question,conditions,candidates)
        except (ProviderError,ApiError) as e:
            code=e.code
            ai['error_code']=code
            result={'status':'error','error':{'code':code,'message':'真实模型未完成有效处理；没有回退到模拟答案'}}
        result.update({'session_id':sid,'condition_version':version,'conditions':conditions,'coverage':self.coverage(s),'ai':ai,'trace':trace,'search_mode':search_mode})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET conditions=excluded.conditions,version=excluded.version',(sid,s,dump(conditions),version,now()))
        self.store.db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(uid('turn'),s,sid,question,dump(result),now()))
        return result
    def mock(self,s,question,conditions,candidates):
        if not candidates:return {'status':'not_found','answer':'已导入且可检索内容中未找到相关消息。','fields':[],'citations':[]}
        groups={}
        for r in candidates:
            meta=json.loads(r['metadata']);key=meta.get('event_key',r['id']);groups.setdefault(key,[]).append(r)
        keys=list(groups)
        # Deterministic title selection only. No unsupported semantic-intent claim.
        selected=[k for k,rows in groups.items() if k in question or (json.loads(rows[0]['metadata']).get('title') or '\0') in question]
        if len(selected)==1:keys=selected
        if len(keys)>1 and not conditions.get('event_key'):
            options=[{'event_key':k,'title':json.loads(groups[k][0]['metadata']).get('title',groups[k][0]['content'][:50])} for k in keys[:8]]
            return {'status':'clarify','question':'找到多个候选，请选择事件，并通过 condition_updates.event_key 提交。','options':options,'fields':[],'citations':[]}
        key=keys[0]
        if not json.loads(groups[key][0]['metadata']).get('event_key'):
            return {'status':'uncertain','answer':'找到相关原文；模拟适配器未提取可核验结构化字段，请查看原文。','fields':[],'citations':[{'message_id':r['id'],'quote':r['content']} for r in groups[key]]}
        v=self.versions(s,key)
        if v['conflicts']:
            return {'status':'uncertain','answer':'发现字段冲突，请核实原通知。','event':v,'fields':[],'citations':[{'message_id':r['id'],'quote':r['content']} for r in groups[key]]}
        fields=[]
        for name,entries in v['fields'].items():
            fields.append({'name':name,**entries[0]})
        if v['cancelled']:
            for r in v['messages']:
                if r['metadata'].get('revision_kind')=='cancellation':
                    fields.append({'name':'通知状态','value':r['content'],'message_id':r['id'],'quote':r['content']})
        if not fields:return {'status':'uncertain','answer':'原文缺少可核对字段。','fields':[],'citations':[],'event':v}
        return self.answer(s,fields,{'event':v})
    def answer(self,s,fields,extra=None):
        require(isinstance(fields,list) and 1<=len(fields)<=30,'MODEL_INVALID_SCHEMA','模型字段结构无效',502)
        checked=[];cites=[]
        for f in fields:
            require(isinstance(f,dict) and set(f)=={'name','value','message_id','quote'},'MODEL_INVALID_SCHEMA','模型字段结构无效',502)
            name=text(f['name'],'字段名',50);value=text(f['value'],'字段值',1000);quote=text(f['quote'],'引用',20000)
            row=self.message(s,f['message_id'])
            require(quote in row['content'] and value in quote,'UNSUPPORTED_EVIDENCE','模型引用不能支持字段值',502)
            checked.append({'name':name,'value':value,'message_id':row['id'],'quote':quote})
            cites.append({'message_id':row['id'],'group_label':row['group_label'],'sent_at':row['sent_at'],'quote':quote})
        return {'status':'answer','answer':'；'.join(f['name']+'：'+f['value'] for f in checked),'fields':checked,'citations':cites,**(extra or {})}
    def live(self,s,question,conditions,candidates,ai,trace,start):
        context={'question':question,'conditions':conditions,'records':[self.public(r) for r in candidates], 'coverage':self.coverage(s),'tool_results':[]}
        allowed_ids=set(r['id'] for r in candidates)
        for turn in range(self.cfg.max_calls):
            remaining=self.cfg.deadline-(time.monotonic()-start)
            if remaining<=0:raise ProviderError('QUERY_DEADLINE')
            ai['attempted']=True;ai['calls']+=1
            response=self.provider.complete(context,min(self.cfg.timeout,remaining))
            ai['actual_participation']=True
            if time.monotonic()-start>self.cfg.deadline:raise ProviderError('QUERY_DEADLINE')
            action=response.get('action')
            trace.append({'model_turn':turn+1,'action':action if action in ('search','read','versions','set_conditions','clarify','finish') else 'invalid'})
            if action=='set_conditions':
                updates=response.get('updates',{});clear=response.get('clear',[])
                keys={'query_text','event_key','group_label','sent_after','sent_before'}
                require(isinstance(updates,dict) and set(updates)<=keys and isinstance(clear,list) and all(isinstance(k,str) and k in keys for k in clear),'MODEL_INVALID_SCHEMA','模型筛选字段无效',502)
                for k in clear:conditions.pop(k,None)
                for k,val in updates.items():
                    if val is None:conditions.pop(k,None)
                    elif k in ('sent_after','sent_before'):conditions[k]=timestamp(val,end=k=='sent_before')
                    else:conditions[k]=text(val,k,1000)
                require(not(conditions.get('sent_after') and conditions.get('sent_before') and conditions['sent_after']>conditions['sent_before']),'MODEL_INVALID_SCHEMA','模型时间范围无效',502)
                rows=self.search.search(self.store,s,conditions.get('query_text',question),conditions)
                allowed_ids=set(r['id'] for r in rows)
                context['conditions']=dict(conditions);context['records']=[self.public(r) for r in rows]
                data=context['records']
            elif action=='search':
                query=text(response.get('query'),'模型查询',1000)
                rows=self.search.search(self.store,s,query,conditions);allowed_ids.update(r['id'] for r in rows)
                data=[self.public(r) for r in rows]
            elif action=='read':
                ids=response.get('message_ids');require(isinstance(ids,list) and 1<=len(ids)<=12 and all(isinstance(x,str) and x in allowed_ids for x in ids),'MODEL_TOOL_DENIED','只能读取已检索候选',502)
                data=[self.public(self.message(s,x)) for x in ids]
            elif action=='versions':
                key=text(response.get('event_key'),'事件标识',100)
                require(any(json.loads(self.message(s,x)['metadata']).get('event_key')==key for x in allowed_ids),'MODEL_TOOL_DENIED','事件必须来自检索候选',502)
                data=self.versions(s,key);allowed_ids.update(r['id'] for r in data['messages'])
            elif action=='clarify':
                out={'status':'clarify','question':text(response.get('question'),'追问',500),'fields':[],'citations':[]}
                ai['succeeded']=True;return out
            elif action=='finish':
                status=response.get('status');require(status in ('answer','not_found','uncertain'),'MODEL_INVALID_SCHEMA','模型状态无效',502)
                if status=='answer':
                    fields=response.get('fields')
                    require(isinstance(fields,list) and all(isinstance(f,dict) and set(f)=={'name','value','message_id','quote'} and all(isinstance(f[k],str) for k in f) and f['message_id'] in allowed_ids for f in fields),'MODEL_TOOL_DENIED','答案只能引用已读取证据',502)
                    # Confirmed/unconfirmed structured conflicts cannot be hidden by model output.
                    for f in fields:
                        row=self.message(s,f['message_id']);key=json.loads(row['metadata']).get('event_key')
                        if key:
                            v=self.versions(s,key)
                            require(not v['conflicts'],'CONFLICTING_EVIDENCE','相关事件存在冲突，需核实',502)
                            valid=any(x['message_id']==f['message_id'] and x['value']==f.get('value') for x in v['fields'].get(f.get('name'),[]))
                            require(valid,'STALE_EVIDENCE','字段没有当前版本证据',502)
                        else:
                            require(f.get('name')=='原文摘录','UNSUPPORTED_FIELD','未标注消息仅支持原文摘录字段',502)
                    out=self.answer(s,fields)
                else:
                    if status=='not_found' and allowed_ids:status='uncertain'
                    out={'status':status,'answer':'已检索材料不足，请补充或核实。' if status=='uncertain' else '已导入且可检索内容中未找到相关消息。','fields':[],'citations':[]}
                ai['succeeded']=True;return out
            else:raise ProviderError('MODEL_INVALID_ACTION')
            context['tool_results'].append({'action':action,'result':data})
        raise ProviderError('MODEL_CALL_LIMIT')
