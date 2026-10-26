"""启动独立服务子进程，再运行 HTTP 演示；临时数据与凭据不进交付包。"""
import os,sys,socket,tempfile,subprocess,time,urllib.request
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='qunyi-verification-') as temp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=os.environ.copy();env['QUNYI_STATE']=temp;env['QUNYI_MODE']='mock';env.pop('QUNYI_TOKENS',None)
        proc=subprocess.Popen([sys.executable,'-m','qunyi','--port',str(port)],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            base=f'http://127.0.0.1:{port}';end=time.monotonic()+10
            while True:
                try:
                    with urllib.request.urlopen(base+'/health',timeout=1) as r:
                        if r.status==200:break
                except OSError:
                    if time.monotonic()>end or proc.poll() is not None:raise RuntimeError('服务启动失败')
                    time.sleep(.1)
            subprocess.run([sys.executable,'scripts/demo.py','--base',base,'--credentials',str(Path(temp)/'credentials.json'),'--output','docs/http-demo-report.json'],cwd=root,env=env,check=True)
            subprocess.run([sys.executable,'scripts/maintenance.py','backup','--state',temp,'--file',str(Path(temp)/'backup.sqlite')],cwd=root,check=True)
            proc.terminate();proc.wait(timeout=5)
            subprocess.run([sys.executable,'scripts/maintenance.py','restore','--state',str(Path(temp)/'restored'),'--file',str(Path(temp)/'backup.sqlite'),'--server-stopped'],cwd=root,check=True)
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
            if proc.stderr:proc.stderr.close()
if __name__=='__main__':main()
