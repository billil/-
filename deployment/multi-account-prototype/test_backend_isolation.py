import os, pathlib, shutil, subprocess, sys, tempfile, textwrap, unittest

class BackendIsolation(unittest.TestCase):
    def test_real_service_filters_messages_and_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);shutil.copytree('qunyi-fix-work/member-d/backend/qunyi',root/'qunyi',ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy('multi-account/service.py',root/'qunyi/service.py');shutil.copy('multi-account/workspace_identity.py',root/'qunyi/workspace_identity.py')
            code='''
import os,tempfile
from pathlib import Path
from qunyi.config import Config
from qunyi.service import Service
from qunyi.workspace_identity import sign
key='k'*40;os.environ['QUNYI_INTERNAL_WORKSPACE_KEY']=key
with tempfile.TemporaryDirectory() as td:
 cfg=Config(state=Path(td),tokens={'owner-token-123456789':'demo'})
 svc=Service(cfg)
 owner='Bearer owner-token-123456789';a='Bearer '+sign('user_'+'a'*32,key);b='Bearer '+sign('user_'+'b'*32,key)
 st,res=svc.dispatch('POST','/v1/imports',{'messages':[{'content':'private owner notice','group_label':'owner group'}]},owner)
 assert st==200,(st,res)
 st,items=svc.dispatch('GET','/v1/messages',authorization=owner);assert items['total']==1
 mid=items['items'][0]['id']
 for token in (a,b):
  st,res=svc.dispatch('GET','/v1/messages',authorization=token);assert st==200 and res['total']==0,(st,res)
  st,res=svc.dispatch('GET','/v1/messages/'+mid,authorization=token);assert st==404,(st,res)
  st,res=svc.dispatch('GET','/v1/coverage',authorization=token);assert not res.get('groups'),res
 svc.store.db.close()
'''
            env=dict(os.environ,PYTHONPATH=str(root));r=subprocess.run([sys.executable,'-c',code],cwd=td,env=env,text=True,capture_output=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)

if __name__=='__main__':unittest.main()
