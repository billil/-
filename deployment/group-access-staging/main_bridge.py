"""Bridge main UI endpoints to combined, access-filtered account/group data."""
import sqlite3
import urllib.parse
from isolated_search import AuthorizationChanged


def handle(handler, access, reader, query, method, path):
    routes=('/api/v1/messages','/api/v1/coverage','/api/v1/query')
    if not (path in routes or path.startswith('/api/v1/messages/')):
        return False
    user=handler.session_user()
    if not user or not access.allowed_groups(user[1]):
        return False  # Existing account handling remains responsible.
    account=user[1]
    try:
        if method=='GET' and path=='/api/v1/messages':
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query,keep_blank_values=True)
            if set(params)-{'limit','offset','order'} or any(len(x)!=1 for x in params.values()):raise ValueError('invalid parameters')
            limit=int(params.get('limit',['20'])[0]);offset=int(params.get('offset',['0'])[0]);order=params.get('order',['asc'])[0]
            items,total=reader.combined(account,limit,offset,order=order)
            handler.reply(200,{'items':items,'limit':limit,'offset':offset,'total':total})
        elif method=='GET' and path.startswith('/api/v1/messages/'):
            items,_=reader.combined(account,1,0,path.rsplit('/',1)[-1])
            handler.reply(200,items[0]) if items else handler.reply(404,{'error':{'message':'消息不存在'}})
        elif method=='GET' and path=='/api/v1/coverage':
            handler.reply(200,reader.combined_coverage(account))
        elif method=='POST' and path=='/api/v1/query':
            if not handler.same_origin():handler.reply(403,{'error':{'message':'请求来源不匹配'}});return True
            if not handler.rate_query(user[0]):handler.reply(429,{'error':{'message':'查询过于频繁'}});return True
            body=handler.body()
            if not isinstance(body,dict) or set(body)-{'question','session_id','condition_updates'} or not isinstance(body.get('question'),str):raise ValueError('invalid query')
            updates=body.get('condition_updates',{})
            if not isinstance(updates,dict) or set(updates)-{'event_key'}:raise ValueError('invalid filter')
            request={'question':body['question']}
            if updates:request['condition_updates']=updates
            status,result=query(account,request)
            result['coverage']=reader.combined_coverage(account)
            names={x['group_key']:x['display_name'] for x in access.my_groups(account)}
            for item in result.get('citations',[]):
                if item.get('group_label') in names:item['group_label']=names[item['group_label']]
            for item in result.get('options',[]):
                if isinstance(item,dict) and item.get('group_label') in names:item['group_label']=names[item['group_label']]
            handler.reply(status,result)
        else:
            handler.reply(405,{'error':{'message':'请求方式不支持'}})
    except (ValueError,TypeError,sqlite3.Error):
        handler.reply(400,{'error':{'message':'请求不正确或查询范围过大'}})
    except (PermissionError,AuthorizationChanged):
        handler.reply(403,{'error':{'message':'群权限已变化，请重新查询'}})
    return True
