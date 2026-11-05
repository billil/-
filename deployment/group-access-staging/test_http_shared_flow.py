"""Loopback gateway, SQLite and existing search engine; fake provider only."""
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from account_store import AccountStore
from group_access import GroupAccess


RUNNER = r'''
from pathlib import Path
from qunyi.config import Config
from qunyi.service import Service
from authorized_reader import AuthorizedReader
from isolated_search import search_authorized
import gateway_account as gateway
import http.server,os
class FakeProvider:
    def complete(self,context,timeout):
        records=context['records']
        if not records:return {'action':'finish','status':'not_found','fields':[]}
        r=records[0]
        return {'action':'finish','status':'answer','fields':[{'name':'原文摘录','value':r['content'],'message_id':r['id'],'quote':r['content']}]}
reader=AuthorizedReader(os.environ['TEST_MSG_DB'],gateway.AUTH_DB,'bot')
cfg=Config(state=Path(os.environ['TEST_STATE']),mode='live',tokens={'x'*20:'demo'})
gateway.GROUP_READER=reader
gateway.GROUP_QUERY=lambda account,data:search_authorized(reader,gateway.GROUP_ACCESS,account,cfg,FakeProvider(),dict({'question':data['question']},**({'condition_updates':{'event_key':data['event_key']}} if data.get('event_key') else {})),Service,group_key=data.get('group_key'))
gateway.MAIN_QUERY=lambda account,data:search_authorized(reader,gateway.GROUP_ACCESS,account,cfg,FakeProvider(),data,Service)
http.server.ThreadingHTTPServer(('127.0.0.1',gateway.LISTEN_PORT),gateway.Handler).serve_forever()
'''


class SharedFlow(unittest.TestCase):
    def test_request_approve_read_search_and_revoke(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'access').write_text('access-token-test');(p/'creds').write_text(json.dumps({'token-secret':'owner'}))
            acc=AccountStore(p/'accounts');acc.setup_owner('owner','owner-password-test','owner-space')
            acc.create_account('alice','alice-password-test','alice-space');acc.create_account('bobby','bobby-password-test','bob-space')
            acl=GroupAccess(p/'accounts');acl.bootstrap('group-a','A','owner-space');acl.bootstrap('group-b','B','owner-space')
            with sqlite3.connect(p/'messages') as db:
                db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,workspace TEXT,batch TEXT,group_label TEXT,content TEXT,sent_at TEXT,imported_at TEXT,metadata TEXT,revision INTEGER)')
                db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',[
                    ('m1','bot','b','group-a','志愿活动图书馆集合',None,'2026-01-01','{}',1),
                    ('m2','bot','b','group-b','志愿活动不可见秘密',None,'2026-01-02','{}',1),
                    ('m3','alice-space','b','个人群','私人课程提醒',None,'2026-01-03','{}',1)])
            sk=socket.socket();sk.bind(('127.0.0.1',0));port=sk.getsockname()[1];sk.close()
            env=dict(os.environ,QUNYI_ACCESS_FILE=str(p/'access'),QUNYI_CREDENTIAL_FILE=str(p/'creds'),QUNYI_ACCOUNT_DB=str(p/'accounts'),QUNYI_GATEWAY_PORT=str(port),TEST_MSG_DB=str(p/'messages'),TEST_STATE=str(p/'state'),PYTHONPATH=os.pathsep.join([str(root),str(root/'test_backend_fixture')]))
            env.pop('QUNYI_B_INTEGRATION',None)
            proc=subprocess.Popen([sys.executable,'-c',RUNNER],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def req(method,path,body=None,cookie=None,origin='https://test.local'):
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);headers={'Host':'test.local','Origin':origin,'Content-Type':'application/json'}
                if cookie:headers['Cookie']=cookie
                c.request(method,path,json.dumps(body) if body is not None else None,headers)
                r=c.getresponse();result=(r.status,r.getheader('Set-Cookie'),json.loads(r.read()));c.close();return result
            try:
                for _ in range(80):
                    try:req('GET','/auth/session');break
                    except OSError:time.sleep(.05)
                cookies={}
                for username,password in [('owner','owner-password-test'),('alice','alice-password-test'),('bobby','bobby-password-test')]:
                    status,cookie,_=req('POST','/auth/login',{'username':username,'password':password})
                    self.assertEqual(status,200);cookies[username]=cookie.split(';')[0]
                alice,owner,bob=cookies['alice'],cookies['owner'],cookies['bobby']
                self.assertEqual(req('GET','/group-access/messages',cookie=alice)[2]['items'],[])
                status,_,body=req('POST','/group-access/apply',{'group_key':'group-a','note':'测试'},alice)
                self.assertEqual(status,201);rid=body['request_id']
                self.assertEqual(req('GET','/group-access/admin/requests',cookie=owner)[2]['items'][0]['account_name'],'alice')
                self.assertEqual(req('POST','/group-access/admin/decision',{'request_id':rid,'approve':True},bob)[0],403)
                self.assertEqual(req('POST','/group-access/admin/decision',{'request_id':rid,'approve':True},owner)[0],200)
                self.assertEqual(req('GET','/group-access/admin/members',cookie=owner)[2]['items'][0]['account_name'],'alice')
                status,_,main=req('GET','/api/v1/messages?limit=100&offset=0&order=desc',cookie=alice)
                self.assertEqual(status,200);self.assertEqual({x['id'] for x in main['items']},{'m1','m3'});self.assertNotIn('不可见秘密',json.dumps(main,ensure_ascii=False))
                self.assertEqual(req('GET','/api/v1/messages/m1',cookie=alice)[0],200)
                self.assertEqual(req('GET','/api/v1/messages/m2',cookie=alice)[0],404)
                self.assertEqual(set(req('GET','/api/v1/coverage',cookie=alice)[2]['groups']),{'A','个人群'})
                status,_,main=req('POST','/api/v1/query',{'question':'志愿活动'},alice)
                self.assertEqual(status,200,main);self.assertEqual(main['citations'][0]['message_id'],'m1')
                status,_,body=req('GET','/group-access/messages',cookie=alice)
                self.assertEqual(status,200);self.assertEqual([x['id'] for x in body['items']],['m1'])
                self.assertEqual(req('GET','/group-access/groups',cookie=alice)[2]['items'][0]['display_name'],'A')
                self.assertEqual(req('GET','/group-access/messages?group=group-b',cookie=alice)[0],403)
                self.assertEqual(req('GET','/group-access/messages/m2',cookie=alice)[0],404)
                self.assertEqual(req('POST','/group-access/search',{'question':'志愿活动','group_key':'group-b'},alice)[0],403)
                status,_,body=req('POST','/group-access/search',{'question':'志愿活动','group_key':'group-a'},alice)
                self.assertEqual(status,200,body);self.assertEqual(body['citations'][0]['message_id'],'m1')
                self.assertNotIn('不可见秘密',json.dumps(body,ensure_ascii=False))
                self.assertEqual(req('POST','/group-access/admin/revoke',{'group_key':'group-a','account_id':'alice-space'},owner)[0],200)
                self.assertEqual(req('GET','/group-access/messages',cookie=alice)[2]['items'],[])
                self.assertEqual(req('GET','/group-access/messages/m1',cookie=alice)[0],404)
                self.assertEqual(req('POST','/group-access/search',{'question':'志愿活动'},alice)[2]['status'],'not_found')
            finally:
                proc.terminate();proc.wait(timeout=5);proc.stderr.close()

if __name__=='__main__':unittest.main()
