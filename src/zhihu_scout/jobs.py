from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .access import ZhihuError, atomic_text, endpoint_url
from .model import next_url, normalize, search_questions


def now():
    return datetime.now(timezone.utc).isoformat()


def run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]


def task(kind, url, pages=1, **context):
    return {"kind": kind, "url": url, "remaining": pages, **context}


def comments(kind, rid, source, options):
    return task("comments", endpoint_url(f"/api/v4/comment_v5/{kind}s/{rid}/root_comment",
                {"order_by": "score", "limit": 20}), options["comment_pages"], source_url=source)


class Session:
    def __init__(self, path: Path, config=None, tasks=()):
        self.path = path
        if config is not None:
            path.mkdir(parents=True, exist_ok=False)
        elif not (path / "session.sqlite3").is_file():
            raise ZhihuError("invalid_input", "未找到知乎会话。", exit_code=2)
        self.db = sqlite3.connect(path / "session.sqlite3")
        if config is not None:
            self.db.executescript("""
                CREATE TABLE meta(body TEXT NOT NULL);
                CREATE TABLE records(kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id));
                CREATE TABLE tasks(id INTEGER PRIMARY KEY, url TEXT UNIQUE, body TEXT, done INTEGER DEFAULT 0);
                CREATE TABLE pages(id INTEGER PRIMARY KEY, url TEXT, fetched_at TEXT, body TEXT);
            """)
            self.db.execute("INSERT INTO meta VALUES (?)", (json.dumps({
                "schema_version": 1, "config": config, "created_at": now(), "status": "pending",
                "limited_streams": [], "warnings": [], "selected_answer_ids": [], "skipped_search_items": 0,
            }),))
            for t in tasks:
                self.add(t)
            self.db.commit()

    def close(self):
        self.db.close()

    def meta(self):
        value = json.loads(self.db.execute("SELECT body FROM meta").fetchone()[0])
        if value.get("schema_version") != 1:
            raise ZhihuError("state_error", "会话版本不受支持。")
        return value

    def save_meta(self, value):
        self.db.execute("UPDATE meta SET body=?", (json.dumps(value, ensure_ascii=False),))

    def add(self, value):
        self.db.execute("INSERT INTO tasks(url,body) VALUES (?,?)", (value["url"], json.dumps(value)))

    def next_task(self):
        rows = self.db.execute("SELECT id,body FROM tasks WHERE done=0 ORDER BY id").fetchall()
        priorities = {"question": 0, "search": 1, "answers": 1, "select": 2, "answer": 3, "comments": 4, "child": 5}
        if not rows:
            return None
        values = [{**json.loads(body), "task_id": rid} for rid, body in rows]
        return min(values, key=lambda t: (priorities[t["kind"]], t["task_id"]))

    def records(self, kind=None):
        rows = self.db.execute("SELECT body FROM records" + (" WHERE kind=?" if kind else ""), (kind,) if kind else ())
        return [json.loads(row[0]) for row in rows]

    def status(self):
        return {"ok": self.meta()["status"] == "scope_complete", **self.meta(),
                "candidates": [{k: r.get(k) for k in ("id", "title", "source_url", "answer_count",
                    "visit_count", "follower_count", "comment_count", "created_time", "updated_time",
                    "metrics_source", "fetched_at")} for r in self.records("question")],
                "session": str(self.path.resolve()),
                "counts": dict(self.db.execute("SELECT kind,count(*) FROM records GROUP BY kind")),
                "saved_pages": self.db.execute("SELECT count(*) FROM pages").fetchone()[0],
                "pending_tasks": self.db.execute("SELECT count(*) FROM tasks WHERE done=0").fetchone()[0]}

    def mark(self, status, error=None):
        meta = self.meta()
        meta.update(status=status, updated_at=now())
        meta.pop("last_error", None)
        if error:
            meta["last_error"] = error
        self.save_meta(meta)
        self.db.commit()

    def commit(self, current, payload):
        meta = self.meta()
        options = meta["config"]
        kind = current["kind"]
        additions, records = [], []
        following = None
        if kind == "select":
            ranked = sorted(self.records("answer"), key=lambda r: (-r["likes"], r["id"]))
            selected = [r for r in ranked if r["likes"] >= options["min_votes"]][:options["top_answers"]]
            meta["selected_answer_ids"] = [r["id"] for r in selected]
            for answer in selected:
                if answer["text_status"] != "content":
                    additions.append(task("answer", endpoint_url(f"/api/v4/answers/{answer['id']}",
                                          {"include": "content,voteup_count,comment_count,question,author"})))
                additions.append(comments("answer", answer["id"], answer["source_url"], options))
        elif kind in {"question", "answer"}:
            record = normalize(payload, kind)
            records.append(record)
            if kind == "answer" and options.get("target_kind") == "answer":
                meta["selected_answer_ids"] = [record["id"]]
                additions.append(comments("answer", record["id"], record["source_url"], options))
        else:
            items = payload.get("data")
            if not isinstance(items, list):
                raise ZhihuError("response_changed", "分页data不是列表。")
            following = None
            fallback = False
            try:
                following = next_url(payload, current["url"])
            except ZhihuError as error:
                u = urlsplit(current["url"])
                params = dict(parse_qsl(u.query, keep_blank_values=True))
                if (error.code == "incomplete_page" and kind == "comments" and not items
                        and params.get("order_by") == "score" and not params.get("offset")):
                    params["order_by"] = "time"
                    additions.append({**current, "url": urlunsplit(u._replace(query=urlencode(params)))})
                    meta["warnings"].append({"code": "score_empty_fallback_time", "source_url": current["source_url"]})
                    fallback = True
                else:
                    raise
            if kind == "search":
                records, skipped = search_questions(items)
                meta["skipped_search_items"] += skipped
            elif kind == "answers":
                records = [normalize(x, "answer", question_id=options["target_id"]) for x in items]
            else:
                records = [normalize(x, "comment", source_url=current["source_url"], root_id=current.get("root_id")) for x in items]
                if kind == "comments" and options["replies"]:
                    for r in records:
                        if r["reply_count"]:
                            additions.append(task("child", endpoint_url(f"/api/v4/comment_v5/comment/{r['id']}/child_comment",
                                                  {"order_by": "time", "limit": 20}), options["reply_pages"],
                                                  source_url=current["source_url"], root_id=r["id"]))
            if following and not fallback:
                if current["remaining"] > 1:
                    additions.append({**current, "url": following, "remaining": current["remaining"] - 1})
                else:
                    meta["limited_streams"].append({"kind": kind, "source_url": current.get("source_url"), "next_url": following})
            if records and kind != "search":
                known = {(r["kind"], r["id"]) for r in self.records()}
                if all((r["kind"], r["id"]) in known for r in records):
                    raise ZhihuError("pagination_stalled", "整页都是已取得的记录，暂停以检查分页。")
        try:
            with self.db:
                for addition in additions:
                    exists = self.db.execute("SELECT done FROM tasks WHERE url=?", (addition["url"],)).fetchone()
                    if exists:
                        if addition["kind"] in {"search", "answers", "comments", "child"} and addition["url"] == following:
                            raise ZhihuError("pagination_stalled", "下一页游标循环，未提交当前页。")
                        continue
                    self.add(addition)
                for record in records:
                    record["fetched_at"] = now()
                    self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                                    (record["kind"], record["id"], json.dumps(record, ensure_ascii=False)))
                if payload is not None:
                    self.db.execute("INSERT INTO pages(url,fetched_at,body) VALUES (?,?,?)",
                                    (current["url"], now(), json.dumps(payload, ensure_ascii=False)))
                self.db.execute("UPDATE tasks SET done=1 WHERE id=?", (current["task_id"],))
                self.save_meta(meta)
        except sqlite3.IntegrityError:
            raise ZhihuError("state_error", "任务提交冲突，原进度保留。") from None


def create_session(library, config):
    if config["mode"] == "inspect":
        tasks = [task("question", endpoint_url(f"/api/v4/questions/{rid}", {
            "include": "title,answer_count,visit_count,follower_count,comment_count,created,updated_time",
        })) for rid in config["question_ids"]]
    elif config["mode"] == "search":
        tasks = [task("search", endpoint_url("/api/v4/search_v3", {
            "t": "general", "q": config["query"], "correction": 1, "offset": 0, "limit": 20,
            "filter_fields": "", "lc_idx": 0, "show_all_topics": 0, "search_source": "Filter",
            "sort": "upvoted_count", "vertical": "", "time_interval": "",
        }), config["search_pages"])]
    else:
        rid, kind = config["target_id"], config["target_kind"]
        tasks = [task(kind, endpoint_url(f"/api/v4/{kind}s/{rid}",
                     {"include": "title,detail,content,voteup_count,comment_count,question,author,answer_count,visit_count,follower_count,created,updated_time"}))]
        if kind == "question":
            tasks.extend([
                task("answers", endpoint_url(f"/api/v4/questions/{rid}/answers", {
                    "include": "content,excerpt,voteup_count,comment_count,question,author",
                    "limit": 20, "offset": 0, "sort_by": "default", "platform": "desktop",
                }), config["answer_pages"]), task("select", "local:select"),
                comments("question", rid, f"https://www.zhihu.com/question/{rid}", config),
            ])
    return Session(library / "sessions" / run_id(), config, tasks)


def crawl(session, client, max_pages, progress=None):
    pages = 0
    try:
        while current := session.next_task():
            if current["kind"] != "select" and pages >= max_pages:
                session.mark("budget_reached")
                break
            payload = client.get(current["url"]) if current["kind"] != "select" else None
            try:
                session.commit(current, payload)
            except ZhihuError:
                if payload is not None:
                    folder = session.path / "rejected-pages"
                    folder.mkdir(exist_ok=True)
                    atomic_text(folder / (run_id() + ".json"), json.dumps({"url": current["url"], "payload": payload}, ensure_ascii=False))
                raise
            if payload is not None:
                pages += 1
                if progress:
                    progress({"event": "page_saved", "kind": current["kind"], "pages_this_run": pages})
        else:
            session.mark("scope_complete")
    except ZhihuError as error:
        session.mark(error.code, error.result())
    except KeyboardInterrupt:
        session.mark("interrupted")
    return {**session.status(), "requests_this_run": client.request_count, "pages_this_run": pages}
