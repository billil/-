import http.client
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        data=b'{"ok":true}'
        self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    do_POST=do_GET

class GatewayTests(unittest.TestCase):
    def test_owner_enrollment_and_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'access').write_text('temporary-demo-token');(p/'creds').write_text(json.dumps({'backend-token':'owner-space'}))
            upstream=http.server.ThreadingHTTPServer(('127.0.0.1',0),Stub)
            thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
            import socket
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env=dict(os.environ,QUNYI_ACCESS_FILE=str(p/'access'),QUNYI_CREDENTIAL_FILE=str(p/'creds'),QUNYI_ACCOUNT_DB=str(p/'db'),QUNYI_UPSTREAM_PORT=str(upstream.server_port),QUNYI_GATEWAY_PORT=str(port))
            process=subprocess.Popen([sys.executable,'multi-account/gateway_account.py'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def call(method,path,body=None,**headers):
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);c.request(method,path,json.dumps(body) if body is not None else None,headers)
                resp=c.getresponse();data=resp.read();result=(resp.status,dict(resp.getheaders()),json.loads(data));c.close();return result
            try:
                for _ in range(80):
                    try:call('GET','/auth/session');break
                    except OSError:time.sleep(.05)
                self.assertEqual(call('GET','/api/v1/messages')[0],401)
                self.assertEqual(call('GET','/api/v1/messages',Authorization='Bearer temporary-demo-token')[0],200)
                data={'username':'owner1','password':'a-long-test-password','access_token':'temporary-demo-token','remember':True}
                self.assertEqual(call('POST','/auth/setup',data,Origin='https://evil.invalid',Host='test.example')[0],403)
                status,headers,_=call('POST','/auth/setup',data,Origin='https://test.example',Host='test.example')
                self.assertEqual(status,200);cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie)
                cookie=cookie.split(';')[0]
                self.assertEqual(call('GET','/api/v1/messages',Cookie=cookie)[0],200)
                self.assertEqual(call('GET','/api/v1/messages',Authorization='Bearer temporary-demo-token')[0],401)
                self.assertEqual(call('POST','/auth/setup',data,Origin='https://test.example',Host='test.example')[0],400)
                self.assertEqual(call('POST','/api/v1/query',{},Cookie=cookie,Origin='https://evil.invalid',Host='test.example')[0],403)
                self.assertEqual(call('POST','/auth/logout',{},Cookie=cookie,Origin='https://test.example',Host='test.example')[0],200)
                self.assertEqual(call('GET','/api/v1/messages',Cookie=cookie)[0],401)
                self.assertEqual(call('POST','/auth/login',{'username':'owner1','password':'wrong'},Origin='https://test.example',Host='test.example')[0],401)
            finally:
                process.terminate();process.wait(timeout=5);process.stderr.close();upstream.shutdown();upstream.server_close()

if __name__=='__main__':unittest.main()
