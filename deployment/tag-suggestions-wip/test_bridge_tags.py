import os, unittest
from unittest.mock import patch
import main_bridge
class Handler:
 path='/api/v1/tag-suggestions'
 def __init__(self,user=('id','alice'),body=None,origin=True):self.user=user;self.data=body or {'message_id':'m1'};self.origin=origin;self.response=None
 def session_user(self):return self.user
 def same_origin(self):return self.origin
 def rate_query(self,u):return True
 def body(self):return self.data
 def reply(self,status,body):self.response=(status,body)
class Access:
 def __init__(self,ok=True):self.ok=ok
 def allowed_groups(self,a):return {'g'} if self.ok else set()
class Tests(unittest.TestCase):
 def test_unauth_rejected(self):
  h=Handler(None);self.assertTrue(main_bridge.handle(h,Access(),None,None,'POST',h.path));self.assertEqual(h.response[0],403)
 def test_bad_origin_never_calls_model(self):
  h=Handler(origin=False)
  with patch.object(main_bridge,'suggest') as fn:main_bridge.handle(h,Access(),None,None,'POST',h.path);fn.assert_not_called()
  self.assertEqual(h.response[0],403)
 def test_success(self):
  h=Handler()
  with patch.object(main_bridge,'suggest',return_value=['作业']):main_bridge.handle(h,Access(),None,None,'POST',h.path)
  self.assertEqual(h.response,(200,{'tags':['作业']}))
if __name__=='__main__':unittest.main()
