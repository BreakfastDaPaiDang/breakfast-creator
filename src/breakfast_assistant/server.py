from __future__ import annotations

import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config, ConfigError


WEB = Path(__file__).parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: Config, port=8765):
        self.config = config
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.authorities = {self.authority, f"localhost:{self.server_port}"}
        self.origin = "http://" + self.authority


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # No request bodies, user information or credential logs.

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self, api=False):
        host = self.headers.get("Host")
        if host not in self.server.authorities:
            raise ConfigError("请求来源不受支持。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ConfigError("请在本地页面操作。", 403)
        if self.headers.get("Origin") not in (None, "http://" + host):
            raise ConfigError("请在本地页面操作。", 403)
        if api and not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.token):
            raise ConfigError("页面已更新，请刷新后重试。", 403)

    def do_GET(self):
        try:
            self.trusted(api=self.path.startswith("/api/"))
            if self.path == "/api/state":
                return self.reply(200, self.server.config.state())
            if self.path not in ASSETS:
                raise ConfigError("未找到页面。", 404)
            filename, mime = ASSETS[self.path]
            body = (WEB / filename).read_bytes()
            if filename == "index.html":
                body = body.replace(b"__STUDIO_TOKEN__", self.server.token.encode())
            self.reply(200, body, mime)
        except ConfigError as error:
            self.reply(error.status, {"ok": False, "message": error.message})
        except (OSError, ValueError, TypeError, sqlite3.Error):
            self.reply(500, {"ok": False, "message": "本地文件暂时无法读取，请让 Agent 检查。"})

    def do_POST(self):
        try:
            self.trusted(api=True)
            if self.headers.get("Content-Type") != "application/json":
                raise ConfigError("请使用页面上的保存按钮。", 415)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 300000:
                raise ConfigError("提交内容过大或为空。", 413)
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ConfigError("提交格式不正确。")
            if self.path == "/api/profile":
                result = self.server.config.save_profile(body.get("user_info"), body.get("revision"))
            elif self.path in {"/api/connections/bilibili", "/api/connections/zhihu"}:
                result = self.server.config.save_connection(self.path.rsplit("/", 1)[1], body)
            elif self.path == "/api/examples/open":
                result = self.server.config.open_examples()
            else:
                raise ConfigError("未找到这个操作。", 404)
            self.reply(200, result)
        except ConfigError as error:
            self.reply(error.status, {"ok": False, "message": error.message})
        except (ValueError, UnicodeError, TypeError):
            self.reply(400, {"ok": False, "message": "提交格式不正确，请重试。"})
        except (OSError, TimeoutError, sqlite3.Error):
            self.reply(500, {"ok": False, "message": "保存未完成，请重试；输入内容仍保留。"})
