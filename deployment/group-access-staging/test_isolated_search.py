"""Existing backend + fake provider; no real model calls or real messages."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent/'test_backend_fixture'))
from qunyi.config import Config
from qunyi.service import Service
from authorized_reader import AuthorizedReader
from group_access import GroupAccess
from isolated_search import search_authorized, AuthorizationChanged


class Provider:
    def __init__(self, message_id='m1', revoke=None):
        self.calls=[];self.message_id=message_id;self.revoke=revoke
    def complete(self, context, timeout):
        self.calls.append(context)
        if self.revoke:self.revoke()
        return {'action':'finish','status':'answer','fields':[{'name':'原文摘录','value':'图书馆集合','message_id':self.message_id,'quote':'志愿活动图书馆集合'}]}


class IsolatedSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();p=Path(self.tmp.name)
        self.db=p/'messages.db';self.acldb=p/'acl.db'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,workspace TEXT,group_label TEXT,content TEXT,sent_at TEXT,imported_at TEXT,metadata TEXT,revision INTEGER)')
            db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',[
                ('m1','bot','group-a','志愿活动图书馆集合','2026-01-01T09:00:00+00:00','2026-01-01','{}',1),
                ('m2','bot','group-b','志愿活动秘密内容',None,'2026-01-02','{}',1),
                ('m3','other','group-a','志愿活动其他空间秘密',None,'2026-01-03','{}',1)])
        self.acl=GroupAccess(self.acldb);self.acl.bootstrap('group-a','A','admin');self.acl.bootstrap('group-b','B','admin2')
        request=self.acl.apply('group-a','alice');self.acl.decide(request,'admin',True)
        self.reader=AuthorizedReader(self.db,self.acldb,'bot')
        self.cfg=Config(state=p/'unused',mode='live',tokens={'x'*20:'demo'})
        self.old=os.environ.pop('QUNYI_B_INTEGRATION',None)
    def tearDown(self):
        if self.old is not None:os.environ['QUNYI_B_INTEGRATION']=self.old
        self.tmp.cleanup()
    def search(self,provider):
        return search_authorized(self.reader,self.acl,'alice',self.cfg,provider,{'question':'志愿活动集合'},Service)
    def test_model_sees_only_authorized_and_answer_has_source(self):
        provider=Provider();status,result=self.search(provider)
        self.assertEqual(status,200,result);self.assertEqual(result['status'],'answer')
        self.assertEqual(result['citations'][0]['message_id'],'m1')
        self.assertNotIn('session_id',result)
        context=json.dumps(provider.calls,ensure_ascii=False)
        self.assertIn('图书馆集合',context);self.assertNotIn('秘密内容',context);self.assertNotIn('其他空间秘密',context)
    def test_model_cannot_cite_another_group(self):
        provider=Provider('m2');status,result=self.search(provider)
        self.assertEqual(status,502,result)
        self.assertEqual(result['error']['code'],'MODEL_TOOL_DENIED')
    def test_uncertain_model_keeps_authorized_original_as_candidate(self):
        class Uncertain:
            def complete(self,context,timeout):return {'action':'finish','status':'uncertain','fields':[]}
        status,result=self.search(Uncertain())
        self.assertEqual(status,200);self.assertEqual(result['status'],'uncertain')
        self.assertEqual(result['candidate_source'],'authorized_search_not_ai_answer')
        self.assertEqual([c['message_id'] for c in result['citations']],['m1'])
    def test_candidate_metadata_is_derived_from_authorized_records(self):
        class OptionService(Service):
            def query(self,*args):return {'status':'clarify','options':[{'event_key':'m1','title':'活动','sent_at':'2099-01-01','group_label':'伪造群'}]}
        status,result=search_authorized(self.reader,self.acl,'alice',self.cfg,Provider(),{'question':'志愿活动'},OptionService)
        self.assertEqual(status,200)
        self.assertEqual(result['options'][0]['sent_at'],'2026-01-01T09:00:00+00:00')
        self.assertEqual(result['options'][0]['group_label'],'group-a')
    def test_selected_group_is_only_context_even_if_both_are_authorized(self):
        rid=self.acl.apply('group-b','alice');self.acl.decide(rid,'admin2',True)
        provider=Provider()
        status,result=search_authorized(self.reader,self.acl,'alice',self.cfg,provider,{'question':'志愿活动集合'},Service,group_key='group-a')
        self.assertEqual(status,200,result)
        self.assertNotIn('秘密内容',json.dumps(provider.calls,ensure_ascii=False))
        with self.assertRaises(PermissionError):
            search_authorized(self.reader,self.acl,'alice',self.cfg,provider,{'question':'x'},Service,group_key='unknown')
    def test_revocation_while_model_runs_blocks_response(self):
        provider=Provider(revoke=lambda:self.acl.revoke('group-a','alice','admin'))
        with self.assertRaises(AuthorizationChanged):self.search(provider)
        self.assertEqual(self.reader.list_messages('alice'),[])
    def test_session_rejected(self):
        with self.assertRaises(ValueError):
            search_authorized(self.reader,self.acl,'alice',self.cfg,Provider(),{'question':'x','session_id':'old'},Service)

if __name__=='__main__':unittest.main()
