import unittest
from claim_http import handle
class H:
 def __init__(self,user=True,origin=True):self.user=user;self.origin=origin;self.response=None
 def session_user(self):return ('u','id') if self.user else None
 def same_origin(self):return self.origin
 def rate(self):return True
 def reply(self,status,body):self.response=status,body
class Tests(unittest.TestCase):
 def test_rejects_unauthenticated(self):
  h=H(False);self.assertTrue(handle(h,'GET','/group-access/claim/status'));self.assertEqual(h.response[0],401)
 def test_never_grants(self):
  h=H();handle(h,'POST','/group-access/claim/start');self.assertEqual(h.response[0],503);self.assertFalse(h.response[1]['admin_granted'])
 def test_checks_origin(self):
  h=H(origin=False);handle(h,'POST','/group-access/claim/start');self.assertEqual(h.response[0],403)
if __name__=='__main__':unittest.main()
