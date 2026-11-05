import tempfile,unittest,sqlite3
from pathlib import Path
from claim_workflow import ClaimWorkflow,ClaimError
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=[1000];self.w=ClaimWorkflow(Path(self.tmp.name)/'db',b'x'*32,lambda:self.now[0])
 def tearDown(self):self.tmp.cleanup()
 def record(self,event,group,op):
  self.now[0]+=1;self.w.record_authenticated_join(event,group,op,self.now[0])
 def test_good_identity_never_grants(self):
  code=self.w.begin('u1');self.now[0]+=1;self.record('e','group','qq');self.w.bind_authenticated_private_message(code,'qq')
  self.assertEqual(self.w.status('u1')['state'],'identity_observed');self.assertFalse(self.w.status('u1')['admin_granted'])
 def test_wrong_sender_cannot_consume_code(self):
  code=self.w.begin('u1');self.record('e','group','qq')
  with self.assertRaises(ClaimError):self.w.bind_authenticated_private_message(code,'wrong')
  self.assertEqual(self.w.status('u1')['state'],'pending')
 def test_replay_or_code_reuse_fails(self):
  a=self.w.begin('u1');b=self.w.begin('u2');self.record('e','g','q');self.w.bind_authenticated_private_message(a,'q')
  for code in (a,b):
   with self.assertRaises(ClaimError):self.w.bind_authenticated_private_message(code,'q')
 def test_expiry_and_join_before_challenge(self):
  self.record('old','g','q');self.now[0]+=1;c=self.w.begin('u');
  with self.assertRaises(ClaimError):self.w.bind_authenticated_private_message(c,'q')
  self.now[0]+=600;self.assertEqual(self.w.status('u')['state'],'expired')
 def test_ambiguous_group_is_rejected(self):
  c=self.w.begin('u');self.record('e1','g1','q');self.record('e2','g2','q')
  with self.assertRaises(ClaimError):self.w.bind_authenticated_private_message(c,'q')
 def test_plaintext_is_not_stored(self):
  code=self.w.begin('u');self.record('e','g','sensitive-id')
  data=Path(self.w.path).read_bytes();self.assertNotIn(code.encode(),data);self.assertNotIn(b'sensitive-id',data)
 def test_stale_join_rejected(self):
  with self.assertRaises(ClaimError):self.w.record_authenticated_join('e','g','q',699)
  with self.assertRaises(ClaimError):self.w.record_authenticated_join('e','g','q',1031)
 def test_superseded_code(self):
  old=self.w.begin('u');self.w.begin('u');self.record('e','g','q')
  with self.assertRaises(ClaimError):self.w.bind_authenticated_private_message(old,'q')
if __name__=='__main__':unittest.main()
