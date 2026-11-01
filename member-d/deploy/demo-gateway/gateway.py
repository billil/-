import http.server, http.client, re, urllib.parse, json, hmac

with open('/var/lib/qunyi-demo/access-token') as f: DEMO_TOKEN = f.read().strip()
with open('/var/lib/qunyi/credentials.json') as f: BACKEND_TOKEN = next(iter(json.load(f)))

class Gateway(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, fmt, *args):
        pass
    def handle_req(self):
        u = urllib.parse.urlsplit(self.path)
        p = u.path
        method = self.command
        if '%' in p or '..' in p or '\\' in p or not p.startswith('/'):
            self.send_error(403); return
        static = method in ('GET','HEAD') and (p in ('/', '/index.html', '/favicon.ico') or re.fullmatch(r'/assets/[A-Za-z0-9_./-]+', p))
        read = method in ('GET','HEAD') and (p in ('/api/health','/api/v1/capabilities','/api/v1/messages','/api/v1/coverage','/api/v1/events') or re.fullmatch(r'/api/v1/(messages|sessions|requests)/[A-Za-z0-9_-]+', p))
        query = method == 'POST' and p == '/api/v1/query'
        if not (static or read or query):
            self.send_error(403); return
        if (read and p != '/api/health') or query:
            given = self.headers.get('Authorization', '')
            if not hmac.compare_digest(given, 'Bearer ' + DEMO_TOKEN):
                self.send_error(401); return
        try:
            n = int(self.headers.get('Content-Length','0'))
            if n < 0 or n > 65536 or self.headers.get('Transfer-Encoding'):
                self.send_error(413); return
            body = self.rfile.read(n) if n else None
            headers = {}
            for name in ('Authorization','Content-Type','Accept'):
                if self.headers.get(name): headers[name] = self.headers[name]
            if read or query: headers['Authorization'] = 'Bearer ' + BACKEND_TOKEN
            headers['Host'] = '127.0.0.1:5173'
            conn = http.client.HTTPConnection('127.0.0.1',5173, timeout=50)
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse(); data = resp.read()
            self.send_response(resp.status)
            for name in ('Content-Type',):
                if resp.getheader(name): self.send_header(name,resp.getheader(name))
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if method != 'HEAD': self.wfile.write(data)
            conn.close()
        except (OSError, ValueError):
            self.close_connection = True
    do_GET = handle_req
    do_HEAD = handle_req
    do_POST = handle_req
    do_PATCH = handle_req
    do_DELETE = handle_req
    do_PUT = handle_req

http.server.ThreadingHTTPServer(('127.0.0.1', 8181),Gateway).serve_forever()
