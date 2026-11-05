import io,json,unittest,urllib.error
from qq_role_verifier import verify_member_role,RoleVerificationUnavailable
class O:
 def __init__(self,data=None,code=None):self.data=data;self.code=code
 def open(self,req,timeout):
  if self.code:raise urllib.error.HTTPError(req.full_url,400,'',{},io.BytesIO(json.dumps({'code':self.code}).encode()))
  return io.BytesIO(json.dumps(self.data).encode())
class Tests(unittest.TestCase):
 def test_owner_and_member(self):
  for role in ('owner','admin','member'):
   self.assertEqual(verify_member_role('group','member','key',opener=O({'member_openid':'member','member_role':role})),role)
 def test_mismatch_fails_closed(self):
  with self.assertRaises(RoleVerificationUnavailable):verify_member_role('g','m','k',opener=O({'member_openid':'other','member_role':'owner'}))
 def test_missing_role_fails_closed(self):
  with self.assertRaises(RoleVerificationUnavailable):verify_member_role('g','m','k',opener=O({'member_openid':'m'}))
 def test_limited_access(self):
  with self.assertRaisesRegex(RoleVerificationUnavailable,'11253'):verify_member_role('g','m','k',opener=O(code=11253))
 def test_url_injection_rejected(self):
  with self.assertRaises(ValueError):verify_member_role('../g','m','k')
if __name__=='__main__':unittest.main()
