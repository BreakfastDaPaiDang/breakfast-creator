from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
from pathlib import Path

from .access import Client, NetworkLease, ScoutError, ZhihuError, load_cookie, save_cookie
from .jobs import Session, crawl, create_session, run_id
from .model import target
from .reading import export


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ZhihuError("invalid_input", "参数无效，请查看--help；不要把凭据放入命令参数。", exit_code=2)


def parser():
    p = Parser(prog="zhihu-scout", description="知乎问题搜索、回答与评论采集；JSON状态，无浏览器依赖")
    p.add_argument("--library", type=Path, default=Path("media-library/zhihu"))
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    cmds = p.add_subparsers(dest="command", required=True)
    auth = cmds.add_parser("auth").add_subparsers(dest="action", required=True)
    auth.add_parser("status").add_argument("--verify", action="store_true")
    auth.add_parser("set").add_argument("--stdin", action="store_true")
    for name in ("search", "inspect", "fetch", "resume"):
        cmd = cmds.add_parser(name)
        cmd.add_argument("--interval", type=float, default=3.0)
        cmd.add_argument("--max-pages", type=int, default=20, help="本次调用的内容请求上限，登录验证另计")
        if name == "search":
            cmd.add_argument("query")
            cmd.add_argument("--search-pages", type=int, default=2)
        elif name == "inspect":
            cmd.add_argument("targets", nargs="+", help="最多10个问题ID或链接，只取问题数据")
        elif name == "fetch":
            cmd.add_argument("target")
            cmd.add_argument("--kind", choices=["question", "answer"], default="question", help="纯数字ID类型，URL自动识别")
            cmd.add_argument("--min-votes", type=int, default=100)
            cmd.add_argument("--top-answers", type=int, default=5)
            cmd.add_argument("--answer-pages", type=int, default=3)
            cmd.add_argument("--comment-pages", type=int, default=3)
            cmd.add_argument("--reply-pages", type=int, default=2)
            cmd.add_argument("--roots-only", action="store_true")
        else:
            cmd.add_argument("session", type=Path)
    cmds.add_parser("status").add_argument("session", type=Path)
    exp = cmds.add_parser("export")
    exp.add_argument("session", type=Path)
    exp.add_argument("--output", type=Path)
    exp.add_argument("--min-likes", type=int, default=5)
    exp.add_argument("--limit", type=int, default=200)
    exp.add_argument("--comments-per-source", type=int, default=10, help="每篇回答／问题评论区的总条数上限，含上下文")
    exp.add_argument("--pack-chars", type=int, default=16000)
    return p


def emit(value, stream=None):
    print(json.dumps(value, ensure_ascii=False), file=stream or sys.stdout, flush=True)


def execute(args):
    if args.command == "auth":
        if args.action == "set":
            if args.stdin:
                value = sys.stdin.read(65537)
            elif sys.stdin.isatty():
                value = getpass.getpass("Zhihu Cookie (hidden): ")
            else:
                raise ZhihuError("needs_auth", "请向用户索取Cookie，由Agent完成配置。", exit_code=2)
            save_cookie(args.env_file, value)
            return {"ok": True, "status": "credentials_saved", "verified": False}
        cookie = load_cookie(args.env_file)
        if not args.verify:
            return {"ok": True, "status": "credentials_present", "verified": False}
        with NetworkLease(args.library):
            return Client(cookie, args.library).verify()
    if args.command in {"status", "export"}:
        session = Session(args.session)
        try:
            if args.command == "status":
                return session.status()
            return export(session, args.output or args.session / "exports" / run_id(),
                          args.min_likes, args.limit, args.pack_chars, args.comments_per_source)
        finally:
            session.close()
    for key in ("max_pages", "search_pages", "top_answers", "answer_pages", "comment_pages", "reply_pages"):
        if hasattr(args, key) and getattr(args, key) < 1:
            raise ZhihuError("invalid_input", "页数与数量预算须大于0。", exit_code=2)
    if getattr(args, "min_votes", 0) < 0 or not 1 <= args.interval <= 60:
        raise ZhihuError("invalid_input", "最低赞数须≥0，请求间隔须为1—60秒。", exit_code=2)
    config = None
    if args.command == "search":
        if not args.query.strip():
            raise ZhihuError("invalid_input", "搜索词不能为空。", exit_code=2)
        config = {"mode": "search", "query": args.query, "search_pages": args.search_pages}
    elif args.command == "inspect":
        parsed = [target(value) for value in args.targets]
        if len(parsed) > 10 or any(kind != "question" for kind, _ in parsed):
            raise ZhihuError("invalid_input", "inspect一次接受1—10个问题，不接受回答链接。", exit_code=2)
        config = {"mode": "inspect", "question_ids": list(dict.fromkeys(rid for _, rid in parsed))}
    elif args.command == "fetch":
        kind, rid = target(args.target, args.kind)
        config = {"mode": "fetch", "target_kind": kind, "target_id": rid, "min_votes": args.min_votes,
                  "top_answers": args.top_answers, "answer_pages": args.answer_pages,
                  "comment_pages": args.comment_pages, "reply_pages": args.reply_pages, "replies": not args.roots_only}
    cookie = load_cookie(args.env_file)
    with NetworkLease(args.library):
        client = Client(cookie, args.library, args.interval)
        session = Session(args.session) if args.command == "resume" else create_session(args.library, config)
        try:
            if not session.next_task():
                return session.status()
            try:
                client.verify()
            except ZhihuError as error:
                session.mark(error.code, error.result())
                return {**session.status(), "requests_this_run": client.request_count}
            return crawl(session, client, args.max_pages, lambda row: emit(row, sys.stderr))
        finally:
            session.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result = execute(parser().parse_args())
        emit(result)
        if result["ok"]:
            return 0
        return {"needs_auth": 2, "auth_expired": 2, "budget_reached": 3,
                "access_restricted": 3, "rate_limited": 3, "interrupted": 130}.get(result["status"], 4)
    except ScoutError as error:
        emit(error.result())
        return error.exit_code
    except KeyboardInterrupt:
        emit({"ok": False, "status": "interrupted"})
        return 130
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        emit({"ok": False, "status": "local_error", "message": "本地文件或数据结构异常；未输出异常原文以免泄露凭据。"})
        return 4


if __name__ == "__main__":
    sys.exit(main())
