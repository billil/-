import tempfile
import unittest
from pathlib import Path

from qunyi.config import Config
from qunyi.service import Service


class NotificationCursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service(Config(state=Path(self.tmp.name), tokens={'a' * 20: 'demo'},
                                      qq_workspace='demo', qq_ingest_token='secret'))
        self.s = self.service
        self.auth = 'Bearer ' + 'a' * 20
        for i in range(3):
            status, _ = self.s.dispatch('POST', '/v1/internal/qq/messages',
                {'group_key': 'group', 'event_id': str(i), 'content': '通知'},
                params={'_ingest_token': 'secret'})
            self.assertEqual(status, 200)
        self.s.store.db.execute("UPDATE messages SET imported_at='2026-11-01T00:00:00+00:00'")
        self.s.store.db.commit()
        status, _ = self.s.dispatch('POST', '/v1/subscriptions',
                                     {'group_keys': ['group']}, self.auth)
        self.assertEqual(status, 200)

    def tearDown(self):
        self.s.store.db.close()
        self.tmp.cleanup()

    def test_equal_timestamps_pagination_has_no_loss(self):
        cursor = ''
        ids = []
        for _ in range(3):
            status, data = self.s.dispatch('GET', '/v1/notifications', authorization=self.auth,
                                           params={'limit': '1', 'after': cursor})
            self.assertEqual(status, 200)
            ids += [item['id'] for item in data['items']]
            cursor = data['next_cursor']
        self.assertEqual(len(set(ids)), 3)
        self.assertFalse(data['has_more'])

    def test_revocation_is_not_undone_by_new_message(self):
        self.s.store.db.execute("UPDATE groups SET authorized=0 WHERE group_key='group'")
        self.s.store.db.commit()
        self.s.dispatch('POST', '/v1/internal/qq/messages',
                        {'group_key': 'group', 'event_id': 'later', 'content': '新通知'},
                        params={'_ingest_token': 'secret'})
        _, data = self.s.dispatch('GET', '/v1/notifications', authorization=self.auth)
        self.assertEqual(data['items'], [])
        status, _ = self.s.dispatch('POST', '/v1/notifications/ack',
                                    {'group_key': 'group', 'cursor': ''}, self.auth)
        self.assertEqual(status, 403)

    def test_invalid_cursor_and_unverified_connection(self):
        status, _ = self.s.dispatch('GET', '/v1/notifications', authorization=self.auth,
                                    params={'after': 'v1.invalid'})
        self.assertEqual(status, 400)
        _, data = self.s.dispatch('GET', '/v1/sync/status', authorization=self.auth)
        self.assertTrue(data['configured'])
        self.assertIsNone(data['connected'])


if __name__ == '__main__':
    unittest.main()
