from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from comment_scout.access import ScoutError, atomic_text, load_cookie as load_bili, save_cookie as save_bili
from zhihu_scout.access import load_cookie as load_zhihu, save_cookie as save_zhihu


class ConfigError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


class Config:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.profile_path = self.root / "config/creator-profile.json"
        self.examples = self.root / "assets/references/style-examples"
        self.env_path = self.root / ".env"

    def initialize(self):
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.examples.mkdir(parents=True, exist_ok=True)
        with self.lock():
            if not self.profile_path.exists():
                atomic_text(self.profile_path, json.dumps({
                    "schema_version": 1, "user_info": "", "updated_at": None,
                }, ensure_ascii=False, indent=2) + "\n")

    @contextmanager
    def lock(self):
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.profile_path.parent / ".write-lock.sqlite3", timeout=5)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield
        finally:
            db.rollback()
            db.close()

    def profile(self):
        if not self.profile_path.exists():
            return {"schema_version": 1, "user_info": "", "updated_at": None}, "missing"
        raw = self.profile_path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8-sig"))
            if (not isinstance(value, dict) or value.get("schema_version") != 1
                    or not isinstance(value.get("user_info"), str)):
                raise ValueError
        except (UnicodeError, ValueError, TypeError):
            raise ConfigError("用户信息文件格式有误，请让 Agent 检查；原文件未修改。", 409) from None
        return value, hashlib.sha256(raw).hexdigest()

    def save_profile(self, user_info, revision):
        if not isinstance(user_info, str) or not user_info.strip():
            raise ConfigError("写一点关于你的信息，再保存。")
        if len(user_info) > 30000:
            raise ConfigError("用户信息请控制在三万字以内。")
        with self.lock():
            current, actual = self.profile()
            if revision != actual:
                raise ConfigError("用户信息刚被更新，请先载入最新内容。你输入的内容仍保留。", 409)
            current.update(user_info=user_info.strip(), updated_at=datetime.now(timezone.utc).isoformat())
            atomic_text(self.profile_path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
        return self.state()

    def connection(self, platform):
        loader = {"bilibili": load_bili, "zhihu": load_zhihu}[platform]
        key = "BILIBILI_COOKIE" if platform == "bilibili" else "ZHIHU_COOKIE"
        try:
            loader(self.env_path)
            return {"configured": True, "status": "configured", "verified": False,
                    "managed_by_environment": bool(os.environ.get(key, "").strip())}
        except ScoutError:
            return {"configured": False, "status": "needs_config", "verified": False,
                    "managed_by_environment": bool(os.environ.get(key, "").strip())}

    def save_connection(self, platform, body):
        if platform not in {"bilibili", "zhihu"}:
            raise ConfigError("未找到这个平台。", 404)
        if self.connection(platform)["managed_by_environment"]:
            raise ConfigError("此连接由环境变量提供，请让 Agent 更新对应环境配置。", 409)
        fields = ["SESSDATA"] if platform == "bilibili" else ["z_c0", "d_c0", "_xsrf"]
        parts = []
        for name in fields:
            value = body.get(name, "")
            if not isinstance(value, str) or len(value) > 65536:
                raise ConfigError("凭据格式不正确，请复制字段的完整值。")
            value = value.strip()
            if not value and name == "_xsrf":
                continue
            if not value or any(ord(c) < 32 or ord(c) > 126 for c in value) or ";" in value:
                raise ConfigError("请粘贴单个字段的完整值，不要粘贴整行 Cookie 或换行。")
            parts.append(name + "=" + value)
        try:
            with self.lock():
                saver = save_bili if platform == "bilibili" else save_zhihu
                saver(self.env_path, "; ".join(parts))
        except ScoutError:
            raise ConfigError("凭据格式不正确，请检查字段后重试。") from None
        return self.state()

    def state(self):
        profile, revision = self.profile()
        files = sorted(p.name for p in self.examples.iterdir()
                       if p.is_file() and p.name.lower() not in {"readme.md", ".gitkeep", "desktop.ini"}) if self.examples.exists() else []
        connections = {name: self.connection(name) for name in ("bilibili", "zhihu")}
        ready = bool(profile["user_info"].strip())
        return {"ok": True, "profile": profile, "revision": revision, "ready": ready,
                "missing_required": [] if ready else ["user_info"],
                "missing_optional": (["style_examples"] if not files else []) +
                    [name for name, status in connections.items() if not status["configured"]],
                "examples": {"path": str(self.examples), "count": len(files), "files": files[:8]},
                "connections": connections}

    def open_examples(self):
        self.examples.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            raise ConfigError("请复制文件夹路径，在本机打开。")
        os.startfile(self.examples)
        return {"ok": True}
