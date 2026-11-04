"""Short-lived, server-to-server workspace assertions; never sent to browsers."""
import hashlib
import hmac
import re
import time

_NAME = re.compile(r'user_[0-9a-f]{32}\Z')

def sign(workspace, key, now=None):
    if not _NAME.fullmatch(workspace) or not isinstance(key, str) or not key.isascii() or len(key) < 32:
        raise ValueError('invalid workspace signing configuration')
    ts=str(int(time.time() if now is None else now))
    body='qunyi1.'+workspace+'.'+ts
    return body+'.'+hmac.new(key.encode('ascii'),body.encode('ascii'),hashlib.sha256).hexdigest()

def verify(token, key, now=None):
    if not isinstance(token,str) or len(token)>180 or not isinstance(key,str) or not key.isascii() or len(key)<32:return None
    parts=token.split('.')
    if len(parts)!=4 or parts[0]!='qunyi1' or not _NAME.fullmatch(parts[1]) or not re.fullmatch(r'\d{1,12}',parts[2]) or not re.fullmatch(r'[0-9a-f]{64}',parts[3]):return None
    current=int(time.time() if now is None else now)
    if abs(current-int(parts[2]))>60:return None
    body='.'.join(parts[:3]);expected=hmac.new(key.encode('ascii'),body.encode('ascii'),hashlib.sha256).hexdigest()
    return parts[1] if hmac.compare_digest(parts[3],expected) else None
