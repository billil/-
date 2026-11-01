"""Experimental B facade integration, scoped to D's workspace database."""
import json
import os
from dataclasses import asdict
from datetime import datetime

from .store import dump, uid


class IntegrationError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class SessionStore:
    def __init__(self, db, workspace):
        self.db, self.workspace = db, workspace
        self.db.execute('''CREATE TABLE IF NOT EXISTS b_sessions (
          workspace TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(workspace,id))''')

    def get(self, session_id):
        row = self.db.execute('SELECT data FROM b_sessions WHERE workspace=? AND id=?',
                              (self.workspace, session_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, session):
        data = asdict(session)
        if isinstance(data.get('updated_at'), datetime):
            data['updated_at'] = data['updated_at'].isoformat()
        self.db.execute('INSERT OR REPLACE INTO b_sessions VALUES(?,?,?)',
                        (self.workspace, session.session_id, dump(data)))

    def touch(self, session_id):
        return None


def query(service, workspace, body, facade=None):
    if os.getenv('QUNYI_B_INTEGRATION') != '1':
        raise IntegrationError(503, 'B_INTEGRATION_DISABLED', 'B AI 集成尚未启用')
    question = body.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise IntegrationError(400, 'INVALID_INPUT', 'question 必须为非空文本')
    if set(body) - {'question', 'session_id'}:
        raise IntegrationError(400, 'INVALID_INPUT', '包含未知查询字段')
    sid = body.get('session_id')
    if sid is not None and (not isinstance(sid, str) or not sid or len(sid) > 200):
        raise IntegrationError(400, 'INVALID_INPUT', 'session_id 不正确')
    if facade is None:
        try:
            from app import facade
        except ImportError as exc:
            raise IntegrationError(503, 'B_DEPENDENCY_MISSING', '无法加载 B AI 模块或依赖') from exc
    sessions = SessionStore(service.store.db, workspace)
    existing = sessions.get(sid) if sid else None
    if sid and existing is None:
        raise IntegrationError(404, 'SESSION_NOT_FOUND', '会话不存在或不可访问')
    sid = sid or uid('bsess')
    available = {}

    def provider(filters):
        # Unsupported hard filters must not be silently dropped.
        if filters.get('category') or filters.get('file_hint') or (filters.get('time_range') and filters.get('time_field') != 'send_time'):
            raise IntegrationError(422, 'FILTER_UNSUPPORTED', '当前后端不能可靠处理该筛选条件')
        cond = {}
        if filters.get('group_id'):
            cond['group_label'] = filters['group_id']
        if filters.get('time_range'):
            from .service import timestamp
            t = filters['time_range']
            cond['sent_after'] = timestamp(t.get('start'))
            cond['sent_before'] = timestamp(t.get('end'), end=True)
        limit = max(1, min(50, int(filters.get('_limit', 20))))
        rows = service.search.search(service.store, workspace, question, cond, limit=limit)
        allowed = {r['group_key'] for r in service.store.rows(
            'SELECT group_key FROM groups WHERE workspace=? AND authorized=1', (workspace,))}
        result = []
        for row in rows:
            if row['group_label'] not in allowed:
                continue
            available[row['id']] = row['content']
            result.append({'msg_id': row['id'], 'content': row['content'],
                           'group_id': row['group_label'], 'send_time': row['sent_at'],
                           'sender': None, 'score': row['score'], 'sources': ['d_local']})
        return result

    try:
        result = facade.process(question.strip(), existing or {'session_id': sid}, provider, sessions, db_path=None)
    except IntegrationError:
        raise
    except RuntimeError as exc:
        raise IntegrationError(503, 'B_MODEL_UNAVAILABLE', 'B 模型服务不可用或未配置') from exc
    except Exception as exc:
        raise IntegrationError(502, 'B_PROCESS_FAILED', 'B AI 处理失败') from exc
    if not isinstance(result, dict):
        raise IntegrationError(502, 'B_INVALID_RESULT', 'B AI 返回格式不正确')
    evidence = result.get('evidence') or []
    if not isinstance(evidence, list):
        raise IntegrationError(502, 'B_INVALID_EVIDENCE', 'AI 引用格式不正确')
    for item in evidence:
        if not isinstance(item, dict) or item.get('msg_id') not in available or not isinstance(item.get('quote'), str) or not item['quote'] or item['quote'] not in available[item['msg_id']]:
            raise IntegrationError(502, 'B_INVALID_EVIDENCE', 'AI 引用无法在候选原文中核验')
    if result.get('status') == 'answer' and not evidence:
        raise IntegrationError(502, 'B_INVALID_EVIDENCE', 'AI 答案缺少可核验引用')
    result['session_id'] = sid
    result['integration'] = 'b_facade_d_workspace_experimental'
    return result
