"""End-to-end B facade over HTTP using a local, synthetic model service."""
import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from qunyi.config import Config
from qunyi.service import Service
from qunyi.server import Server

TOKEN='synthetic-b-bridge-test-token'
PARSE={'keywords':['志愿活动'],'category':'any','time':{'type':'none'},'filters':{'explicit':[],'vague':[]},'ambiguous':None}

class Model(BaseHTTPRequestHandler):
    calls=0
    def log_message(self,*args):pass
    def do_POST(self):
        req=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        payload=json.loads(req['messages'][1]['content'])
        Model.calls+=1
        if 'messages' in payload:
            m=payload['messages'][0]
            response={'answer':'集合地点图书馆南门','events':[{'summary':'志愿活动','location':'图书馆南门','time':None,'sources':[{'msg_id':m['msg_id'],'quote':m['content']}]}]}
        else:response=PARSE
        raw=json.dumps({'choices':[{'message':{'content':json.dumps(response,ensure_ascii=False)}}]}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

class BIntegration(unittest.TestCase):
    def test_http_b_facade_database_and_restarted_session(self):
        model=ThreadingHTTPServer(('127.0.0.1',0),Model)
        model_thread=threading.Thread(target=model.serve_forever,daemon=True);model_thread.start()
        endpoint='http://127.0.0.1:%d/v1/chat/completions'%model.server_port
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'QUNYI_B_INTEGRATION':'1','LLM_BASE_URL':endpoint.rsplit('/chat/completions',1)[0], 'LLM_MODEL':'test-model','LLM_API_KEY':'test-only'},clear=False):
            config=Config(Path(tmp),'live',{TOKEN:'space-a','synthetic-b-bridge-other-token':'space-b'},endpoint,'test-model','test-only',allow_http_loopback=True)
            app=Service(config)
            app.dispatch('POST','/v1/imports',{'messages':[{'content':'志愿活动集合地点图书馆南门','group_label':'授权测试群'}]},'Bearer '+TOKEN)
            srv=Server(('127.0.0.1',0),app);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
            try:
                def ask(token,body):
                    request=urllib.request.Request('http://127.0.0.1:%d/v1/query'%srv.server_port,json.dumps(body).encode(),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    with urllib.request.urlopen(request) as resp:return json.load(resp)
                result=ask(TOKEN,{'question':'志愿活动'})
                self.assertTrue(result['ai']['actual_participation'])
                self.assertEqual(result['trace'][-1]['pipeline'],'b_facade')
                self.assertTrue(result['citations'])
                self.assertEqual(result['citations'][0]['group_label'],'授权测试群')
                sid=result['session_id']
                with self.assertRaises(urllib.error.HTTPError) as ctx:ask('synthetic-b-bridge-other-token',{'question':'志愿活动','session_id':sid})
                self.assertEqual(ctx.exception.code,404)
                self.assertGreaterEqual(Model.calls,2)
            finally:srv.shutdown();srv.server_close();t.join();app.store.close()
            reopened=Service(config)
            try:
                self.assertTrue(reopened.store.one('SELECT id FROM b_sessions WHERE workspace=? AND id=?',('space-a',sid)))
                code, follow = reopened.dispatch('POST','/v1/query',{'question':'志愿活动','session_id':sid},'Bearer '+TOKEN)
                self.assertEqual(code,200);self.assertEqual(follow['session_id'],sid)
                self.assertTrue(follow['citations']);self.assertEqual(follow['ai']['calls'],2)
                code, other = reopened.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer synthetic-b-bridge-other-token')
                self.assertEqual(code,200);self.assertEqual(other['status'],'uncertain');self.assertEqual(other['citations'],[])
                self.assertFalse(other['ai']['attempted'])
                reopened.store.db.execute('UPDATE groups SET authorized=0 WHERE workspace=?',('space-a',))
                reopened._ensure_group('space-a','授权测试群')
                self.assertEqual(reopened.store.one('SELECT authorized FROM groups WHERE workspace=?',('space-a',))['authorized'],0)
                code, denied = reopened.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer '+TOKEN)
                self.assertEqual(denied['status'],'uncertain');self.assertFalse(denied['ai']['attempted']);self.assertFalse(denied['citations'])
                reopened.store.db.execute('UPDATE groups SET authorized=1 WHERE workspace=?',('space-a',))
                with patch.dict(os.environ, {'LLM_MODEL':'wrong-model'}):
                    code, failed = reopened.dispatch('POST','/v1/query',{'question':'志愿活动'},'Bearer '+TOKEN)
                    self.assertEqual(code,503);self.assertEqual(failed['error']['code'],'B_MODEL_CONFIG_MISMATCH')
            finally:reopened.store.close()
        model.shutdown();model.server_close();model_thread.join()

if __name__=='__main__':unittest.main()
