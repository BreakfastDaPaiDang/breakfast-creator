from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import Catalog
from .health import HealthStatus, diagnose_sources, format_report, report_json
from .models import RightsStatus
from .service import MaterialScout, load_candidates, select_candidates
from .watermarks import (
    MARK_AUTHORIZATIONS,
    MARK_KINDS,
    MARK_TREATMENTS,
    decide_mark_treatment,
)

DEFAULT_LIBRARY = Path("media-library")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="material-scout",
        description="Discover, acquire, and catalog reusable content assets.",
    )
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check source adapters and media dependencies")
    doctor.add_argument("--network", action="store_true", help="Run one live query per source")
    doctor.add_argument("--json", action="store_true", help="Emit a machine-readable report")

    search = subparsers.add_parser("search", help="Search normalized source adapters")
    search.add_argument("--query", action="append", required=True, help="Repeat for multiple queries")
    search.add_argument(
        "--source",
        action="append",
        choices=["youtube", "bilibili"],
        default=None,
        help="Repeat to choose sources; defaults to both",
    )
    search.add_argument("--limit", type=int, default=10, help="Results per query per source")
    search.add_argument("--output", type=Path)

    acquire = subparsers.add_parser("acquire", help="Acquire selected candidates and provenance")
    acquire.add_argument("--session", type=Path, required=True, help="Path to candidates.json")
    acquire.add_argument("--id", action="append", required=True, help="Candidate ID, remote ID, or #number")
    acquire.add_argument("--purpose", choices=["research", "production"], default="research")
    acquire.add_argument("--media", choices=["none", "proxy", "master"], default="proxy")
    transcript = subparsers.add_parser("transcript", help="Fetch Bilibili platform captions using local credentials; JSON output")
    transcript.add_argument("target")
    transcript.add_argument("--output", type=Path, required=True)
    transcript.add_argument("--part", type=int, default=1)
    transcript.add_argument("--env-file", type=Path, default=Path(".env"))
    acquire.add_argument(
        "--rights-status",
        choices=[status.value for status in RightsStatus],
        default=RightsStatus.UNKNOWN.value,
    )

    register = subparsers.add_parser("register", help="Register any local content asset")
    register.add_argument("path", type=Path)
    register.add_argument("--title")
    register.add_argument(
        "--rights-status",
        choices=[status.value for status in RightsStatus],
        required=True,
    )

    listing = subparsers.add_parser("list", help="List cataloged assets")
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--json", action="store_true")

    mark = subparsers.add_parser("mark", help="Record and gate visible-mark treatment")
    mark_subparsers = mark.add_subparsers(dest="mark_command", required=True)
    mark_record = mark_subparsers.add_parser("record", help="Record a visible-mark assessment")
    mark_record.add_argument("--asset-id", required=True)
    mark_record.add_argument("--status", choices=["unassessed", "absent", "present"], required=True)
    mark_record.add_argument("--kind", choices=sorted(MARK_KINDS), default="unknown")
    mark_record.add_argument("--authorization", choices=sorted(MARK_AUTHORIZATIONS), default="unknown")
    mark_record.add_argument("--notes")
    mark_check = mark_subparsers.add_parser("check", help="Check whether a treatment is allowed")
    mark_check.add_argument("--asset-id", required=True)
    mark_check.add_argument("--treatment", choices=sorted(MARK_TREATMENTS), required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "transcript":
        from comment_scout.access import ScoutError
        from .transcript import acquire_transcript
        try:
            result = acquire_transcript(args.target, args.output, env_file=args.env_file,
                                        library=args.library / "comments", part=args.part)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except ScoutError as error:
            print(json.dumps(error.result(), ensure_ascii=False))
            return error.exit_code
        except (OSError, ValueError, TypeError, KeyError):
            print(json.dumps({"ok": False, "status": "local_error", "message": "字幕处理失败，请检查输出目录与输入结构。"}, ensure_ascii=False))
            return 4
    scout = MaterialScout(args.library)
    try:
        if args.command == "doctor":
            report = diagnose_sources(network=args.network)
            print(report_json(report) if args.json else format_report(report))
            return 2 if report.status is HealthStatus.ERROR else 0

        if args.command == "search":
            sources = args.source or ["youtube", "bilibili"]
            session = scout.search(args.query, sources, args.limit, args.output)
            print(f"Candidates: {len(session.candidates)}")
            print(f"Session: {session.output_dir}")
            for sheet in session.contact_sheets:
                print(f"Contact sheet: {sheet}")
            for warning in session.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            return 0 if session.candidates else 2

        if args.command == "acquire":
            candidates = load_candidates(args.session)
            selected = select_candidates(candidates, args.id)
            for candidate in selected:
                asset = scout.acquire(
                    candidate,
                    RightsStatus(args.rights_status),
                    purpose=args.purpose,
                    media=args.media,
                )
                print(f"Acquired: {asset.asset_id} ({len(asset.representations)} representations)")
            return 0

        if args.command == "register":
            asset = scout.register_local(
                args.path,
                RightsStatus(args.rights_status),
                title=args.title,
            )
            print(json.dumps(asset.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "list":
            assets = scout.list_assets(args.limit)
            if args.json:
                print(json.dumps(assets, ensure_ascii=False, indent=2))
            else:
                for asset in assets:
                    print(f"{asset['asset_id']}\t{asset['kind']}\t{asset['rights_status']}\t{asset['title']}")
            return 0

        if args.command == "mark":
            return _handle_mark(args, scout)
    except (KeyError, PermissionError, RuntimeError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 1


def _handle_mark(args: argparse.Namespace, scout: MaterialScout) -> int:
    with Catalog(scout.catalog_path) as catalog:
        asset = catalog.get_asset(args.asset_id)
        if not asset:
            raise KeyError(f"Unknown asset: {args.asset_id}")
        if args.mark_command == "record":
            mark_id = catalog.record_mark(
                args.asset_id,
                args.status,
                args.kind,
                args.authorization,
                args.notes,
            )
            print(f"Recorded visible mark assessment: {mark_id}")
            return 0
        mark = catalog.latest_mark(args.asset_id)
        authorization = str(mark["authorization"]) if mark else "unknown"
        decision = decide_mark_treatment(
            str(asset["rights_status"]), authorization, args.treatment
        )
        print(json.dumps({"allowed": decision.allowed, "reason": decision.reason}, ensure_ascii=False))
        return 0 if decision.allowed else 3


if __name__ == "__main__":
    raise SystemExit(main())
