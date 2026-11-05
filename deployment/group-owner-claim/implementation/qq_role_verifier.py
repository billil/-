"""Fail-closed QQ member role verifier; no external request at import time."""
import json, re, urllib.error, urllib.parse, urllib.request

class RoleVerificationUnavailable(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def verify_member_role(group_id, member_id, access_token, *, opener=None, timeout=8):
 """Return a role only when API confirms the exact member; unknown/error denies.

 The caller obtains IDs from a verified QQ event, never from a browser request.
 """
 if not all(isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}',x) for x in (group_id,member_id)):
  raise ValueError('invalid identifier')
 if not isinstance(access_token,str) or not access_token:raise RoleVerificationUnavailable('机器人访问凭证不可用')
 url='https://api.sgroup.qq.com/v2/groups/'+urllib.parse.quote(group_id,safe='')+'/members/'+urllib.parse.quote(member_id,safe='')
 request=urllib.request.Request(url,headers={'Authorization':'QQBot '+access_token,'Accept':'application/json'},method='GET')
 try:
  with (opener or urllib.request.build_opener(NoRedirect())).open(request,timeout=timeout) as response:
   raw=response.read(65537)
  if len(raw)>65536:raise ValueError('oversize')
  payload=json.loads(raw)
  if not isinstance(payload,dict):raise ValueError('bad response')
  if payload.get('member_openid')!=member_id:raise ValueError('member mismatch')
  role=payload.get('member_role')
  if role not in ('owner','admin','member'):raise ValueError('missing role')
  return role
 except urllib.error.HTTPError as exc:
  try: payload=json.loads(exc.read(65536)); code=payload.get('code')
  except Exception: code=None
  if code==11253:raise RoleVerificationUnavailable('QQ 群成员查询权限尚不可用（11253）') from None
  raise RoleVerificationUnavailable('QQ 群成员身份查询失败') from None
 except (urllib.error.URLError,TimeoutError,OSError,ValueError,TypeError,UnicodeError):
  raise RoleVerificationUnavailable('QQ 群成员身份查询失败') from None
