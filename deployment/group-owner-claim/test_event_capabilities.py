import unittest
from event_capabilities import group_add_capabilities
class Tests(unittest.TestCase):
 def test_event_with_identifiers(self):
  result=group_add_capabilities({'group_openid':'group-secret','op_member_openid':'user-secret'})
  self.assertEqual(result,{'group_identifier_present':True,'operator_identifier_present':True})
  self.assertNotIn('secret',str(result))
 def test_missing_or_malformed(self):
  self.assertFalse(group_add_capabilities({'group_openid':' ','op_member_openid':42})['operator_identifier_present'])
  self.assertFalse(group_add_capabilities(None)['group_identifier_present'])
  self.assertFalse(group_add_capabilities({'op_member_openid':'x'*257})['operator_identifier_present'])
if __name__=='__main__':unittest.main()
