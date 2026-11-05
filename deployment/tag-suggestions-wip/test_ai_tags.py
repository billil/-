import io,json,unittest
from ai_tags import suggest, SuggestionError
class Access:
 def __init__(self):self.groups={'g'}
 def allowed_groups(self,a):return self.groups
class Reader:
 def __init__(self,yes=True):self.yes=yes
 def combined(self,a,l,o,i):return ([{'content':'请周五前提交作业'}] if self.yes else [],1)
class Opener:
 def __init__(self,tags):self.tags=tags;self.called=False
 def open(self,req,timeout):
  self.called=True;body=json.dumps({'choices':[{'message':{'content':json.dumps({'tags':self.tags},ensure_ascii=False)}}]},ensure_ascii=False).encode();return io.BytesIO(body)
class Tests(unittest.TestCase):
 def test_filter(self):
  op=Opener(['作业','待处理','敏感 信息','作业','报名','额外']);self.assertEqual(suggest(Reader(),Access(),'u','id','https://example.com','m','key',opener=op),['作业','待处理','报名']);self.assertTrue(op.called)
 def test_denied_never_calls_model(self):
  op=Opener([])
  with self.assertRaises(PermissionError):suggest(Reader(False),Access(),'u','id','https://example.com','m','key',opener=op)
  self.assertFalse(op.called)
 def test_not_configured(self):
  with self.assertRaises(SuggestionError):suggest(Reader(),Access(),'u','id','','','')
if __name__=='__main__':unittest.main()
