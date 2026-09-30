from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from comment_scout.access import NoRedirect, NetworkLease, ScoutError, atomic_text


USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")


class ZhihuError(ScoutError):
    def result(self):
        result = {"ok": False, "status": self.code, "message": self.message, **self.details}
        if self.code in {"needs_auth", "auth_expired"}:
            result.update(next_action="ask_user_to_provide_credentials", user_prompt=
                          "请提供知乎Cookie原始请求头值（包含z_c0、d_c0，可带_xsrf）；由Agent保存到本地.env的ZHIHU_COOKIE，不需要账号密码，不回显凭据。")
        elif self.code in {"access_restricted", "rate_limited"}:
            result["next_action"] = "inspect_restriction; do_not_retry_in_a_loop_or_launch_browser"
        return result


def validate_cookie(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    if not value or len(value) > 65536 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ZhihuError("needs_auth", "Cookie缺失或格式无效。", exit_code=2)
    pairs = dict(p.strip().split("=", 1) for p in value.split(";") if "=" in p)
    if not pairs.get("z_c0") or not pairs.get("d_c0"):
        raise ZhihuError("needs_auth", "需要包含z_c0、d_c0的知乎Cookie。", exit_code=2)
    return value


def load_cookie(path: Path) -> str:
    value = os.environ.get("ZHIHU_COOKIE", "").strip()
    if not value and path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?ZHIHU_COOKIE\s*=\s*(.*)$", line)
            if match:
                value = match[1].strip()
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        raise ZhihuError("needs_auth", "本地Cookie配置格式无效。", exit_code=2) from None
                elif len(value) > 1 and value[0] == value[-1] == "'":
                    value = value[1:-1]
    if not isinstance(value, str):
        raise ZhihuError("needs_auth", "本地Cookie配置格式无效。", exit_code=2)
    return validate_cookie(value)


def save_cookie(path: Path, value: str):
    if path.name != ".env":
        raise ZhihuError("invalid_input", "凭据只写入.env。", exit_code=2)
    value = validate_cookie(value)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [s for s in lines if not re.match(r"^\s*(?:export\s+)?ZHIHU_COOKIE\s*=", s)]
    atomic_text(path, "\n".join(lines + ["ZHIHU_COOKIE=" + json.dumps(value)]) + "\n")


def endpoint_url(path: str, params=None) -> str:
    return "https://www.zhihu.com" + path + ("?" + urllib.parse.urlencode(params) if params else "")


def validate_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    allowed = r"/api/v4/(?:me|search_v3|questions/\d+(?:/answers)?|answers/\d+|comment_v5/(?:questions/\d+/root_comment|answers/\d+/root_comment|comment/\d+/child_comment))"
    if (u.scheme != "https" or u.netloc != "www.zhihu.com" or u.fragment
            or not re.fullmatch(allowed, u.path)):
        raise ZhihuError("invalid_endpoint", "接口地址不在知乎只读白名单中。")
    return u.path


class Client:
    def __init__(self, cookie: str, library: Path, interval=3.0, *, opener=None, clock=time.time, sleep=time.sleep):
        if not 1 <= interval <= 60:
            raise ZhihuError("invalid_input", "请求间隔须为1—60秒。", exit_code=2)
        self.cookie = validate_cookie(cookie)
        self.path = library / "request-state.json"
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.request_count = 0

    def pace(self):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            delay = max(0, float(state.get("next_request_at", 0)) - self.clock())
            blocked = float(state.get("blocked_until", 0))
        except (ValueError, TypeError, AttributeError):
            raise ZhihuError("state_error", "请求节奏文件损坏。") from None
        if blocked > self.clock() or delay > 60:
            raise ZhihuError("rate_limited", "仍在冷却期，请稍后检查并恢复。", exit_code=3,
                             details={"retry_at": max(blocked, self.clock() + delay)})
        if delay:
            self.sleep(delay)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(self.path, json.dumps({"next_request_at": self.clock() + self.interval}))

    def restrict(self, path, *, http_status=None, api_code=None, retry_after=None, hint=None):
        seconds = max(300, int(retry_after)) if retry_after and str(retry_after).isdigit() else 300
        retry_at = self.clock() + seconds
        atomic_text(self.path, json.dumps({"blocked_until": retry_at}))
        details = {"endpoint": path, "http_status": http_status, "api_code": api_code, "retry_at": retry_at}
        if hint:
            details["hint"] = hint
        raise ZhihuError("access_restricted", "平台限制访问，可能需要验证或请求签名；Cookie是否失效不能据此确定。已停止请求。",
                         exit_code=3, details=details)

    def get(self, url: str) -> dict:
        path = validate_url(url)
        self.pace()
        request = urllib.request.Request(url, headers={
            "Cookie": self.cookie, "User-Agent": USER_AGENT, "Accept": "application/json",
            "Referer": "https://www.zhihu.com/", "x-requested-with": "fetch", "x-api-version": "3.0.91",
        }, method="GET")
        if re.fullmatch(r"/api/v4/(?:questions/\d+(?:/answers)?|answers/\d+)", path):
            from .signing import headers as sign_headers
            for key, value in sign_headers(url, self.cookie).items():
                request.add_header(key, value)
        self.request_count += 1
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(16_000_001)
        except urllib.error.HTTPError as error:
            if error.code in {403, 412, 429}:
                api_code, hint = None, None
                try:
                    detail = json.loads(error.read(65536)).get("error", {})
                    value = detail.get("code")
                    if isinstance(value, int):
                        api_code = value
                    message = str(detail.get("message", ""))
                    if "x-zse" in message.lower():
                        hint = "request_signature_required"
                except (AttributeError, ValueError, OSError):
                    pass
                self.restrict(path, http_status=error.code,
                              api_code=api_code, hint=hint,
                              retry_after=error.headers.get("Retry-After") if error.headers else None)
            if error.code == 401:
                raise ZhihuError("auth_expired", "知乎登录凭据失效。", exit_code=2) from None
            if 300 <= error.code < 400:
                raise ZhihuError("access_restricted", "接口要求跳转，未转发Cookie。") from None
            raise ZhihuError("http_error", "接口请求失败，未自动重试。",
                             details={"endpoint": path, "http_status": error.code}) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ZhihuError("network_error", "网络请求失败，未自动重试。", details={"endpoint": path}) from None
        if len(raw) > 16_000_000:
            raise ZhihuError("response_changed", "响应超过大小限制。")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            if raw.lstrip().startswith(b"<"):
                self.restrict(path, http_status=200)
            raise ZhihuError("response_changed", "接口未返回JSON。") from None
        if not isinstance(payload, dict):
            raise ZhihuError("response_changed", "接口响应不是对象。")
        if payload.get("error"):
            error = payload["error"]
            code = error.get("code") if isinstance(error, dict) else None
            if not isinstance(code, (int, float)):
                code = None
            if code in {100, 401}:
                raise ZhihuError("auth_expired", "接口报告未登录。", exit_code=2)
            if code in {403, 10003, 40362, 40301}:
                self.restrict(path, api_code=code)
            raise ZhihuError("api_error", "接口返回错误，未跳过当前任务。", details={"endpoint": path, "api_code": code})
        return payload

    def verify(self):
        data = self.get(endpoint_url("/api/v4/me"))
        if not (data.get("id") or data.get("uid")):
            raise ZhihuError("auth_expired", "登录检查未取得账号标识。", exit_code=2)
        return {"ok": True, "status": "authenticated", "verified": True,
                "note": "只验证登录接口，不代表搜索、回答或评论接口已通过。"}
