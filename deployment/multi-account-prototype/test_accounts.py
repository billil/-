import os
import sqlite3
import tempfile
import unittest
from account_store import AccountStore

class Accounts(unittest.TestCase):
    def test_login_expiry_logout_and_disable(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'accounts.sqlite3')
            store = AccountStore(path)
            store.create_account('owner1', 'very-long-test-password', 'approved-space')
            self.assertIsNone(store.login('owner1', 'incorrect'))
            self.assertIsNone(store.login('unknown', 'incorrect'))
            token, _ = store.login('owner1', 'very-long-test-password', True)
            self.assertEqual(store.lookup(token), ('owner1', 'approved-space'))
            with sqlite3.connect(path) as db:
                self.assertNotIn(token, str(db.execute('SELECT * FROM sessions').fetchall()))
                self.assertNotIn('very-long-test-password', str(db.execute('SELECT * FROM accounts').fetchall()))
                db.execute('UPDATE sessions SET expires=0')
            self.assertIsNone(store.lookup(token))
            token, _ = store.login('owner1', 'very-long-test-password')
            store.logout(token)
            self.assertIsNone(store.lookup(token))
            token, _ = store.login('owner1', 'very-long-test-password')
            with sqlite3.connect(path) as db:
                db.execute('UPDATE accounts SET active=0')
            self.assertIsNone(store.lookup(token))

if __name__ == '__main__': unittest.main()
