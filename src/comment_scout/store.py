from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .access import ScoutError, atomic_text


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        raise ScoutError("invalid_input", "评论ID须为整数或字符串，不能使用浮点数。", exit_code=2)
    return str(value)


def normalize(reply: dict, root_id: str, source: dict, fetched_at: str) -> dict:
    if not isinstance(reply, dict) or not reply.get("rpid") or not isinstance(reply.get("content"), dict):
        raise ScoutError("response_changed", "评论字段不完整，当前页未提交。")
    rid = identifier(reply.get("rpid_str") or reply["rpid"])
    root = identifier(reply.get("root_str") or reply.get("root"))
    if root in {"", "0"}:
        root = root_id or rid
    parent = identifier(reply.get("parent_str") or reply.get("parent"))
    if parent in {"", "0"}:
        parent = root_id or None
    try:
        stamp = datetime.fromtimestamp(int(reply["ctime"]), timezone.utc).isoformat()
        likes = int(reply.get("like", 0))
        replies = int(reply.get("rcount", 0))
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        raise ScoutError("response_changed", "评论时间或计数字段异常，当前页未提交。") from None
    text = reply["content"].get("message")
    if not isinstance(text, str):
        raise ScoutError("response_changed", "评论正文不是字符串。")
    return {"id": rid, "root_id": root, "parent_id": parent, "author_id": identifier(reply.get("mid")),
            "text": text, "created_at": stamp, "likes": likes, "reply_count": replies,
            "source_url": source.get("url"), "comment_url": source.get("url", "") + "#reply" + rid,
            "fetched_at": fetched_at}


class Session:
    def __init__(self, path: Path, *, source: dict | None = None, sort="newest", replies=True):
        self.path = path
        if source is not None:
            path.mkdir(parents=True, exist_ok=False)
        elif not (path / "session.sqlite3").is_file():
            raise ScoutError("invalid_input", "未找到评论会话数据库。", exit_code=2)
        self.db = sqlite3.connect(path / "session.sqlite3", timeout=1)
        self.db.row_factory = sqlite3.Row
        if source is not None:
            self.db.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE comments (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE tasks (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, root_id TEXT NOT NULL,
                  page INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0, UNIQUE(kind,root_id,page));
                CREATE TABLE pages (id INTEGER PRIMARY KEY, kind TEXT, root_id TEXT, page INTEGER,
                  fetched_at TEXT, digest TEXT, body TEXT NOT NULL);
            """)
            self.set_meta({"schema_version": 1, "source": source, "sort": sort,
                           "include_replies": replies, "created_at": now(), "status": "pending",
                           "coverage": "Only comments returned by the API at collection time; not historical/deleted/hidden comments."})
            self.db.execute("INSERT INTO tasks(kind,root_id,page) VALUES ('root','',1)")
            self.db.commit()

    def close(self):
        self.db.close()

    def meta(self) -> dict:
        try:
            value = self.db.execute("SELECT value FROM metadata WHERE key='session'").fetchone()
            result = json.loads(value[0])
            if result["schema_version"] != 1:
                raise ValueError
            return result
        except (TypeError, KeyError, ValueError, sqlite3.Error):
            raise ScoutError("state_error", "会话格式损坏或版本不受支持。") from None

    def set_meta(self, meta: dict):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('session',?)", (json.dumps(meta, ensure_ascii=False),))

    def status(self) -> dict:
        meta = self.meta()
        return {"ok": meta["status"] in {"complete", "imported"}, **meta, "session": str(self.path.resolve()),
                "comment_count": self.db.execute("SELECT count(*) FROM comments").fetchone()[0],
                "saved_pages": self.db.execute("SELECT count(*) FROM pages").fetchone()[0],
                "pending_pages": self.db.execute("SELECT count(*) FROM tasks WHERE done=0").fetchone()[0]}

    def mark(self, status: str, error: dict | None = None):
        meta = self.meta()
        meta.update(status=status, updated_at=now())
        meta.pop("last_error", None)
        if error:
            meta["last_error"] = error
        self.set_meta(meta)
        self.db.commit()

    def next_task(self) -> dict | None:
        row = self.db.execute("SELECT * FROM tasks WHERE done=0 ORDER BY CASE kind WHEN 'reply' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
        return dict(row) if row else None

    def records(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM comments ORDER BY rowid")]

    def commit_page(self, task: dict, payload: dict):
        data = payload.get("data")
        if not isinstance(data, dict) or "replies" not in data or not isinstance(data.get("page"), dict):
            raise ScoutError("response_changed", "分页结构异常；保留当前页供恢复，未标记完成。")
        items = data["replies"]
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ScoutError("response_changed", "分页评论不是列表。")
        page = data["page"]
        try:
            number, size, total = int(page["num"]), int(page["size"]), int(page["count"])
        except (ValueError, TypeError, KeyError):
            raise ScoutError("response_changed", "分页信息不完整。") from None
        if number != task["page"] or size <= 0 or total < 0:
            raise ScoutError("pagination_stalled", "接口返回的页码或计数异常。")
        if not items and total > (number - 1) * size:
            raise ScoutError("incomplete_page", "接口显示还有评论，却返回空页；未当作完整结束。")
        if items and total == 0:
            raise ScoutError("response_changed", "评论数量与分页总数矛盾。")
        meta = self.meta()
        stamp = now()
        normalized = [normalize(item, task["root_id"], meta["source"], stamp) for item in items]
        digest = hashlib.sha256(json.dumps(sorted(r["id"] for r in normalized)).encode()).hexdigest()
        if items and self.db.execute("SELECT 1 FROM pages WHERE kind=? AND root_id=? AND digest=?",
                                     (task["kind"], task["root_id"], digest)).fetchone():
            raise ScoutError("pagination_stalled", "接口重复返回已保存的一页，已暂停防止无限翻页。")
        # Pinned entries may be supplied separately and must not disappear from the collection.
        pinned = data.get("top_replies") or []
        if task["kind"] == "root":
            top = data.get("top")
            if isinstance(top, dict):
                pinned = list(pinned) + [v for v in top.values() if isinstance(v, dict) and v.get("rpid")]
            upper = data.get("upper")
            if isinstance(upper, dict) and isinstance(upper.get("top"), dict):
                pinned = list(pinned) + [upper["top"]]
            normalized += [normalize(item, "", meta["source"], stamp) for item in pinned]
        with self.db:
            self.db.execute("INSERT INTO pages(kind,root_id,page,fetched_at,digest,body) VALUES (?,?,?,?,?,?)",
                            (task["kind"], task["root_id"], number, stamp, digest, json.dumps(payload, ensure_ascii=False)))
            for record in normalized:
                self.db.execute("INSERT OR REPLACE INTO comments VALUES (?,?)",
                                (record["id"], json.dumps(record, ensure_ascii=False)))
                if task["kind"] == "root" and meta["include_replies"] and record["reply_count"] > 0:
                    self.db.execute("INSERT OR IGNORE INTO tasks(kind,root_id,page) VALUES ('reply',?,1)", (record["id"],))
            if number * size < total:
                self.db.execute("INSERT OR IGNORE INTO tasks(kind,root_id,page) VALUES (?,?,?)",
                                (task["kind"], task["root_id"], number + 1))
            self.db.execute("UPDATE tasks SET done=1 WHERE id=?", (task["id"],))
            meta.update(status="running", updated_at=stamp)
            self.set_meta(meta)


def crawl(session: Session, client, *, max_pages: int = 50, max_comments: int = 1000, progress=None) -> dict:
    """Per-invocation budgets; a page is committed atomically, then budgets are checked."""
    if max_pages < 1 or max_comments < 1:
        raise ScoutError("invalid_input", "本次页数和新增评论预算必须大于0。", exit_code=2)
    if session.meta()["status"] == "imported":
        raise ScoutError("invalid_input", "CSV导入会话不能作为网络抓取断点。", exit_code=2)
    before = session.status()["comment_count"]
    pages = 0
    try:
        while task := session.next_task():
            if pages >= max_pages or session.status()["comment_count"] - before >= max_comments:
                session.mark("budget_reached")
                return {**session.status(), "next_action": "resume_if_authorized"}
            payload = client.page(session.meta()["source"], task, session.meta()["sort"])
            try:
                session.commit_page(task, payload)
            except ScoutError as error:
                # Preserve rejected response evidence separately; never count it as a saved page.
                evidence = session.path / "rejected-pages" / (uuid.uuid4().hex + ".json")
                evidence.parent.mkdir(exist_ok=True)
                atomic_text(evidence, json.dumps({"task": task, "received_at": now(),
                                                 "error": error.result(), "response": payload},
                                                ensure_ascii=False, indent=2))
                error.details["diagnostic_file"] = str(evidence.resolve())
                raise
            pages += 1
            if progress:
                progress({"event": "page_saved", "pages_this_run": pages,
                          "comments": session.status()["comment_count"], "kind": task["kind"]})
        session.mark("complete")
        return session.status()
    except ScoutError as error:
        session.mark(error.code, error.result())
        raise
    except KeyboardInterrupt:
        session.mark("interrupted")
        raise
