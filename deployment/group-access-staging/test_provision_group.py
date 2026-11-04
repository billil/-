import os
import sqlite3
import tempfile
import unittest
from group_access import GroupAccess
from provision_group import provision


class ProvisionTests(unittest.TestCase):
    def test_verified_mapping_alias_and_no_manual_source(self):
        with tempfile.TemporaryDirectory() as t:
            a=os.path.join(t,'account');m=os.path.join(t,'messages')
            with sqlite3.connect(a) as db:
                db.execute('CREATE TABLE accounts(username TEXT,workspace TEXT,active INTEGER)')
                db.execute("INSERT INTO accounts VALUES('owner','space-owner',1)")
            with sqlite3.connect(m) as db:
                db.execute('CREATE TABLE groups(workspace TEXT,group_key TEXT,source TEXT,authorized INTEGER)')
                db.executemany('INSERT INTO groups VALUES(?,?,?,?)',[('bot','opaque1','qq_websocket',1),('bot','fake','manual',1)])
            with self.assertRaises(ValueError):provision(a,m,'bot','fake','123456','群','owner')
            provision(a,m,'bot','opaque1','123456','测试群','owner')
            acl=GroupAccess(a);rid=acl.apply('123456','student');acl.decide(rid,'space-owner',True)
            self.assertEqual(acl.allowed_groups('student'),{'opaque1'})
            with self.assertRaises(ValueError):provision(a,m,'bot','opaque1','654321','群','owner')

if __name__=='__main__':unittest.main()
