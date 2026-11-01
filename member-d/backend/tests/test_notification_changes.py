import tempfile
import unittest
from pathlib import Path

from qunyi.config import Config
from qunyi.service import Service

TOKEN = 'synthetic-notification-test-token'

class NotificationChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.tmp.name), 'mock', {TOKEN: 'space'})
        self.app = Service(self.config)

    def tearDown(self):
        self.app.store.close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, params=None):
        return self.app.dispatch(method, path, body, 'Bearer ' + TOKEN, params)

    def test_pages_updates_deletes_and_restart(self):
        _, result = self.call('POST', '/v1/imports', {'messages': [
            {'content':'通知一', 'group_label':'测试群'},
            {'content':'通知二', 'group_label':'测试群'}]})
        ids = [item['message_id'] for item in result['items']]
        self.assertEqual(self.call('POST', '/v1/subscriptions', {'group_keys':['测试群']})[0], 200)
        # Simulate identical import timestamps, including across the page boundary.
        self.app.store.db.execute("UPDATE messages SET imported_at='2026-01-01T00:00:00Z'")
        self.app.store.commit()
        cursor = ''; seen = []
        while True:
            status, page = self.call('GET', '/v1/notifications', params={'limit':'1','after':cursor})
            self.assertEqual(status, 200)
            seen.extend(page['items']); cursor = page['next_cursor']
            if not page['has_more']: break
        self.assertEqual({x['id'] for x in seen}, set(ids))
        self.assertEqual(self.call('PATCH', '/v1/messages/'+ids[0], {'content':'地点已变更'})[0], 200)
        self.assertEqual(self.call('DELETE', '/v1/messages/'+ids[1])[0], 200)
        self.app.store.close(); self.app = Service(self.config)
        _, page = self.call('GET', '/v1/notifications', params={'after':cursor})
        self.assertEqual([x['change_type'] for x in page['items']], ['updated','deleted'])
        self.assertEqual(page['items'][0]['content'], '地点已变更')
        self.assertNotIn('content', page['items'][1])
        self.assertEqual(self.call('GET','/v1/subscriptions')[1]['count'],1)

    def test_revoked_group_and_legacy_cursor(self):
        self.call('POST','/v1/imports',{'messages':[{'content':'隐私','group_label':'测试群'}]})
        self.call('POST','/v1/subscriptions',{'group_keys':['测试群']})
        self.app.store.db.execute("UPDATE groups SET authorized=0 WHERE group_key='测试群'")
        self.app.store.commit()
        self.assertEqual(self.call('GET','/v1/notifications')[1]['items'],[])
        # A later import must not silently restore revoked access.
        self.call('POST','/v1/imports',{'messages':[{'content':'后续通知','group_label':'测试群'}]})
        self.assertEqual(self.call('GET','/v1/notifications')[1]['items'],[])
        self.assertEqual(self.call('GET','/v1/notifications',params={'after':'2026-01-01T00:00:00Z'})[0],400)
        self.assertIsNone(self.call('GET','/v1/sync/status')[1]['connected'])

if __name__ == '__main__': unittest.main()
