import os
import sqlite3
import tempfile
import unittest
from group_access import GroupAccess

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.access = GroupAccess(os.path.join(self.temp.name, 'access.db'))
        self.access.bootstrap('g1','测试群','admin1')
        self.access.bootstrap('g2','第二个群','admin2')
    def tearDown(self):
        self.temp.cleanup()
    def test_approval_is_group_scoped_and_revocable(self):
        rid = self.access.apply('g1','alice','我在群中')
        self.assertEqual(self.access.allowed_groups('alice'),set())
        with self.assertRaises(PermissionError): self.access.decide(rid,'admin2',True)
        self.access.decide(rid,'admin1',True)
        self.assertEqual(self.access.allowed_groups('alice'),{'g1'})
        self.assertEqual(self.access.allowed_groups('bob'),set())
        with self.assertRaises(PermissionError): self.access.revoke('g1','alice','admin2')
        self.access.revoke('g1','alice','admin1')
        self.assertEqual(self.access.allowed_groups('alice'),set())
    def test_rejected_duplicate_and_unknown(self):
        rid=self.access.apply('g1','alice')
        with self.assertRaises(sqlite3.IntegrityError): self.access.apply('g1','alice')
        self.access.decide(rid,'admin1',False)
        self.assertEqual(self.access.allowed_groups('alice'),set())
        with self.assertRaises(PermissionError): self.access.decide(rid,'admin1',True)
        self.access.apply('g1','alice')
        with self.assertRaises(ValueError): self.access.apply('unknown','alice')

if __name__ == '__main__': unittest.main()
