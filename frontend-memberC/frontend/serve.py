#!/usr/bin/env python3
"""群忆前端本地服务器：静态文件 + 同源 API 代理。

为什么需要它：
    成员 D 的后端（member-d/backend）明确只做 API，不带 CORS 头、也不处理
    OPTIONS 预检（见 qunyi/server.py 注释）。浏览器从 127.0.0.1:5173 直接
    fetch 127.0.0.1:8000 会被 CORS 拦下。所以前端统一请求同源的 /api/...，
    由本脚本转发到后端，浏览器侧不存在跨域。

用法：
    python serve.py                # 默认 http://127.0.0.1:5173 -> 后端 8000
    python serve.py --port 5174
    python serve.py --backend http://127.0.0.1:8100
"""
import argparse
import http.client
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
API_PREFIX = '/api'
# 逐跳头，不转发
HOP_BY_HOP = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length',
}
EXTRA_TYPES = {
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'QunyiUI/0.1'
    protocol_version = 'HTTP/1.1'
    backend = ('127.0.0.1', 8000)

    # ---- 基础工具 -------------------------------------------------------
    def log_message(self, fmt, *args):
        # 只记方法与路径，不记查询串（可能有筛选条件）和请求头（有令牌）。
        try:
            sys.stderr.write('%s %s\n' % (self.command, urlsplit(self.path).path))
        except Exception:
            pass

    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status, payload):
        import json
        self._send(status, json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    # ---- 路由 -----------------------------------------------------------
    def do_GET(self):
        if self._is_api():
            return self._proxy()
        return self._static()

    def do_HEAD(self):
        if self._is_api():
            return self._proxy()
        return self._static()

    def do_POST(self):
        return self._proxy() if self._is_api() else self._json(
            405, {'status': 'error', 'error': {'code': 'METHOD_NOT_ALLOWED', 'message': '静态资源只支持 GET'}})

    do_PATCH = do_POST
    do_DELETE = do_POST
    do_PUT = do_POST

    def do_OPTIONS(self):
        # 同源场景其实用不到；保留以便将来把前端放到别的端口调试。
        self._send(204, b'', 'text/plain', [
            ('Allow', 'GET, POST, PATCH, DELETE, OPTIONS'),
            ('Access-Control-Allow-Origin', self.headers.get('Origin', '*')),
            ('Access-Control-Allow-Headers', 'Authorization, Content-Type'),
            ('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS'),
        ])

    def _is_api(self):
        p = urlsplit(self.path).path
        return p == API_PREFIX or p.startswith(API_PREFIX + '/')

    # ---- API 代理 -------------------------------------------------------
    def _proxy(self):
        parsed = urlsplit(self.path)
        target = parsed.path[len(API_PREFIX):] or '/'
        if parsed.query:
            target += '?' + parsed.query

        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}

        conn = http.client.HTTPConnection(*self.backend, timeout=90)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status, resp_headers = resp.status, resp.getheaders()
        except OSError as exc:
            self._json(502, {
                'status': 'error',
                'error': {
                    'code': 'BACKEND_UNREACHABLE',
                    'message': '代理连不上后端 %s:%s，请先在后端目录运行 python -m qunyi' % self.backend,
                },
                'detail': type(exc).__name__,
            })
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        passthrough = [(k, v) for k, v in resp_headers if k.lower() not in HOP_BY_HOP]
        self.send_response(status)
        for k, v in passthrough:
            self.send_header(k, v)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---- 静态文件 -------------------------------------------------------
    def _static(self):
        rel = unquote(urlsplit(self.path).path).lstrip('/')
        if rel == '':
            rel = 'index.html'
        full = os.path.realpath(os.path.join(ROOT, rel))
        if not (full == ROOT or full.startswith(ROOT + os.sep)):
            return self._json(403, {'status': 'error', 'error': {'code': 'FORBIDDEN', 'message': '路径越界'}})
        if os.path.isdir(full):
            full = os.path.join(full, 'index.html')
        if not os.path.isfile(full):
            return self._send(404, ('未找到 %s' % rel).encode('utf-8'), 'text/plain; charset=utf-8')
        ext = os.path.splitext(full)[1].lower()
        ctype = EXTRA_TYPES.get(ext) or mimetypes.guess_type(full)[0] or 'application/octet-stream'
        try:
            with open(full, 'rb') as fh:
                body = fh.read()
        except OSError:
            return self._send(500, b'read error', 'text/plain; charset=utf-8')
        self._send(200, body, ctype)


def main():
    ap = argparse.ArgumentParser(description='群忆前端本地服务器（静态 + API 代理）')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5173)
    ap.add_argument('--backend', default='http://127.0.0.1:8000')
    args = ap.parse_args()

    parts = urlsplit(args.backend)
    Handler.backend = (parts.hostname or '127.0.0.1', parts.port or 80)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print('Qunyi UI  http://%s:%d  ->  backend %s' % (args.host, args.port, args.backend))
    print('Ctrl+C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
