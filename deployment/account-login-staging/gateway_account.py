"""Gateway account transition: single provisioned owner, no public registration."""
import http.server, http.client, http.cookies, json, hmac, os, re, threading, time, urllib.parse
from collections import defaultdict, deque
from account_store import AccountStore

AUTH_DB = os.environ.get('QUNYI_ACCOUNT_DB','/var/lib/qunyi-demo/accounts.sqlite3')
ACCESS_FILE = os.environ.get('QUNYI_ACCESS_FILE','/var/lib/qunyi-demo/access-token')
CREDENTIAL_FILE = os.environ.get('QUNYI_CREDENTIAL_FILE','/var/lib/qunyi/credentials.json')
UPSTREAM_PORT = int(os.environ.get('QUNYI_UPSTREAM_PORT','5173'))
LISTEN_PORT = int(os.environ.get('QUNYI_GATEWAY_PORT','8181'))
with open(ACCESS_FILE) as f: ACCESS = f.read().strip()
with open(CREDENTIAL_FILE) as f: TOKEN, WORKSPACE = next(iter(json.load(f).items()))
STORE = AccountStore(AUTH_DB)
os.chmod(AUTH_DB, 0o600)
RATE = defaultdict(deque)
LOCK = threading.Lock()

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def reply(self,status,obj,cookie=None):
        self.close_connection = True  # Rejected requests may have unread bodies.
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(data)));self.end_headers()
        if self.command != 'HEAD':self.wfile.write(data)
    def body(self):
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError: return None
        if n<0 or n>65536 or self.headers.get('Transfer-Encoding'): return None
        raw=self.rfile.read(n)
        try:
            v=json.loads(raw)
            return v if isinstance(v,dict) else None
        except (ValueError, UnicodeError): return None
    def session(self):
        try:
            c=http.cookies.SimpleCookie(); c.load(self.headers.get('Cookie',''))
            return c['qunyi_session'].value if 'qunyi_session' in c else ''
        except http.cookies.CookieError: return ''
    def same_origin(self):
        origin=self.headers.get('Origin','')
        host=self.headers.get('Host','')
        parsed=urllib.parse.urlsplit(origin)
        return parsed.scheme=='https' and parsed.netloc==host and not parsed.path and not parsed.query and not parsed.fragment
    def rate(self):
        # Bounded global limiter; all requests may share a proxy address.
        with LOCK:
            now=time.monotonic(); q=RATE['auth']
            while q and q[0]<now-60:q.popleft()
            if len(q)>=30:return False
            q.append(now);return True
    def handle_req(self):
        u=urllib.parse.urlsplit(self.path);p=u.path;m=self.command
        if '%' in p or '..' in p or '\\' in p or not p.startswith('/'):
            self.reply(403,{'error':'forbidden'});return
        if p=='/auth/session' and m=='GET':
            user=STORE.lookup(self.session())
            self.reply(200,{'authenticated':bool(user),'username':user[0] if user else None,'setup_required':not STORE.configured()});return
        if p in ('/auth/login','/auth/setup','/auth/logout') and m=='POST':
            if not self.same_origin():self.reply(403,{'error':'请求来源不匹配'});return
            if not self.rate():self.reply(429,{'error':'请求过于频繁，请稍后再试'});return
            data=self.body()
            if data is None:self.reply(400,{'error':'请求格式错误'});return
            if p=='/auth/logout':
                STORE.logout(self.session());self.reply(200,{'ok':True},'qunyi_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict');return
            if p=='/auth/setup':
                supplied=data.get('access_token','')
                if not isinstance(supplied,str) or not supplied.isascii() or not hmac.compare_digest(supplied,ACCESS):
                    self.reply(401,{'error':'验证失败'});return
                try: STORE.setup_owner(data.get('username',''),data.get('password',''),WORKSPACE)
                except (ValueError,TypeError):self.reply(400,{'error':'账号已设置或信息不符合要求'});return
            result=STORE.login(data.get('username',''),data.get('password',''),data.get('remember') is True)
            if not result:self.reply(401,{'error':'账号或密码错误'});return
            cookie='qunyi_session='+result[0]+'; Path=/; HttpOnly; Secure; SameSite=Strict'
            if data.get('remember') is True:cookie+='; Max-Age=2592000'
            self.reply(200,{'ok':True},cookie);return
        static=m in ('GET','HEAD') and (p in ('/','/index.html','/favicon.ico') or re.fullmatch(r'/assets/[A-Za-z0-9_./-]+',p))
        read=m in ('GET','HEAD') and (p in ('/api/health','/api/v1/capabilities','/api/v1/messages','/api/v1/coverage','/api/v1/events') or re.fullmatch(r'/api/v1/(messages|sessions|requests)/[A-Za-z0-9_-]+',p))
        query=m=='POST' and p=='/api/v1/query'
        if not (static or read or query):self.reply(403,{'error':'forbidden'});return
        if query and not self.same_origin():self.reply(403,{'error':'请求来源不匹配'});return
        if (read and p!='/api/health') or query:
            if not STORE.lookup(self.session()):
                # Backward compatibility only until owner enrollment completes.
                given=self.headers.get('Authorization','')
                if STORE.configured() or not hmac.compare_digest(given,'Bearer '+ACCESS):
                    self.reply(401,{'error':{'message':'请先登录'}});return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<0 or n>65536 or self.headers.get('Transfer-Encoding'):self.reply(413,{'error':'请求过大'});return
            body=self.rfile.read(n) if n else None
            headers={k:self.headers[k] for k in ('Content-Type','Accept') if self.headers.get(k)}
            if read or query:headers['Authorization']='Bearer '+TOKEN
            headers['Host']='127.0.0.1:5173'
            conn=http.client.HTTPConnection('127.0.0.1',UPSTREAM_PORT,timeout=50);conn.request(m,self.path,body,headers)
            resp=conn.getresponse();data=resp.read();self.send_response(resp.status)
            if resp.getheader('Content-Type'):self.send_header('Content-Type',resp.getheader('Content-Type'))
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if m!='HEAD':self.wfile.write(data)
            conn.close()
        except (OSError,ValueError):self.close_connection=True
    do_GET=handle_req;do_HEAD=handle_req;do_POST=handle_req
    do_PATCH=handle_req;do_DELETE=handle_req;do_PUT=handle_req

if __name__=='__main__':http.server.ThreadingHTTPServer(('127.0.0.1',LISTEN_PORT),Handler).serve_forever()
