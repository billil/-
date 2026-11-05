"""Public-facing claim endpoint: deliberately disabled until QQ role checks work."""

def handle(handler, method, path):
 if path not in ('/group-access/claim/status','/group-access/claim/start'):return False
 user=handler.session_user()
 if not user:
  handler.reply(401,{'error':'请先登录'});return True
 if method=='GET' and path.endswith('/status'):
  handler.reply(200,{'available':False,'admin_granted':False,'reason':'QQ群成员身份验证暂不可用'});return True
 if method=='POST' and path.endswith('/start'):
  if not handler.same_origin():handler.reply(403,{'error':'请求来源不匹配'});return True
  if not handler.rate():handler.reply(429,{'error':'请求过于频繁'});return True
  handler.reply(503,{'error':'群认领暂不可用：QQ身份验证权限尚未开通','admin_granted':False});return True
 handler.reply(405,{'error':'请求方式不支持'});return True
