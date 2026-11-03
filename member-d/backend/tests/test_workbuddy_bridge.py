"""HTTP bridge checks with a local SSE double, not a real WorkBuddy service."""
import json, os, tempfile, threading, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from qunyi.config import Config
from qunyi.service import Service
from qunyi.server import Server
TOKEN='test-workbuddy-bridge-token-123'
class FakeWorkBuddy(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*args): pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.__class__.requests.append((self.path,self.headers.get('x-wb-webapp-access-key'),data.get('stream')))
        payload=json.loads(data['messages'][1]['content'])
        if 'messages' in payload:
            m=payload['messages'][0]
            result={'answer':'图书馆南门','events':[{'summary':'志愿活动','location':'图书馆南门','time':None,'sources':[{'msg_id':m['msg_id'],'quote':m['content']}]}]}
        else:
            result={'keywords':['志愿活动'],'category':'any','time':{'type':'none'},'filters':{'explicit':[],'vague':[]},'ambiguous':None}
        chunk={'choices':[{'delta':{'content':json.dumps(result,ensure_ascii=False)}}]}
        raw=('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
class WorkBuddyBridgeTests(unittest.TestCase):
    def test_real_http_with_mock_sse_and_permissions(self):
        fake=ThreadingHTTPServer(('127.0.0.1',0),FakeWorkBuddy);thread=threading.Thread(target=fake.serve_forever,daemon=True);thread.start()
        env={'QUNYI_B_INTEGRATION':'1','LLM_PROVIDER':'workbuddy','WB_ENDPOINT':f'http://127.0.0.1:{fake.server_port}','WB_PUBLISHABLE_KEY':'test-only-key','WB_MODEL':'test-model'}
        try:
            with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,env,clear=True):
                app=Service(Config(state=Path(d),mode='live',tokens={TOKEN:'test'},allow_http_loopback=True))
                app.dispatch('POST','/v1/imports',{'messages':[{'content':'志愿活动集合地点图书馆南门','group_label':'test group'}]},'Bearer '+TOKEN)
                srv=Server(('127.0.0.1',0),app);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
                try:
                    req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}/v1/query',json.dumps({'question':'志愿活动'}).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+TOKEN})
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response: result=json.load(response)
                    self.assertTrue(result['ai']['actual_participation']);self.assertFalse(result['ai']['simulation'])
                    self.assertTrue(result['citations']);self.assertEqual(result['citations'][0]['quote'],'志愿活动集合地点图书馆南门')
                    self.assertGreaterEqual(len(FakeWorkBuddy.requests),2)
                    self.assertTrue(all(p=='/.cloud/llm/chat/completions' and key=='test-only-key' and stream is True for p,key,stream in FakeWorkBuddy.requests))
                    app.store.db.execute('UPDATE groups SET authorized=0 WHERE workspace=?',('test',))
                    before=len(FakeWorkBuddy.requests);_,denied=app.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer '+TOKEN)
                    self.assertEqual(denied['status'],'uncertain');self.assertEqual(len(FakeWorkBuddy.requests),before)
                    with patch.dict(os.environ,{'WB_ENDPOINT':'http://example.com'}):
                        code,bad=app.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer '+TOKEN)
                        self.assertEqual(code,503);self.assertEqual(bad['error']['code'],'B_MODEL_CONFIG_MISMATCH')
                    with patch.dict(os.environ,{'WB_PUBLISHABLE_KEY':''}):
                        code,missing=app.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer '+TOKEN)
                        self.assertEqual(code,503);self.assertEqual(missing['error']['code'],'MODEL_NOT_CONFIGURED')
                finally: srv.shutdown();srv.server_close();t.join();app.store.close()
        finally: fake.shutdown();fake.server_close();thread.join()
