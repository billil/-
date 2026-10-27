import argparse,json,os
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
from .config import Config
from .service import Service
from .store import uid

MAX_BODY=1048576
class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,address,service):self.service=service;super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    server_version='Qunyi/0.1'
    def setup(self):super().setup();self.connection.settimeout(10)
    def log_message(self,*args):pass  # Never log bearer tokens, URLs, payloads or exceptions.
    def reply(self,status,data):
        blob=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(blob)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Connection','close');self.end_headers()
        try:self.wfile.write(blob)
        except (BrokenPipeError,ConnectionResetError):pass
    def handle_api(self):
        parsed=urlsplit(self.path);body=None
        try:
            # No cookie auth or permissive CORS; front-end should proxy same-origin.
            if self.headers.get('Transfer-Encoding'):raise ValueError('UNSUPPORTED_TRANSFER_ENCODING')
            if self.command in ('POST','PATCH'):
                if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                    return self.reply(415,{'status':'error','error':{'code':'JSON_REQUIRED','message':'需要 application/json'},'request_id':uid('req')})
                n=int(self.headers.get('Content-Length','0'))
                if n<0 or n>MAX_BODY:return self.reply(413,{'status':'error','error':{'code':'BODY_TOO_LARGE','message':'请求不能超过1 MiB'},'request_id':uid('req')})
                raw=self.rfile.read(n)
                if len(raw)!=n:raise ValueError()
                body=json.loads(raw.decode('utf-8'))
            params={k:v[-1] for k,v in parse_qs(parsed.query).items()}
            status,data=self.server.service.dispatch(self.command,parsed.path,body,self.headers.get('Authorization',''),params)
            self.reply(status,data)
        except (ValueError,UnicodeError,TimeoutError):self.reply(400,{'status':'error','error':{'code':'INVALID_JSON','message':'请求格式不正确'},'request_id':uid('req')})
    do_GET=handle_api;do_POST=handle_api;do_PATCH=handle_api;do_DELETE=handle_api

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args();cfg=Config.load();service=Service(cfg)
    server=Server((args.host,args.port),service)
    print(f'Qunyi backend http://{args.host}:{args.port} mode={cfg.mode}',flush=True)
    print('Credentials: QUNYI_TOKENS environment or state/credentials.json; do not share this file.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();service.store.close()
if __name__=='__main__':main()
