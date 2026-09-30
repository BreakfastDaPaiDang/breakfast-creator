from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .access import BilibiliClient, NetworkLease, ScoutError, load_cookie, save_cookie
from .reading import export, import_csv
from .store import Session, crawl


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo unrecognized arguments, which could contain pasted credentials.
        raise ScoutError("invalid_input", "命令参数无效，请查看--help；凭据不支持命令行参数传入。", exit_code=2)


def parser() -> argparse.ArgumentParser:
    p = Parser(prog="comment-scout", description="Agent comment research: JSON status, no browser, resumable collection.")
    p.add_argument("--library", type=Path, default=Path("media-library/comments"))
    p.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = p.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth")
    auths = auth.add_subparsers(dest="auth_command", required=True)
    check = auths.add_parser("status", help="Offline credential presence check; --verify makes one login request")
    check.add_argument("--verify", action="store_true")
    configure = auths.add_parser("set", help="Store Cookie in .env; hidden terminal input or stdin")
    configure.add_argument("--stdin", action="store_true", help="Read Cookie through stdin, never argv")
    for name in ("fetch", "resume"):
        cmd = commands.add_parser(name)
        if name == "fetch":
            cmd.add_argument("target", help="BV / av / full video URL")
            cmd.add_argument("--sort", choices=["newest", "hot"], default="newest")
            cmd.add_argument("--roots-only", action="store_true")
        else:
            cmd.add_argument("session", type=Path)
        cmd.add_argument("--interval", type=float, default=3.0, help="Seconds between requests; 1–60, default 3")
        cmd.add_argument("--max-pages", type=int, default=50, help="Comment pages this invocation")
        cmd.add_argument("--max-comments", type=int, default=1000, help="New comments this invocation; checked at page boundaries")
    status = commands.add_parser("status", help="Read saved progress; no network")
    status.add_argument("session", type=Path)
    imp = commands.add_parser("import-csv", help="Read original extension CSV or old two-column output; no network")
    imp.add_argument("input", type=Path)
    imp.add_argument("--source-url", help="Original video URL, if known")
    imp.add_argument("--encoding", default="utf-8-sig")
    exp = commands.add_parser("export", help="Write full data and filtered Agent reading packs; no network")
    exp.add_argument("session", type=Path)
    exp.add_argument("--output", type=Path, help="New directory; existing directories are not overwritten")
    exp.add_argument("--mode", choices=["hot", "research"], default="research")
    exp.add_argument("--min-likes", type=int, default=5)
    exp.add_argument("--limit", type=int, default=200, help="Maximum primary comments selected by likes")
    exp.add_argument("--recent", type=int, default=50, help="Additional recent comments in research mode")
    exp.add_argument("--contains", action="append", default=[], help="Also select literal text matches in research mode")
    exp.add_argument("--pack-chars", type=int, default=16000)
    return p


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def emit(value, *, stream=None):
    print(json.dumps(value, ensure_ascii=False), file=stream or sys.stdout)


def execute(args) -> dict:
    if args.command == "auth":
        if args.auth_command == "set":
            if args.stdin:
                value = sys.stdin.read(65537)
                if len(value) > 65536:
                    raise ScoutError("invalid_input", "凭据输入过长。", exit_code=2)
            elif sys.stdin.isatty():
                value = getpass.getpass("Bilibili Cookie (hidden): ")
            else:
                raise ScoutError("needs_auth", "请向用户索取凭据并由Agent配置；使用auth status判断状态。", exit_code=2)
            save_cookie(args.env_file, value)
            return {"ok": True, "status": "credentials_saved", "verified": False}
        cookie = load_cookie(args.env_file)
        if not args.verify:
            return {"ok": True, "status": "credentials_present", "verified": False}
        with NetworkLease(args.library):
            return BilibiliClient(cookie, args.library).verify()
    if args.command == "import-csv":
        session = import_csv(args.input, args.library / "sessions" / run_id(), source_url=args.source_url, encoding=args.encoding)
        try:
            return session.status()
        finally:
            session.close()
    if args.command in {"status", "export"}:
        session = Session(args.session)
        try:
            if args.command == "status":
                return session.status()
            output = args.output or args.session / "exports" / run_id()
            return export(session, output, mode=args.mode, min_likes=args.min_likes, limit=args.limit,
                          recent=args.recent, contains=args.contains, pack_chars=args.pack_chars)
        finally:
            session.close()
    if args.max_pages < 1 or args.max_comments < 1:
        raise ScoutError("invalid_input", "抓取预算必须大于0。", exit_code=2)
    cookie = load_cookie(args.env_file)
    with NetworkLease(args.library):
        client = BilibiliClient(cookie, args.library, args.interval)
        session = Session(args.session) if args.command == "resume" else None
        try:
            if session and session.meta()["status"] == "imported":
                raise ScoutError("invalid_input", "CSV导入会话不能网络续抓。", exit_code=2)
            if session and not session.next_task():
                return session.status()
            client.verify()
            if session is None:
                source = client.resolve(args.target)
                session = Session(args.library / "sessions" / run_id(), source=source, sort=args.sort, replies=not args.roots_only)
            result = crawl(session, client, max_pages=args.max_pages, max_comments=args.max_comments,
                           progress=lambda value: emit(value, stream=sys.stderr))
            return {**result, "requests_this_run": client.request_count}
        except ScoutError as error:
            if session:
                session.mark(error.code, error.result())
                return {**session.status(), **error.result(), "exit_code": error.exit_code,
                        "requests_this_run": client.request_count}
            error.details.update(requests_this_run=client.request_count)
            raise
        except KeyboardInterrupt:
            if session:
                session.mark("interrupted")
                return {**session.status(), "exit_code": 130, "next_action": "resume_if_authorized"}
            raise
        finally:
            if session:
                session.close()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        args = parser().parse_args(argv)
        result = execute(args)
        emit(result)
        if "exit_code" in result:
            return result["exit_code"]
        return 0 if result.get("ok") else 3
    except ScoutError as error:
        emit(error.result())
        return error.exit_code
    except FileExistsError:
        emit({"ok": False, "status": "output_exists", "message": "输出目录已存在，请选用新目录。"})
        return 2
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # No traceback: may contain cookie input, SQL data, or user comments.
        emit({"ok": False, "status": "local_error", "message": "本地文件或状态处理失败；请检查路径和会话格式。"})
        return 4
    except KeyboardInterrupt:
        emit({"ok": False, "status": "interrupted"})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
