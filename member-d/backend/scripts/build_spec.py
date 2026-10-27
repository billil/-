"""从约定生成静态 OpenAPI；不读取运行凭据。"""
import json
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    string={'type':'string'}
    metadata={'type':'object','additionalProperties':False,'properties':{
        'event_key':{'type':'string','maxLength':100},'title':{'type':'string','maxLength':200},
        'fields':{'type':'object','maxProperties':20,'additionalProperties':{'type':'string','maxLength':1000}},
        'revision_kind':{'enum':['initial','correction','cancellation']},'supersedes':{'type':'array','maxItems':20,'items':string},'source_confirmed':{'type':'boolean'}}}
    message={'type':'object','required':['content'],'additionalProperties':False,'properties':{
        'content':{'type':'string','minLength':1,'maxLength':20000},'group_label':{'type':'string','maxLength':100},'sent_at':{'type':['string','null'],'description':'ISO date or timezone-aware datetime'},'metadata':metadata}}
    condition={'type':'object','additionalProperties':False,'properties':{k:{'type':['string','null']} for k in ['query_text','event_key','group_label','sent_after','sent_before']}}
    imports={'type':'object','additionalProperties':False,'required':['messages'],'properties':{'messages':{'type':'array','minItems':1,'maxItems':200,'items':{'$ref':'#/components/schemas/MessageInput'}},'idempotency_key':{'type':'string','maxLength':100}}}
    query={'type':'object','additionalProperties':False,'required':['question'],'properties':{'question':{'type':'string','minLength':1,'maxLength':2000},'session_id':string,'condition_updates':condition,'clear_conditions':{'type':'array','items':{'enum':list(condition['properties'])}},'search_mode':{'enum':['keyword','expanded']}}}
    ai={'type':'object','properties':{'mode':{'enum':['mock','live']},'simulation':{'type':'boolean'},'attempted':{'type':'boolean'},'actual_participation':{'type':'boolean'},'calls':{'type':'integer'},'succeeded':{'type':'boolean'},'error_code':{'type':['string','null']}}}
    field={'type':'object','required':['name','value','message_id','quote'],'properties':{k:string for k in ['name','value','message_id','quote']}}
    result={'type':'object','required':['status','request_id'],'properties':{'status':{'enum':['answer','clarify','not_found','uncertain','error']},'session_id':string,'request_id':string,'conditions':condition,'condition_version':{'type':'integer'},'answer':string,'question':string,'fields':{'type':'array','items':field},'citations':{'type':'array','items':{'type':'object'}},'coverage':{'type':'object'},'ai':ai,'trace':{'type':'array','items':{'type':'object'}},'error':{'type':'object'}}}
    paths={}
    def op(method,path,summary,body=None,response=None):
        o={'summary':summary,'responses':{str(c):{'description':d} for c,d in [(200,'成功；批量导入也可能含逐条失败'),(400,'字段不合法'),(401,'未授权'),(404,'接口或对象不存在'),(409,'幂等键冲突'),(413,'请求过大'),(415,'需要JSON'),(500,'内部错误'),(502,'模型无效或证据不通过'),(503,'模型未配置'),(504,'模型超时')]}}
        if body:o['requestBody']={'required':True,'content':{'application/json':{'schema':body}}}
        if response:o['responses']['200']['content']={'application/json':{'schema':response}}
        if '{id}' in path:o['parameters']=[{'name':'id','in':'path','required':True,'schema':string}]
        if path=='/health':o['security']=[]
        if path=='/v1/messages':o['parameters']=[{'name':'limit','in':'query','schema':{'type':'integer','minimum':1,'maximum':100,'default':20}},{'name':'offset','in':'query','schema':{'type':'integer','minimum':0,'default':0}}]
        paths.setdefault(path,{})[method]=o
    op('get','/health','健康检查');op('get','/v1/capabilities','能力与模拟模式标识')
    op('post','/v1/imports','导入文本',imports)
    op('get','/v1/messages','消息列表');op('get','/v1/messages/{id}','消息详情')
    patch={k:v for k,v in message.items() if k!='required'};patch['minProperties']=1
    op('patch','/v1/messages/{id}','修订并更新索引；清理派生会话',patch);op('delete','/v1/messages/{id}','删除原文与索引；清理派生会话')
    op('get','/v1/coverage','覆盖统计');op('post','/v1/index/rebuild','重建当前空间索引',{'type':'object'})
    op('get','/v1/events','人工标注事件与更正证据');op('post','/v1/query','查询或继续会话',query,result)
    op('get','/v1/sessions/{id}','会话详情');op('get','/v1/requests/{id}','脱敏请求日志')
    spec={'openapi':'3.1.0','info':{'title':'群忆独立后端原型','version':'0.1.0','description':'未认证 WorkBuddy 联名版；真实外部模型未实测。详见 API.md。'},'servers':[{'url':'http://127.0.0.1:8000'}],'security':[{'bearerAuth':[]}],'paths':paths,'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}},'schemas':{'MessageInput':message,'ImportInput':imports,'QueryInput':query,'QueryResult':result}}}
    (root/'docs/openapi.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
