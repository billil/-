import json,tempfile,unittest,threading,urllib.request,urllib.error,concurrent.futures
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from qunyi.config import Config
from qunyi.service import Service
from qunyi.providers import ProviderError
from qunyi.server import Server
TOKA='synthetic-test-token-space-a';TOKB='synthetic-test-token-space-b'
class Sequence:
    def __init__(self,*responses):self.responses=list(responses);self.calls=0
    def complete(self,context,timeout):
        self.calls+=1;r=self.responses.pop(0)
        if isinstance(r,Exception):raise r
        return r(context) if callable(r) else r
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cfg=Config(Path(self.tmp.name),'mock',{TOKA:'a',TOKB:'b'});self.app=Service(self.cfg)
    def tearDown(self):self.app.store.close();self.tmp.cleanup()
    def call(self,m,p,b=None,token=TOKA):return self.app.dispatch(m,p,b,'Bearer '+token)
    def seed(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'samples/messages.json').read_text(encoding='utf-8'))
        c,r=self.call('POST','/v1/imports',data);self.assertEqual(c,200);return r['items'][0]['message_id']
    def correction(self,old,place='图书馆南门',confirmed=True):
        c,r=self.call('POST','/v1/imports',{'messages':[{'content':'外出志愿服务地点改为'+place,'metadata':{'event_key':'service','title':'外出志愿服务','fields':{'地点':place},'supersedes':[old],'revision_kind':'correction','source_confirmed':confirmed}}]})
        self.assertEqual(r['success_count'],1);return r['items'][0]['message_id']
    def q(self,**kw):return self.call('POST','/v1/query',{'question':'志愿活动',**kw})
    def test_01_auth(self):self.assertEqual(self.call('GET','/v1/messages',token='bad')[0],401)
    def test_02_partial_import(self):
        _,r=self.call('POST','/v1/imports',{'messages':[{'content':'明天交表'},{'content':''},None]});self.assertEqual((r['success_count'],r['failure_count']),(1,2))
    def test_03_idempotency(self):
        b={'messages':[{'content':'独立通知'}],'idempotency_key':'same'};_,a=self.call('POST','/v1/imports',b);_,r=self.call('POST','/v1/imports',b)
        self.assertEqual(a['items'],r['items']);self.assertTrue(r['replayed']);b['messages'][0]['content']='不同';self.assertEqual(self.call('POST','/v1/imports',b)[0],409)
    def test_04_validation(self):
        _,r=self.call('POST','/v1/imports',{'messages':[{'content':'地点东门','sent_at':'bad'},{'content':'地点东门','metadata':{'fields':{'地点':'西门'}}}]});self.assertEqual(r['failure_count'],2)
    def test_05_cross_workspace(self):
        mid=self.seed();self.assertEqual(self.call('GET','/v1/messages/'+mid,token=TOKB)[0],404);self.assertEqual(self.call('DELETE','/v1/messages/'+mid,token=TOKB)[0],404);self.assertEqual(self.call('GET','/v1/coverage',token=TOKB)[1]['imported_count'],0)
    def test_06_coverage(self):
        self.seed();r=self.call('GET','/v1/coverage')[1];self.assertEqual((r['imported_count'],r['indexed_count'],r['unknown_time_count']),(3,3,1))
    def test_07_clarify(self):self.seed();self.assertEqual(self.q()[1]['status'],'clarify')
    def test_08_no_result(self):self.seed();self.assertEqual(self.q(question='宇宙兑换凭证')[1]['status'],'not_found')
    def test_09_versions(self):
        self.correction(self.seed());_,r=self.q(condition_updates={'event_key':'service'});self.assertEqual(r['status'],'answer');self.assertIn('图书馆南门',r['answer']);self.assertNotIn('地点：东门',r['answer']);self.assertFalse(r['ai']['actual_participation']);self.assertTrue(r['ai']['simulation'])
    def test_10_unconfirmed_conflict(self):
        self.correction(self.seed(),confirmed=False);self.assertEqual(self.q(condition_updates={'event_key':'service'})[1]['status'],'uncertain')
    def test_11_chain(self):
        old=self.seed();new=self.correction(old)
        self.call('POST','/v1/imports',{'messages':[{'content':'外出志愿服务时间更改为11月3日','metadata':{'event_key':'service','fields':{'时间':'11月3日'},'source_confirmed':True,'supersedes':[new],'revision_kind':'correction'}}]})
        r=self.q(condition_updates={'event_key':'service'})[1];self.assertIn('时间：11月3日',r['answer']);self.assertIn('地点：图书馆南门',r['answer'])
    def test_12_conditions(self):
        self.seed();_,r=self.q(condition_updates={'sent_after':'2026-11-01'});sid=r['session_id'];_,r=self.q(session_id=sid,condition_updates={'sent_after':'2026-10-01'});self.assertTrue(r['conditions']['sent_after'].startswith('2026-10-01'));self.assertEqual(r['condition_version'],2)
        _,r=self.q(session_id=sid,clear_conditions=['sent_after']);self.assertNotIn('sent_after',r['conditions'])
    def test_13_sessions(self):
        self.seed();_,a=self.q(condition_updates={'event_key':'service'});_,b=self.q(condition_updates={'event_key':'training'});self.assertNotEqual(a['session_id'],b['session_id']);self.assertEqual(self.call('GET','/v1/sessions/'+a['session_id'],token=TOKB)[0],404)
    def test_14_delete(self):
        mid=self.seed();_,r=self.q(condition_updates={'event_key':'service'});sid=r['session_id'];self.call('DELETE','/v1/messages/'+mid);self.assertEqual(self.call('GET','/v1/messages/'+mid)[0],404);self.assertEqual(self.call('GET','/v1/sessions/'+sid)[0],404);self.assertEqual(self.app.store.one('SELECT count(*) n FROM turns')['n'],0);self.assertEqual(self.app.store.one('SELECT count(*) n FROM search_index WHERE message_id=?',(mid,))['n'],0)
    def test_15_patch(self):
        mid=self.seed();c,r=self.call('PATCH','/v1/messages/'+mid,{'content':'全新天文讲座','metadata':{},'group_label':'单独测试群'});self.assertEqual(c,200);self.assertEqual(r['revision'],2);self.assertEqual(self.q(question='天文讲座',condition_updates={'group_label':'单独测试群'})[1]['status'],'uncertain')
    def test_16_restart(self):
        mid=self.seed();self.app.store.close();self.app=Service(self.cfg);self.assertEqual(self.call('GET','/v1/messages/'+mid)[0],200)
    def test_17_rebuild(self):
        self.seed();self.app.store.db.execute('DELETE FROM search_index');self.app.store.commit();self.assertEqual(self.call('POST','/v1/index/rebuild',{})[1]['indexed'],3)
    def test_18_live_missing(self):
        self.cfg.mode='live';c,r=self.q(question='宇宙凭证');self.assertEqual(c,503);self.assertEqual(r['status'],'error');self.assertEqual(r['ai']['calls'],1);self.assertFalse(r['ai']['simulation'])
    def test_19_live_empty_called(self):
        self.cfg.mode='live';p=Sequence({'action':'finish','status':'not_found'});self.app.provider=p;c,r=self.q();self.assertEqual(c,200);self.assertEqual(p.calls,1);self.assertTrue(r['ai']['actual_participation'])
    def test_20_live_timeout(self):
        self.cfg.mode='live';self.app.provider=Sequence(ProviderError('MODEL_TIMEOUT'));c,r=self.q();self.assertEqual(c,504);self.assertFalse(r['ai']['succeeded'])
    def test_21_call_limit(self):
        self.cfg.mode='live';self.app.provider=Sequence(*[{'action':'search','query':'志愿'}]*3);c,r=self.q();self.assertEqual(c,502);self.assertEqual(r['error']['code'],'MODEL_CALL_LIMIT')
    def test_22_model_read_denied(self):
        mid=self.seed();self.cfg.mode='live';self.app.provider=Sequence({'action':'read','message_ids':[mid]});c,r=self.call('POST','/v1/query',{'question':'志愿'},token=TOKB);self.assertEqual(c,502);self.assertEqual(r['error']['code'],'MODEL_TOOL_DENIED')
    def test_23_hallucination(self):
        mid=self.seed();self.cfg.mode='live';self.app.provider=Sequence({'action':'finish','status':'answer','fields':[{'name':'地点','value':'西门','message_id':mid,'quote':'地点西门'}]});self.assertEqual(self.q()[0],502)
    def test_24_stale(self):
        mid=self.seed();self.correction(mid);self.cfg.mode='live';self.app.provider=Sequence({'action':'finish','status':'answer','fields':[{'name':'地点','value':'东门','message_id':mid,'quote':'东门'}]});self.assertEqual(self.q()[0],502)
    def test_25_live_valid(self):
        mid=self.seed();self.cfg.mode='live';self.app.provider=Sequence({'action':'finish','status':'answer','fields':[{'name':'地点','value':'东门','message_id':mid,'quote':'东门'}]});c,r=self.q();self.assertEqual(c,200);self.assertTrue(r['ai']['succeeded'])
    def test_26_live_conditions(self):
        self.seed();self.cfg.mode='live';self.app.provider=Sequence({'action':'set_conditions','updates':{'event_key':'training'},'clear':[]},{'action':'finish','status':'uncertain'});c,r=self.q();self.assertEqual(c,200);self.assertEqual(r['conditions']['event_key'],'training')
    def test_27_logs(self):
        _,r=self.q(question='private-marker');_,log=self.call('GET','/v1/requests/'+r['request_id']);self.assertNotIn('private-marker',json.dumps(log));self.assertNotIn(TOKA,json.dumps(log));self.assertEqual(self.call('GET','/v1/requests/'+r['request_id'],token=TOKB)[0],404)
    def test_28_concurrency(self):
        b={'idempotency_key':'parallel','messages':[{'content':'并发测试'}]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as e:outs=list(e.map(lambda _:self.call('POST','/v1/imports',b),range(6)))
        self.assertEqual(len({x[1]['items'][0]['message_id'] for x in outs}),1)
    def test_29_cycle(self):
        a=self.seed();b=self.correction(a);self.assertEqual(self.call('PATCH','/v1/messages/'+a,{'metadata':{'event_key':'service','supersedes':[b]}})[0],400)
    def test_30_cancel(self):
        old=self.seed();self.call('POST','/v1/imports',{'messages':[{'content':'外出志愿服务已取消','metadata':{'event_key':'service','revision_kind':'cancellation','source_confirmed':True,'supersedes':[old],'fields':{'状态':'已取消'}}}]});r=self.q(condition_updates={'event_key':'service'})[1];self.assertTrue(r['event']['cancelled']);self.assertIn('已取消',r['answer'])
    def test_31_http(self):
        server=Server(('127.0.0.1',0),self.app);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base+'/health') as r:self.assertTrue(json.load(r)['ok'])
            req=urllib.request.Request(base+'/v1/imports',json.dumps({'messages':[{'content':'HTTP真实传输模拟数据'}]}).encode(),{'Authorization':'Bearer '+TOKA,'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['success_count'],1)
            req=urllib.request.Request(base+'/v1/query',b'{bad',{'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code,400)
        finally:server.shutdown();server.server_close();t.join()
    def test_32_provider_wire(self):
        class Fixture(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));assert payload['model']=='synthetic-model'
                blob=json.dumps({'choices':[{'message':{'content':json.dumps({'action':'finish','status':'not_found'})}}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(blob)));self.end_headers();self.wfile.write(blob)
            def log_message(self,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            from qunyi.providers import ChatProvider
            self.cfg.mode='live';self.cfg.endpoint=f'http://127.0.0.1:{server.server_port}/chat';self.cfg.model='synthetic-model';self.cfg.api_key='synthetic-fixture-only';self.cfg.allow_http_loopback=True;self.cfg.validate();self.app.provider=ChatProvider(self.cfg)
            c,r=self.q();self.assertEqual(c,200);self.assertTrue(r['ai']['actual_participation'])
        finally:server.shutdown();server.server_close();t.join()
    def test_33_backup(self):
        self.seed();dest=Path(self.tmp.name)/'copy.sqlite';self.app.store.backup(dest)
        import sqlite3
        c = sqlite3.connect(dest)
        try:
            self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0], 3)
        finally:
            c.close()
    def test_34_invalid_schema(self):
        self.seed();self.cfg.mode='live';self.app.provider=Sequence({'action':'finish','status':'answer','fields':'bad'});self.assertEqual(self.q()[0],502)
    def test_35_invalid_filters(self):
        self.assertEqual(self.q(condition_updates={'workspace':'b'})[0],400);self.assertEqual(self.q(condition_updates={'sent_after':'2026-12-01','sent_before':'2026-01-01'})[0],400);self.assertEqual(self.app.dispatch('GET','/v1/messages',authorization='Bearer '+TOKA,params={'limit':'oops'})[0],400)
if __name__=='__main__':unittest.main(verbosity=2)
