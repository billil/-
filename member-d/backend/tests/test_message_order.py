"""Verify newest-first pagination while preserving the original default."""
import tempfile
import unittest
from pathlib import Path
from qunyi.config import Config
from qunyi.service import Service

class MessageOrderTests(unittest.TestCase):
    def test_descending_and_default_order(self):
        with tempfile.TemporaryDirectory() as directory:
            token = 'test-only-token-' + 'x' * 20
            app = Service(Config(state=Path(directory), tokens={token: 'space'}))
            try:
                auth = 'Bearer ' + token
                for i in range(3):
                    app.dispatch('POST', '/v1/imports', {'messages': [
                        {'content': f'test message {i}', 'group_label': 'test group'}]}, auth)
                    app.store.db.execute('UPDATE messages SET imported_at=? WHERE content=?',
                                         (f'2026-01-0{i+1}', f'test message {i}'))
                for params, expected in [
                    ({'limit':'2'}, ['test message 0', 'test message 1']),
                    ({'limit':'2', 'order':'desc'}, ['test message 2', 'test message 1']),
                    ({'limit':'2', 'order':'desc', 'offset':'2'}, ['test message 0'])]:
                    status, result = app.dispatch('GET', '/v1/messages', {}, auth, params)
                    self.assertEqual(status, 200)
                    self.assertEqual([item['content'] for item in result['items']], expected)
                status, _ = app.dispatch('GET', '/v1/messages', {}, auth, {'order':'invalid'})
                self.assertEqual(status, 400)
            finally:
                app.store.close()
