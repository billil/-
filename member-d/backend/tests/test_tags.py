import json, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
from qunyi.config import Config
from qunyi.service import Service
from qunyi.server import Server

A='synthetic-tags-token-space-a'; B='synthetic-tags-token-space-b'

class TagTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cfg=Config(Path(self.tmp.name),'mock',{A:'a',B:'b'});self.app=Service(self.cfg)
    def tearDown(self):self.app.store.close();self.tmp.cleanup()
    def call(self,m,p,b=None,token=A,q=None):return self.app.dispatch(m,p,b,'Bearer '+token,q)
    def message(self,token=A):
        return self.call('POST','/v1/imports',{'messages':[{'content':'测试通知','group_label':'测试群'}]},token)[1]['items'][0]['message_id']
    def test_label_lifecycle_and_persistence(self):
        mid=self.message();code,tag=self.call('POST','/v1/tags',{'name':' 重要 ','color':'#ff0033'});self.assertEqual(code,201)
        self.assertEqual((tag['name'],tag['color']),('重要','#FF0033'));tid=tag['id']
        self.assertEqual(self.call('POST','/v1/tags',{'name':'重要','color':'#123456'})[0],409)
        for _ in range(2):self.assertEqual(self.call('POST',f'/v1/messages/{mid}/tags',{'tag_id':tid})[0],200)
        self.assertEqual(self.call('GET',f'/v1/messages/{mid}/tags')[1]['count'],1)
        self.assertEqual(self.call('GET',f'/v1/tags/{tid}/messages')[1]['total'],1)
        self.assertEqual(self.call('PATCH',f'/v1/tags/{tid}',{'name':'作业','color':'#123abc'})[1]['color'],'#123ABC')
        self.app.store.close();self.app=Service(self.cfg)
        self.assertEqual(self.call('GET','/v1/tags')[1]['items'][0]['name'],'作业')
        self.assertEqual(self.call('GET',f'/v1/tags/{tid}/messages')[1]['items'][0]['id'],mid)
        self.assertEqual(self.call('DELETE',f'/v1/tags/{tid}')[0],200)
        self.assertEqual(self.call('GET',f'/v1/messages/{mid}')[0],200)
        self.assertEqual(self.call('GET',f'/v1/messages/{mid}/tags')[1]['count'],0)
    def test_isolation_validation_and_cascade(self):
        mid=self.message();tid=self.call('POST','/v1/tags',{'name':'A','color':'#00AA33'})[1]['id']
        other=self.message(B);self.assertEqual(self.call('POST',f'/v1/messages/{other}/tags',{'tag_id':tid},B)[0],404)
        self.assertEqual(self.call('GET',f'/v1/tags/{tid}/messages',token=B)[0],404)
        self.assertEqual(self.call('POST','/v1/tags',{'name':' A ','color':'red'})[0],400)
        self.assertEqual(self.call('POST','/v1/tags',{'name':'a','color':'#ffffff'})[0],409)
        self.call('POST',f'/v1/messages/{mid}/tags',{'tag_id':tid});self.call('DELETE',f'/v1/messages/{mid}')
        self.assertEqual(self.call('GET','/v1/tags')[1]['items'][0]['message_count'],0)
    def test_http_and_detach(self):
        srv=Server(('127.0.0.1',0),self.app);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            base='http://127.0.0.1:'+str(srv.server_port)
            def request(method,path,body=None):
                data=json.dumps(body).encode() if body is not None else None
                req=urllib.request.Request(base+path,data,{'Authorization':'Bearer '+A,'Content-Type':'application/json'},method=method)
                with urllib.request.urlopen(req) as resp:return resp.status,json.load(resp)
            mid=self.message();status,tag=request('POST','/v1/tags',{'name':'课程','color':'#0000ff'});self.assertEqual(status,201)
            request('POST',f'/v1/messages/{mid}/tags',{'tag_id':tag['id']})
            self.assertEqual(request('DELETE',f'/v1/messages/{mid}/tags/{tag["id"]}')[1]['removed'],True)
            self.assertEqual(request('GET',f'/v1/messages/{mid}/tags')[1]['items'],[])
        finally:srv.shutdown();srv.server_close();t.join()

if __name__=='__main__':unittest.main()
