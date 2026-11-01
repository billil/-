import os
import tempfile
import unittest
import json
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

from qunyi.config import Config
from qunyi.service import Service


class BIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Service(Config(state=Path(self.tmp.name), tokens={'a'*20: 'one', 'b'*20: 'two'}))
        self.a, self.b = 'Bearer '+'a'*20, 'Bearer '+'b'*20
        for space in ('one', 'two'):
            self.s._ensure_group(space, 'group', 'group', 'test')
            self.s.imports(space, {'messages': [{'content': '志愿活动图书馆集合 ' + space, 'group_label': 'group'}]})
        self.s.store.commit()

    def tearDown(self):
        self.s.store.close()
        self.tmp.cleanup()

    @patch.dict(os.environ, {'QUNYI_B_INTEGRATION': '1'})
    def test_real_facade_with_fake_model_and_workspace_isolation(self):
        import app.agent as agent
        seen = []
        def parse(q, today):
            return {'keywords': ['志愿活动'], 'filters': {'explicit': [], 'vague': []}, 'time': {'type': 'none'}}
        def answer(q, candidates, assumptions, clusters):
            seen.extend(candidates)
            return {'status': 'ok', 'answer': '图书馆集合', 'evidence': [{'msg_id': candidates[0]['msg_id'], 'quote': '图书馆集合'}]}
        with patch.object(agent, 'parse_query', parse), patch.object(agent, 'generate_answer', answer):
            code, result = self.s.dispatch('POST', '/v1/ai/query', {'question': '志愿活动'}, self.a)
        self.assertEqual(code, 200, result)
        self.assertEqual(result['status'], 'answer')
        self.assertTrue(seen)
        self.assertTrue(all(c['content'].endswith('one') for c in seen))
        self.assertIsNotNone(self.s.store.one('SELECT * FROM b_sessions WHERE workspace=? AND id=?', ('one', result['session_id'])))
        code, _ = self.s.dispatch('POST', '/v1/ai/query', {'question': '志愿活动', 'session_id': result['session_id']}, self.b)
        self.assertEqual(code, 404)

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            code, result = self.s.dispatch('POST', '/v1/ai/query', {'question': '志愿活动'}, self.a)
        self.assertEqual(code, 503)
        self.assertEqual(result['error']['code'], 'B_INTEGRATION_DISABLED')

    @patch.dict(os.environ, {'QUNYI_B_INTEGRATION': '1'})
    def test_rejects_unverified_evidence(self):
        import app.agent as agent
        with patch.object(agent, 'parse_query', return_value={'keywords':['志愿活动'], 'filters':{}, 'time':{'type':'none'}}), \
             patch.object(agent, 'generate_answer', return_value={'status':'ok', 'answer':'x', 'evidence':[{'msg_id':'foreign', 'quote':'x'}]}):
            code, result = self.s.dispatch('POST', '/v1/ai/query', {'question': '志愿活动'}, self.a)
        self.assertEqual(code, 502)
        self.assertEqual(result['error']['code'], 'B_INVALID_EVIDENCE')

    @patch.dict(os.environ, {'QUNYI_B_INTEGRATION': '1'})
    def test_http_with_actual_facade_and_fake_model(self):
        import app.agent as agent
        from qunyi.server import Server
        server = Server(('127.0.0.1', 0), self.s)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        answer = {'status': 'ok', 'answer': '图书馆集合'}
        def fake_answer(q, candidates, assumptions, clusters):
            return {**answer, 'evidence': [{'msg_id': candidates[0]['msg_id'], 'quote': '图书馆集合'}]}
        request = urllib.request.Request(
            'http://127.0.0.1:%d/v1/ai/query' % server.server_port,
            data=json.dumps({'question':'志愿活动'}).encode(),
            headers={'Authorization': self.a, 'Content-Type':'application/json'}, method='POST')
        try:
            with patch.object(agent, 'parse_query', return_value={'keywords':['志愿活动'], 'filters':{}, 'time':{'type':'none'}}), \
                 patch.object(agent, 'generate_answer', fake_answer), urllib.request.urlopen(request) as response:
                data = json.load(response)
            self.assertEqual(data['status'], 'answer')
            self.assertTrue(data['evidence'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
