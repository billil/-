"""Short-lived diagnostics only; never an identity verifier or an authorization grant."""
import hashlib, hmac, os, time

class IdentityProbe:
    def __init__(self, clock=time.monotonic, window_seconds=1800):
        self.clock=clock
        self.deadline=clock()+window_seconds
        self.key=os.urandom(32)
        self.entries=[]
    def _digest(self, value):
        if not isinstance(value,str) or not value.strip() or len(value)>256:return None
        return hmac.new(self.key,value.encode(),hashlib.sha256).digest()
    def record_join(self,data):
        if self.clock()>self.deadline or not isinstance(data,dict): return
        digest=self._digest(data.get('op_member_openid'))
        if digest is not None:self.entries=(self.entries+[digest])[-10:]
    def compare_private(self,data):
        if self.clock()>self.deadline or not isinstance(data,dict) or (not isinstance(data.get('content'),str) or data['content'].strip()!='群忆身份测试'):return None
        author=data.get('author') or {}
        if not isinstance(author,dict):return {'private_id_present':False,'join_samples':len(self.entries),'same_identifier':False}
        digest=self._digest(author.get('user_openid'))
        return {'private_id_present':digest is not None,'join_samples':len(self.entries), 'same_identifier':digest is not None and any(hmac.compare_digest(digest,x) for x in self.entries)}
