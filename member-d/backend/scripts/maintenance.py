import argparse,sqlite3,shutil
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['backup','restore']);ap.add_argument('--state',default='state');ap.add_argument('--file',required=True);ap.add_argument('--server-stopped',action='store_true');a=ap.parse_args()
    db=Path(a.state)/'qunyi.sqlite3';target=Path(a.file)
    if a.action=='backup':
        if not db.exists():raise SystemExit('数据库不存在')
        if target.exists():raise SystemExit('目标已存在，选择新文件名')
        if db.resolve()==target.resolve():raise SystemExit('不能备份到自身')
        src=sqlite3.connect(str(db));dst=sqlite3.connect(str(target))
        try:src.backup(dst)
        finally:dst.close();src.close()
    else:
        if not a.server_stopped:raise SystemExit('先停止服务并提供 --server-stopped')
        if not target.is_file():raise SystemExit('备份不存在')
        with sqlite3.connect('file:'+str(target.resolve())+'?mode=ro',uri=True) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('备份完整性检查失败')
            c.execute('SELECT version FROM schema_version').fetchone()
        db.parent.mkdir(parents=True,exist_ok=True)
        if db.exists():raise SystemExit('先移走当前 state，或恢复到新 state 目录，禁止直接覆盖')
        shutil.copyfile(target,db)
    print('Maintenance completed. Backups retain source data; protect and purge them separately.')
if __name__=='__main__':main()
