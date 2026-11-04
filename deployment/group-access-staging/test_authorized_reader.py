import os
import sqlite3
import tempfile
import unittest
from authorized_reader import AuthorizedReader
from group_access import GroupAccess


class ReaderTests(unittest.TestCase):
    def test_filters_before_return_and_after_revoke(self):
        with tempfile.TemporaryDirectory() as td:
            data=os.path.join(td,'messages.sqlite');perms=os.path.join(td,'grants.sqlite')
            with sqlite3.connect(data) as db:
                db.execute('CREATE TABLE messages(id TEXT, workspace TEXT, group_label TEXT, content TEXT, sent_at TEXT, imported_at TEXT)')
                db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',[
                    ('a','bot-space','group-a','可见内容',None,'2026-01-01'),
                    ('b','bot-space','group-b','不应泄露',None,'2026-01-02'),
                    ('c','other-space','group-a','其他工作区',None,'2026-01-03')])
            acl=GroupAccess(perms);acl.bootstrap('group-a','A群','admin');acl.bootstrap('group-b','B群','other-admin')
            req=acl.apply('group-a','alice');acl.decide(req,'admin',True)
            reader=AuthorizedReader(data,perms,'bot-space')
            self.assertEqual([m['id'] for m in reader.list_messages('alice')],['a'])
            self.assertEqual(reader.list_messages('alice','group-b'),[])
            self.assertIsNone(reader.message('alice','b'))
            self.assertIsNone(reader.message('bob','a'))
            acl.revoke('group-a','alice','admin')
            self.assertEqual(reader.list_messages('alice'),[])
            self.assertIsNone(reader.message('alice','a'))

if __name__ == '__main__': unittest.main()
