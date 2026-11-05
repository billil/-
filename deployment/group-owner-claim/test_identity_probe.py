import unittest
from identity_probe import IdentityProbe
class Tests(unittest.TestCase):
 def test_compare_does_not_store_identifiers(self):
  p=IdentityProbe(clock=lambda:0);p.record_join({'op_member_openid':'sensitive-group-identifier'})
  a=p.compare_private({'content':'群忆身份测试','author':{'user_openid':'sensitive-group-identifier'}})
  self.assertTrue(a['same_identifier']);self.assertNotIn('sensitive',repr(p.entries));self.assertNotIn('sensitive',repr(a))
 def test_other_user_and_other_text(self):
  p=IdentityProbe(clock=lambda:0);p.record_join({'op_member_openid':'a'})
  self.assertFalse(p.compare_private({'content':'群忆身份测试','author':{'user_openid':'b'}})['same_identifier'])
  self.assertIsNone(p.compare_private({'content':'other','author':{'user_openid':'a'}}))
 def test_expired_and_malformed(self):
  t=[0];p=IdentityProbe(clock=lambda:t[0],window_seconds=1);p.record_join({'op_member_openid':'a'});t[0]=2
  self.assertIsNone(p.compare_private({'content':'群忆身份测试','author':{'user_openid':'a'}}));self.assertEqual(len(p.entries),1)
  q=IdentityProbe(clock=lambda:0);self.assertFalse(q.compare_private({'content':'群忆身份测试','author':'bad'})['private_id_present'])
if __name__=='__main__':unittest.main()
