import unittest,json,threading,http.client
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from qunyi.providers import ChatProvider,ProviderError
from tests import test_backend as common
# Use a separate class without re-running inherited test methods.
class Boundaries(unittest.TestCase):
    setUp=common.BackendTests.setUp;tearDown=common.BackendTests.tearDown
    call=common.BackendTests.call;seed=common.BackendTests.seed;q=common.BackendTests.q;correction=common.BackendTests.correction
    def test_unicode_token_rejected(self):self.assertEqual(self.call('GET','/v1/messages',token='不合法')[0],401)
    def test_invalid_json_shape(self):self.assertEqual(self.call('POST','/v1/imports',[])[0],400)
    def test_public_config_no_secret(self):
        self.cfg.api_key='synthetic-secret-marker';r=self.call('GET','/v1/capabilities')[1];self.assertNotIn('synthetic-secret-marker',json.dumps(r));self.assertFalse(r['true_semantic_model'])
    def test_provider_redirect_and_invalid_json(self):
        class F(BaseHTTPRequestHandler):
            count=0
            def do_POST(self):
                F.count+=1;self.rfile.read(int(self.headers['Content-Length']))
                if self.path=='/redirect':self.send_response(302);self.send_header('Location','/destination');self.end_headers()
                else:self.send_response(200);self.end_headers();self.wfile.write(b'{"choices":[]}')
            def log_message(self,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),F);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            self.cfg.allow_http_loopback=True;self.cfg.model='synthetic';self.cfg.api_key='synthetic-fixture';base=f'http://127.0.0.1:{server.server_port}'
            self.cfg.endpoint=base+'/redirect'
            with self.assertRaises(ProviderError) as e:ChatProvider(self.cfg).complete({},2)
            self.assertEqual(e.exception.code,'MODEL_HTTP_ERROR');self.assertEqual(F.count,1)
            self.cfg.endpoint=base+'/invalid'
            with self.assertRaises(ProviderError) as e:ChatProvider(self.cfg).complete({},2)
            self.assertEqual(e.exception.code,'MODEL_INVALID_JSON')
        finally:server.shutdown();server.server_close();t.join()
    def test_http_limits_and_content_type(self):
        from qunyi.server import Server
        server=Server(('127.0.0.1',0),self.app);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2);c.request('POST','/v1/query','{}',{'Content-Type':'text/plain'});r=c.getresponse();self.assertEqual(r.status,415);r.read();c.close()
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2);c.request('POST','/v1/query','',{'Content-Type':'application/json','Content-Length':'2000000'});r=c.getresponse();self.assertEqual(r.status,413);r.read();c.close()
        finally:server.shutdown();server.server_close();t.join()
    def test_cross_workspace_supersedes(self):
        from tests.test_backend import TOKB
        mid=self.seed();_,r=self.call('POST','/v1/imports',{'messages':[{'content':'模拟更正','metadata':{'event_key':'service','supersedes':[mid]}}]},token=TOKB);self.assertEqual(r['failure_count'],1)
    def test_deleted_reference_not_recovered_from_session(self):
        old=self.seed();self.correction(old);_,q=self.q(condition_updates={'event_key':'service'});self.call('DELETE','/v1/messages/'+old)
        self.assertEqual(self.call('GET','/v1/sessions/'+q['session_id'])[0],404)
        _,out=self.q(condition_updates={'event_key':'service'});self.assertNotIn('11月1日09:00',json.dumps(out,ensure_ascii=False))
    def test_malformed_model_field_types(self):
        self.seed();self.cfg.mode='live'
        self.app.provider=common.Sequence({'action':'finish','status':'answer','fields':[{'name':{},'value':'东门','message_id':'unknown','quote':'东门'}]})
        self.assertEqual(self.q()[0],502)
