from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .config import Config, ConfigError
from .server import Server


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="breakfast-assistant")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="本地检查必填项、范例和凭据，不联网")
    server = sub.add_parser("serve", help="启动本地配置页")
    server.add_argument("--port", type=int, default=8765)
    profile = sub.add_parser("profile-set", help="Agent从stdin保存用户确认的信息")
    profile.add_argument("--revision", required=True, help="status返回的revision，避免覆盖并发修改")
    args = parser.parse_args()
    config = Config(args.root)
    try:
        if args.command == "serve":
            config.initialize()
            with Server(config, args.port) as http:
                print(json.dumps({"ok": True, "url": http.origin}, ensure_ascii=False), flush=True)
                http.serve_forever()
        elif args.command == "profile-set":
            print(json.dumps(config.save_profile(sys.stdin.read(30001), args.revision), ensure_ascii=False))
        else:
            print(json.dumps(config.state(), ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        return 0
    except ConfigError as error:
        print(json.dumps({"ok": False, "message": error.message}, ensure_ascii=False))
        return 1
    except (OSError, sqlite3.Error):
        print(json.dumps({"ok": False, "message": "本地文件或端口不可用。"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
