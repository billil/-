import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qq_group_bot import (
    GROUP_AND_C2C_EVENT,
    AccessTokenManager,
    MessageStore,
    QQGroupBot,
    Settings,
)


def sample_message(message_id="msg-1", group="group-a", content="你好"):
    return {
        "id": message_id,
        "group_openid": group,
        "content": content,
        "message_type": 0,
        "timestamp": "2026-10-28T10:00:00+08:00",
        "author": {
            "member_openid": "member-1",
            "username": "测试成员",
            "member_role": "member",
        },
    }


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.store = MessageStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def make_bot(self, groups=frozenset({"group-a"})):
        settings = Settings(
            app_id="test-app",
            app_secret="test-secret",
            allowed_groups=groups,
            database_path=self.db_path,
            retention_days=30,
        )
        return QQGroupBot(settings, self.store)

    def dispatch(self, bot, event_type, data):
        asyncio.run(bot._dispatch({"op": 0, "s": 1, "t": event_type, "d": data}))

    def test_save_and_deduplicate_full_and_at_events(self):
        bot = self.make_bot()
        self.dispatch(bot, "GROUP_MESSAGE_CREATE", sample_message())
        self.dispatch(bot, "GROUP_AT_MESSAGE_CREATE", sample_message())
        self.assertEqual(self.store.count(), 1)
        row = self.store.connection.execute(
            "SELECT content, username FROM messages WHERE id = 'msg-1'"
        ).fetchone()
        self.assertEqual(row, ("你好", "测试成员"))

    def test_unauthorized_group_is_not_saved(self):
        bot = self.make_bot()
        self.dispatch(bot, "GROUP_MESSAGE_CREATE", sample_message(group="other-group"))
        self.assertEqual(self.store.count(), 0)

    def test_empty_whitelist_is_discovery_mode(self):
        bot = self.make_bot(groups=frozenset())
        self.dispatch(bot, "GROUP_MESSAGE_CREATE", sample_message())
        self.assertEqual(self.store.count(), 0)

    def test_explicit_wildcard_allows_all_groups(self):
        bot = self.make_bot(groups=frozenset({"*"}))
        self.dispatch(bot, "GROUP_MESSAGE_CREATE", sample_message(group="other-group"))
        self.assertEqual(self.store.count(), 1)

    def test_ready_event_stores_session_id(self):
        bot = self.make_bot()
        self.dispatch(bot, "READY", {"session_id": "session-1", "user": {"username": "测试"}})
        self.assertEqual(bot.session_id, "session-1")

    def test_identify_uses_group_intent_and_single_shard(self):
        bot = self.make_bot()

        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def send(self, value):
                self.sent.append(json.loads(value))

        websocket = FakeWebSocket()
        asyncio.run(bot._identify_or_resume(websocket, "access-token"))
        payload = websocket.sent[0]
        self.assertEqual(payload["op"], 2)
        self.assertEqual(payload["d"]["token"], "QQBot access-token")
        self.assertEqual(payload["d"]["intents"], GROUP_AND_C2C_EVENT)
        self.assertEqual(payload["d"]["shard"], [0, 1])

    def test_access_token_is_cached(self):
        manager = AccessTokenManager("app", "secret")
        with patch(
            "qq_group_bot.http_json",
            return_value={"access_token": "token-1", "expires_in": "7200"},
        ) as request:
            first = asyncio.run(manager.get())
            second = asyncio.run(manager.get())
        self.assertEqual(first, "token-1")
        self.assertEqual(second, "token-1")
        self.assertEqual(request.call_count, 1)

    def test_retention_cleanup(self):
        bot = self.make_bot()
        self.dispatch(bot, "GROUP_MESSAGE_CREATE", sample_message())
        self.store.connection.execute(
            "UPDATE messages SET received_at = '2000-01-01T00:00:00+00:00'"
        )
        self.store.connection.commit()
        self.assertEqual(self.store.cleanup(30), 1)
        self.assertEqual(self.store.count(), 0)


if __name__ == "__main__":
    unittest.main()
