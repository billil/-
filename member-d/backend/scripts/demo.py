"""对已启动的服务执行完整 HTTP 演示；只导入包内模拟通知。"""
import argparse,json,os,urllib.request,urllib.error
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',default='http://127.0.0.1:8000');ap.add_argument('--credentials',default='state/credentials.json');ap.add_argument('--output',default='demo-report.json');a=ap.parse_args()
    token=os.environ.get('QUNYI_DEMO_TOKEN')
    if not token:token=next(iter(json.loads(Path(a.credentials).read_text(encoding='utf-8'))))
    results=[]
    def call(method,path,data=None):
        request=urllib.request.Request(a.base+path,None if data is None else json.dumps(data,ensure_ascii=False).encode(),{'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method)
        try:
            with urllib.request.urlopen(request,timeout=45) as r:status=r.status;obj=json.load(r)
        except urllib.error.HTTPError as e:status=e.code;obj=json.load(e)
        results.append({'method':method,'path':path,'http':status,'response':obj})
        if status>=400:raise RuntimeError('HTTP '+str(status)+' '+obj.get('error',{}).get('code',''))
        return obj
    root=Path(__file__).resolve().parents[1]
    capability=call('GET','/v1/capabilities')
    if capability['mode']!='mock':raise RuntimeError('演示脚本要求显式 mock 模式，不隐式向模型发数据')
    imported=call('POST','/v1/imports',json.loads((root/'samples/messages.json').read_text(encoding='utf-8')))
    original=imported['items'][0]['message_id']
    correction={'group_label':'模拟班级群','sent_at':'2026-10-28T12:00:00+08:00','content':'【模拟数据】外出志愿服务更正：集合地点改为图书馆南门，其他不变。','metadata':{'event_key':'service','title':'外出志愿服务','source_confirmed':True,'revision_kind':'correction','supersedes':[original],'fields':{'地点':'图书馆南门'}}}
    call('POST','/v1/imports',{'messages':[correction]})
    first=call('POST','/v1/query',{'question':'志愿活动'})
    assert first['status']=='clarify'
    answer=call('POST','/v1/query',{'question':'选择外出志愿服务','session_id':first['session_id'],'condition_updates':{'event_key':'service'}})
    assert answer['status']=='answer' and any(f['value']=='图书馆南门' for f in answer['fields'])
    call('GET','/v1/messages/'+answer['fields'][0]['message_id']);call('GET','/v1/coverage')
    empty=call('POST','/v1/query',{'question':'完全未知天文摄影兑换凭证'});assert empty['status']=='not_found'
    Path(a.output).write_text(json.dumps({'label':'模拟数据的真实本地 HTTP 运行；未调用外部 AI','steps':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print('HTTP demo passed. Report: '+a.output)
if __name__=='__main__':main()
