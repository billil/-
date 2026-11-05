import hashlib, hmac, http.client, http.server, importlib, json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time, types, unittest
from pathlib import Path
from workspace_identity import sign, verify
from account_store import AccountStore

class Capture(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        b=json.dumps({'workspace':verify(self.headers.get('Authorization','')[7:],os.environ['TEST_SIGNING_KEY'])}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)

class Multi(unittest.TestCase):
    def test_identity_signatures_expiry_tampering(self):
        key='k'*40; user='user_'+('a'*32); assertion=sign(user,key,now=1000)
        self.assertEqual(verify(assertion,key,now=1000),user)
        self.assertIsNone(verify(assertion,key,now=1061))
        self.assertIsNone(verify(assertion,'x'*40,now=1000))
        self.assertIsNone(verify(assertion.replace('user_a','user_b'),key,now=1000))
        with self.assertRaises(ValueError): sign('demo',key)

    def test_owner_and_registered_users_are_separate(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'access').write_text('test-token');(p/'creds').write_text(json.dumps({'backend-secret-token':'demo'}))
            owner=AccountStore(p/'db');owner.setup_owner('owner1','long-owner-password','demo')
            os.environ['TEST_SIGNING_KEY']='k'*40
            upstream=http.server.ThreadingHTTPServer(('127.0.0.1',0),Capture);threading.Thread(target=upstream.serve_forever,daemon=True).start()
            sk=socket.socket();sk.bind(('127.0.0.1',0));port=sk.getsockname()[1];sk.close()
            env=dict(os.environ,QUNYI_ACCESS_FILE=str(p/'access'),QUNYI_CREDENTIAL_FILE=str(p/'creds'),QUNYI_ACCOUNT_DB=str(p/'db'),QUNYI_UPSTREAM_PORT=str(upstream.server_port),QUNYI_GATEWAY_PORT=str(port),QUNYI_INTERNAL_WORKSPACE_KEY='k'*40)
            proc=subprocess.Popen([sys.executable,'multi-account/gateway_account.py'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def req(method,path,body=None,cookie=None,origin='https://test.local'):
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);h={'Host':'test.local','Origin':origin,'Content-Type':'application/json'}
                if cookie:h['Cookie']=cookie
                c.request(method,path,json.dumps(body) if body is not None else None,h);r=c.getresponse();out=r.read();result=(r.status,r.getheader('Set-Cookie'),json.loads(out));c.close();return result
            try:
                for _ in range(80):
                    try:req('GET','/auth/session');break
                    except OSError:time.sleep(.05)
                self.assertEqual(req('POST','/auth/register',{'username':'bad','password':'valid-password-123'},origin='https://evil.invalid')[0],403)
                cookies=[];spaces=[]
                for u in ('student_a','student_b'):
                    status,cookie,_=req('POST','/auth/register',{'username':u,'password':'valid-password-123','remember':True})
                    self.assertEqual(status,200);self.assertIn('HttpOnly',cookie);cookies.append(cookie.split(';')[0])
                    status,_,payload=req('GET','/api/v1/messages',cookie=cookies[-1]);self.assertEqual(status,200);spaces.append(payload['workspace'])
                self.assertTrue(all(x and x.startswith('user_') for x in spaces));self.assertNotEqual(*spaces)
                self.assertEqual(req('POST','/auth/register',{'username':'student_a','password':'valid-password-123'})[0],409)
                self.assertEqual(req('GET','/api/v1/messages')[0],401)
            finally:
                proc.terminate();proc.wait(timeout=5);proc.stderr.close();upstream.shutdown();upstream.server_close()

if __name__=='__main__':unittest.main()
