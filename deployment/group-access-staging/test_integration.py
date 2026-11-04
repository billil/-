"""Actual HTTP on loopback, isolated synthetic accounts and database."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from account_store import AccountStore
from group_access import GroupAccess


class Integration(unittest.TestCase):
    def test_real_gateway_session_and_admin_approval(self):
        root=Path(__file__).parent
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'access').write_text('local-test-token');(p/'creds').write_text(json.dumps({'test-token':'owner_space'}))
            accounts=AccountStore(p/'db')
            accounts.setup_owner('owner1','owner-password-test','owner_space')
            accounts.create_account('alice','alice-password-test','alice_space')
            accounts.create_account('bob12','bob-password-test','bob_space')
            acl=GroupAccess(p/'db');acl.bootstrap('bot-group-opaque','测试群','owner_space')
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env=dict(os.environ,QUNYI_ACCESS_FILE=str(p/'access'),QUNYI_CREDENTIAL_FILE=str(p/'creds'),QUNYI_ACCOUNT_DB=str(p/'db'),QUNYI_GATEWAY_PORT=str(port),QUNYI_INTERNAL_WORKSPACE_KEY='k'*40)
            proc=subprocess.Popen([sys.executable,str(root/'gateway_account.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def req(method,path,body=None,cookie=None,origin='https://test.local'):
                h={'Host':'test.local','Origin':origin,'Content-Type':'application/json'}
                if cookie:h['Cookie']=cookie
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                c.request(method,path,json.dumps(body) if body is not None else None,h)
                r=c.getresponse();result=(r.status,r.getheader('Set-Cookie'),json.loads(r.read()));c.close();return result
            try:
                for _ in range(80):
                    try:req('GET','/auth/session');break
                    except OSError:time.sleep(.05)
                cookies={}
                for user,pw in [('owner1','owner-password-test'),('alice','alice-password-test'),('bob12','bob-password-test')]:
                    status,cookie,_=req('POST','/auth/login',{'username':user,'password':pw})
                    self.assertEqual(status,200);cookies[user]=cookie.split(';')[0]
                self.assertEqual(req('GET','/group-access/my-requests')[0],401)
                self.assertEqual(req('POST','/group-access/apply',{'group_key':'bot-group-opaque'},cookies['alice'],origin='https://evil.local')[0],403)
                status,_,data=req('POST','/group-access/apply',{'group_key':'bot-group-opaque','note':'我是群成员'},cookies['alice'])
                self.assertEqual(status,201);self.assertFalse(data['data_access_enabled']);rid=data['request_id']
                self.assertEqual(req('GET','/group-access/admin/requests',cookie=cookies['bob12'])[2]['items'],[])
                self.assertEqual(req('POST','/group-access/admin/decision',{'request_id':rid,'approve':True},cookies['bob12'])[0],403)
                status,_,data=req('POST','/group-access/admin/decision',{'request_id':rid,'approve':True},cookies['owner1'])
                self.assertEqual(status,200);self.assertFalse(data['data_access_enabled'])
                self.assertEqual(acl.allowed_groups('alice_space'),{'bot-group-opaque'})
                self.assertEqual(acl.allowed_groups('bob_space'),set())
                status,_,data=req('POST','/group-access/admin/revoke',{'group_key':'bot-group-opaque','account_id':'alice_space'},cookies['owner1'])
                self.assertEqual(status,200);self.assertEqual(acl.allowed_groups('alice_space'),set())
            finally:
                proc.terminate();proc.wait(timeout=5);proc.stderr.close()

if __name__=='__main__': unittest.main()
