import os
import tempfile
import unittest
from group_access import GroupAccess
from group_access_http import handle


class FakeHandler:
    def __init__(self, account=None, data=None, origin=True, rate=True):
        self.account, self.data, self.origin, self.rate_ok = account, data, origin, rate
        self.response = None;self.path='/'
    def session_user(self): return ('name', self.account) if self.account else None
    def body(self): return self.data
    def same_origin(self): return self.origin
    def rate(self): return self.rate_ok
    def reply(self, status, payload): self.response = (status, payload)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=GroupAccess(os.path.join(self.tmp.name,'db'))
        self.store.bootstrap('group1','测试群','admin-workspace')
    def tearDown(self): self.tmp.cleanup()
    def call(self, method, path, account=None, data=None, origin=True, rate=True):
        h=FakeHandler(account,data,origin,rate)
        self.assertTrue(handle(h,self.store,method,path))
        return h.response
    def test_workflow_without_data_access(self):
        path='/group-access/apply'
        self.assertEqual(self.call('POST',path,data={'group_key':'group1'})[0],401)
        self.assertEqual(self.call('POST',path,'alice',{'group_key':'group1'},False)[0],403)
        self.assertEqual(self.call('POST',path,'alice',{'group_key':'group1','account_id':'victim'})[0],400)
        status,body=self.call('POST',path,'alice',{'group_key':'group1','note':'学生'})
        self.assertEqual(status,201);self.assertFalse(body['data_access_enabled'])
        rid=body['request_id']
        self.assertEqual(len(self.call('GET','/group-access/admin/requests','other')[1]['items']),0)
        self.assertEqual(len(self.call('GET','/group-access/admin/requests','admin-workspace')[1]['items']),1)
        self.assertEqual(self.call('POST','/group-access/admin/decision','other',{'request_id':rid,'approve':True})[0],403)
        status,body=self.call('POST','/group-access/admin/decision','admin-workspace',{'request_id':rid,'approve':True})
        self.assertEqual(status,200);self.assertFalse(body['data_access_enabled'])
        self.assertEqual(self.store.allowed_groups('alice'),{'group1'})
        self.assertEqual(self.call('GET','/group-access/my-requests','alice')[1]['items'][0]['status'],'approved')
        self.assertEqual(self.call('POST','/group-access/admin/revoke','admin-workspace',{'group_key':'group1','account_id':'alice'})[0],200)
        self.assertEqual(self.store.allowed_groups('alice'),set())
    def test_reject_bad_requests_and_rate(self):
        self.assertEqual(self.call('POST','/group-access/apply','a',{'group_key':'group1'},rate=False)[0],429)
        self.assertEqual(self.call('POST','/group-access/admin/decision','a',{'request_id':True,'approve':True})[0],400)
        self.assertEqual(self.call('GET','/group-access/missing','a')[0],404)

if __name__ == '__main__': unittest.main()
