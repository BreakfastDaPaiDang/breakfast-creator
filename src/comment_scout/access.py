from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ScoutError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 4, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.exit_code = code, message, exit_code
        self.details = details or {}

    def result(self) -> dict:
        result = {"ok": False, "status": self.code, "message": self.message}
        result.update(self.details)
        if self.code in {"needs_auth", "auth_expired"}:
            result["next_action"] = "ask_user_to_provide_credentials"
            result["user_prompt"] = (
                "请提供有效的B站SESSDATA完整原始值，由Agent保存到本地.env的"
                "BILIBILI_COOKIE配置。无需提供账号密码或自行填写配置。"
                "Agent不得回显凭据或将其写进任务文档。"
            )
        elif self.code == "rate_limited":
            result["next_action"] = "wait_then_resume; do_not_switch_accounts_or_retry_in_a_loop"
        return result


def atomic_text(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def validate_cookie(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    if not value or "\n" in value or "\r" in value:
        raise ScoutError("needs_auth", "Cookie缺失或格式无效。", exit_code=2)
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ScoutError("needs_auth", "Cookie应为浏览器请求头的原始值。", exit_code=2) from None
    pairs = dict(part.strip().split("=", 1) for part in value.split(";") if "=" in part)
    if not pairs.get("SESSDATA"):
        raise ScoutError("needs_auth", "Cookie缺少SESSDATA。", exit_code=2)
    return value


def load_cookie(env_file: Path) -> str:
    value = os.environ.get("BILIBILI_COOKIE", "").strip()
    if not value and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?BILIBILI_COOKIE\s*=\s*(.*)$", line)
            if match:
                value = match.group(1).strip()
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        raise ScoutError("needs_auth", "本地凭据格式无效。", exit_code=2) from None
                elif len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
    if not isinstance(value, str):
        raise ScoutError("needs_auth", "本地凭据格式无效。", exit_code=2)
    return validate_cookie(value)


def save_cookie(env_file: Path, value: str) -> None:
    if env_file.name != ".env":
        raise ScoutError("invalid_input", "凭据仅写入名为.env的文件。", exit_code=2)
    value = validate_cookie(value)
    lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
    lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?BILIBILI_COOKIE\s*=", line)]
    lines.append("BILIBILI_COOKIE=" + json.dumps(value, ensure_ascii=True))
    atomic_text(env_file, "\n".join(lines) + "\n")


class NetworkLease:
    """One network job per library, with OS-released locking after a process crash."""

    def __init__(self, library: Path):
        self.library = library
        self.db = None

    def __enter__(self):
        self.library.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.library / "network-lock.sqlite3", timeout=0)
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            self.db.close()
            raise ScoutError("busy", "已有评论网络任务运行，请等待其结束。", exit_code=3) from None
        return self

    def __exit__(self, *args):
        if self.db:
            self.db.rollback()
            self.db.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward authentication to a redirect destination.
        return None


# Public BV identifier encoding; numeric constants checked against the local
# bilibili-api aid_bvid_transformer reference (its conversion code is WTFPL).
BV_ALPHABET = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
BV_XOR = 23442827791579
BV_BOUND = 1 << 51


def aid_to_bvid(aid: int) -> str:
    if not 0 < aid < BV_BOUND:
        raise ScoutError("invalid_input", "av号超出有效范围。", exit_code=2)
    value = (BV_BOUND | aid) ^ BV_XOR
    chars = list("BV1" + "0" * 9)
    for index in range(11, 2, -1):
        value, digit = divmod(value, 58)
        chars[index] = BV_ALPHABET[digit]
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    return "".join(chars)


def bvid_to_aid(bvid: str) -> int:
    chars = list(bvid)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    value = 0
    try:
        for char in chars[3:]:
            value = value * 58 + BV_ALPHABET.index(char)
    except ValueError:
        raise ScoutError("invalid_input", "BV号包含无效字符。", exit_code=2) from None
    aid = (value & (BV_BOUND - 1)) ^ BV_XOR
    if aid_to_bvid(aid) != bvid:
        raise ScoutError("invalid_input", "BV号编码无效。", exit_code=2)
    return aid


class BilibiliClient:
    """Read-only GET adapter. No browser, fingerprint emulation, or automatic retries."""

    PATHS = {"/x/web-interface/nav", "/x/web-interface/view", "/x/v2/reply", "/x/v2/reply/reply",
             "/x/player/pagelist", "/x/player/wbi/v2"}

    def __init__(self, cookie: str, library: Path, interval: float = 3.0, *,
                 opener=None, clock=time.time, sleep=time.sleep):
        if not 1 <= interval <= 60:
            raise ScoutError("invalid_input", "请求间隔须为1—60秒，默认3秒。", exit_code=2)
        self.cookie = validate_cookie(cookie)
        self.rate_path = library / "request-state.json"
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.request_count = 0

    def _state(self) -> dict:
        if self.rate_path.exists():
            try:
                state = json.loads(self.rate_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError
                return state
            except (ValueError, TypeError):
                raise ScoutError("state_error", "请求节奏状态损坏，请检查本地状态文件。") from None
        return {}

    def _pace(self):
        state = self._state()
        now = self.clock()
        if state.get("blocked_until", 0) > now:
            raise ScoutError("rate_limited", "仍在访问限制冷却期，保留进度并稍后恢复。", exit_code=3,
                             details={"retry_at": state["blocked_until"], **state.get("cause", {})})
        delay = max(0, state.get("next_request_at", 0) - now)
        if delay > 60:
            raise ScoutError("rate_limited", "尚未到下一次允许请求的时间。", exit_code=3)
        if delay:
            self.sleep(delay)
        self.rate_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(self.rate_path, json.dumps({"next_request_at": self.clock() + self.interval}))

    def _blocked(self, retry_after: str | None = None, *, endpoint: str, http_status=None, api_code=None):
        seconds = 300
        if retry_after and retry_after.isdigit():
            seconds = max(seconds, int(retry_after))
        retry_at = self.clock() + seconds
        cause = {"endpoint": endpoint}
        if http_status is not None:
            cause["http_status"] = http_status
        if api_code is not None:
            cause["api_code"] = api_code
        atomic_text(self.rate_path, json.dumps({"blocked_until": retry_at, "cause": cause}))
        raise ScoutError("rate_limited", "平台拒绝或限制请求；至少冷却5分钟，已有评论进度保留。", exit_code=3,
                         details={**cause, "retry_at": retry_at})

    def get(self, path: str, params: dict | None = None) -> dict:
        if path not in self.PATHS:
            raise ScoutError("invalid_input", "不支持的接口。", exit_code=2)
        self._pace()
        url = "https://api.bilibili.com" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "Cookie": self.cookie, "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        })
        self.request_count += 1
        try:
            with self.opener.open(request, timeout=25) as response:
                raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise ScoutError("response_changed", "接口响应超过大小限制。")
            payload = json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code in {403, 412, 429}:
                self._blocked(error.headers.get("Retry-After") if error.headers else None,
                              endpoint=path, http_status=error.code)
            if error.code == 401:
                raise ScoutError("auth_expired", "B站登录已失效。", exit_code=2) from None
            raise ScoutError("network_error", f"接口HTTP状态{error.code}；未自动重试。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ScoutError("network_error", "网络请求失败；未自动重试，任务可恢复。") from None
        except (ValueError, UnicodeError):
            raise ScoutError("response_changed", "接口未返回预期JSON，任务已暂停。") from None
        if not isinstance(payload, dict) or "code" not in payload:
            raise ScoutError("response_changed", "接口响应结构改变，任务已暂停。")
        code = payload["code"]
        if code in {-101, -111}:
            raise ScoutError("auth_expired", "B站登录失效或凭据无效。", exit_code=2)
        if code in {-352, -412, -509, -403}:
            self._blocked(endpoint=path, api_code=code)
        if code != 0:
            # Never echo response messages, URLs, request headers or credential values.
            raise ScoutError("api_error", f"接口返回错误码{code}；已暂停，未跳过当前页。")
        expected = list if path == "/x/player/pagelist" else dict
        if not isinstance(payload.get("data"), expected):
            raise ScoutError("response_changed", "接口缺少data对象。")
        return payload

    def verify(self) -> dict:
        data = self.get("/x/web-interface/nav")["data"]
        if data.get("isLogin") is not True:
            raise ScoutError("auth_expired", "B站登录已失效，请更新本地Cookie。", exit_code=2)
        return {"ok": True, "status": "authenticated", "verified": True}

    def resolve(self, target: str) -> dict:
        target = target.strip()
        if target.startswith(("http://", "https://")):
            parsed = urllib.parse.urlsplit(target)
            if parsed.hostname not in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
                raise ScoutError("invalid_input", "请提供B站视频完整链接或BV/av号。", exit_code=2)
            target = parsed.path.strip("/").split("/")[-1]
        if re.fullmatch(r"BV[0-9A-Za-z]{10}", target):
            aid, bvid = bvid_to_aid(target), target
        elif re.fullmatch(r"av[0-9]+", target, re.I):
            aid = int(target[2:])
            bvid = aid_to_bvid(aid)
        else:
            raise ScoutError("invalid_input", "第一版支持BV/av号与视频链接；动态和番剧链接尚未接入。", exit_code=2)
        return {"platform": "bilibili", "resource_type": 1, "oid": str(aid),
                "bvid": bvid, "url": "https://www.bilibili.com/video/" + bvid,
                "title": bvid, "metadata_status": "not_requested", "id_resolution": "local_bv_av"}

    def page(self, source: dict, task: dict, sort: str) -> dict:
        params = {"oid": source["oid"], "type": source["resource_type"], "pn": task["page"], "ps": 20}
        if task["kind"] == "root":
            params["sort"] = 0 if sort == "newest" else 2
            return self.get("/x/v2/reply", params)
        params["root"] = task["root_id"]
        return self.get("/x/v2/reply/reply", params)
