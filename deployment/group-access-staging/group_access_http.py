"""Gateway endpoints; shared reads/search are disabled unless explicitly injected."""
import sqlite3
from isolated_search import AuthorizationChanged


def handle(handler, store, method, path, reader=None, query=None):
    if not path.startswith('/group-access/'):
        return False
    user = handler.session_user()
    if not user:
        handler.reply(401, {'error': '请先登录'})
        return True
    account = user[1]  # Server-side workspace identity, never a request parameter.
    data_enabled = reader is not None
    if method not in ('GET', 'POST'):
        handler.reply(405, {'error': '请求方式不支持'})
        return True
    if method == 'POST' and not handler.same_origin():
        handler.reply(403, {'error': '请求来源不匹配'})
        return True
    if method == 'POST' and not handler.rate():
        handler.reply(429, {'error': '请求过于频繁'})
        return True
    try:
        if method == 'GET' and path == '/group-access/messages':
            if reader is None:
                handler.reply(503, {'error':'群消息功能尚未启用'})
            else:
                handler.reply(200, {'items':reader.list_messages(account), 'data_access_enabled':True})
        elif method == 'GET' and path.startswith('/group-access/messages/'):
            if reader is None:
                handler.reply(503, {'error':'群消息功能尚未启用'})
            else:
                item=reader.message(account,path[len('/group-access/messages/'):])
                handler.reply(200, {'item':item}) if item else handler.reply(404, {'error':'消息不存在'})
        elif method == 'POST' and path == '/group-access/search':
            if query is None:
                handler.reply(503, {'error':'群消息查找尚未启用'})
            elif not handler.rate_query(user[0]):
                handler.reply(429, {'error':'查询过于频繁'})
            else:
                data=handler.body()
                if not isinstance(data,dict) or set(data)!={'question'}:
                    raise ValueError('invalid search')
                status,result=query(account,data)
                handler.reply(status,result)
        elif method == 'GET' and path == '/group-access/my-requests':
            handler.reply(200, {'items': store.my_requests(account), 'data_access_enabled': data_enabled})
        elif method == 'GET' and path == '/group-access/admin/requests':
            handler.reply(200, {'items': store.admin_requests(account), 'data_access_enabled': data_enabled})
        elif method == 'GET' and path == '/group-access/admin/members':
            handler.reply(200, {'items': store.admin_members(account), 'data_access_enabled': data_enabled})
        elif method == 'POST' and path == '/group-access/apply':
            data = handler.body()
            if not isinstance(data, dict) or set(data) - {'group_key', 'note'}:
                raise ValueError('invalid input')
            request_id = store.apply(data.get('group_key'), account, data.get('note', ''))
            handler.reply(201, {'request_id': request_id, 'status': 'pending', 'data_access_enabled': data_enabled})
        elif method == 'POST' and path == '/group-access/admin/decision':
            data = handler.body()
            if not isinstance(data, dict) or set(data) != {'request_id', 'approve'} or type(data['request_id']) is not int or type(data['approve']) is not bool:
                raise ValueError('invalid input')
            store.decide(data['request_id'], account, data['approve'])
            handler.reply(200, {'ok': True, 'data_access_enabled': data_enabled})
        elif method == 'POST' and path == '/group-access/admin/revoke':
            data = handler.body()
            if not isinstance(data, dict) or set(data) != {'group_key', 'account_id'}:
                raise ValueError('invalid input')
            store.revoke(data['group_key'], data['account_id'], account)
            handler.reply(200, {'ok': True, 'data_access_enabled': data_enabled})
        else:
            handler.reply(404, {'error': '接口不存在'})
    except (ValueError, TypeError, sqlite3.IntegrityError):
        handler.reply(400, {'error': '申请信息无效或已提交'})
    except PermissionError:
        handler.reply(403, {'error': '无权处理该申请'})
    except AuthorizationChanged:
        handler.reply(409, {'error': '群权限已变化，请重新查找'})
    except sqlite3.Error:
        handler.reply(503, {'error': '群消息服务暂不可用'})
    return True
