from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from .providers import SearchAdapter, default_adapters, run_command, yt_dlp_command


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass(slots=True)
class HealthCheck:
    name: str
    status: HealthStatus
    detail: str
    remediation: str | None = None


@dataclass(slots=True)
class HealthReport:
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def status(self) -> HealthStatus:
        statuses = {check.status for check in self.checks}
        if HealthStatus.ERROR in statuses:
            return HealthStatus.ERROR
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.OK

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "checks": [asdict(check) for check in self.checks]}


Locator = Callable[[str], str | None]


def diagnose_sources(
    network: bool = False,
    runner=run_command,
    locator: Locator | None = None,
    adapters: dict[str, SearchAdapter] | None = None,
) -> HealthReport:
    locate = locator or find_executable
    checks: list[HealthCheck] = []

    yt_result = runner([*yt_dlp_command(), "--version"])
    if yt_result.returncode == 0:
        version = yt_result.stdout.strip().splitlines()[0]
        checks.append(HealthCheck("yt-dlp", HealthStatus.OK, f"project package {version}"))
    else:
        checks.append(
            HealthCheck(
                "yt-dlp",
                HealthStatus.ERROR,
                _detail(yt_result),
                "Run `uv sync` to restore the locked project dependency.",
            )
        )

    try:
        ejs_version = importlib.metadata.version("yt-dlp-ejs")
        checks.append(HealthCheck("youtube-ejs", HealthStatus.OK, f"yt-dlp-ejs {ejs_version}"))
    except importlib.metadata.PackageNotFoundError:
        checks.append(
            HealthCheck(
                "youtube-ejs",
                HealthStatus.ERROR,
                "yt-dlp-ejs is not installed",
                "Run `uv sync`; it is included by yt-dlp[default].",
            )
        )

    deno = locate("deno")
    node = locate("node")
    if deno:
        version = runner([deno, "--version"])
        version_text = version.stdout.strip().splitlines()[0] if version.stdout.strip() else version.stderr.strip()
        runtime_ok = version.returncode == 0 and _version_at_least(version_text, (2, 3))
        checks.append(
            HealthCheck(
                "youtube-js-runtime",
                HealthStatus.OK if runtime_ok else HealthStatus.ERROR,
                version_text,
                None if runtime_ok else "Upgrade to Deno 2.3+.",
            )
        )
    elif node:
        version = runner([node, "--version"])
        version_text = version.stdout.strip() or version.stderr.strip()
        runtime_ok = version.returncode == 0 and _version_at_least(version_text, (22, 0))
        checks.append(
            HealthCheck(
                "youtube-js-runtime",
                HealthStatus.OK if runtime_ok else HealthStatus.ERROR,
                f"Node {version_text}",
                None if runtime_ok else "Upgrade to Node 22+.",
            )
        )
    else:
        checks.append(
            HealthCheck(
                "youtube-js-runtime",
                HealthStatus.ERROR,
                "Neither Deno nor Node is available",
                "Install Deno 2.3+ (preferred) or Node 22+ for YouTube challenges.",
            )
        )

    ffmpeg = locate("ffmpeg")
    checks.append(
        HealthCheck(
            "ffmpeg",
            HealthStatus.OK if ffmpeg else HealthStatus.ERROR,
            ffmpeg or "not found",
            None if ffmpeg else "Install FFmpeg and start a new terminal.",
        )
    )

    bili = locate("bili")
    checks.append(
        HealthCheck(
            "bilibili-search-primary",
            HealthStatus.OK if bili else HealthStatus.DEGRADED,
            bili or "bilibili-cli missing; yt-dlp fallback remains available",
            None if bili else "Run `uv tool install bilibili-cli`.",
        )
    )
    yutto = locate("yutto")
    checks.append(
        HealthCheck(
            "bilibili-download-primary",
            HealthStatus.OK if yutto else HealthStatus.DEGRADED,
            yutto or "yutto missing; yt-dlp fallback remains available",
            None if yutto else "Run `uv tool install yutto`.",
        )
    )

    checks.append(
        HealthCheck(
            "youtube-po-token",
            HealthStatus.DEGRADED,
            "No managed PO Token provider is configured; normal downloads work until YouTube requires one",
            "Add a trusted provider only after a 403/PO-token failure; do not paste per-video tokens into manifests.",
        )
    )

    if network:
        source_adapters = adapters or default_adapters(runner)
        probes = {"youtube": "OpenAI", "bilibili": "人工智能"}
        for source, query in probes.items():
            try:
                result = source_adapters[source].search(query, 1)
                if result.candidates:
                    detail = f"returned {len(result.candidates)} candidate via {result.adapter or 'adapter'}"
                    status = HealthStatus.DEGRADED if result.warnings else HealthStatus.OK
                    if result.warnings:
                        detail += f"; {'; '.join(result.warnings)}"
                    checks.append(HealthCheck(f"{source}-network", status, detail))
                else:
                    checks.append(HealthCheck(f"{source}-network", HealthStatus.DEGRADED, "request succeeded but returned no candidates"))
            except (OSError, RuntimeError, ValueError) as error:
                checks.append(HealthCheck(f"{source}-network", HealthStatus.ERROR, str(error)))
    return HealthReport(checks)


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name != "nt":
        return None
    suffixes = (".exe", ".cmd", ".bat", "")
    path_values = [os.environ.get("PATH", "")]
    try:
        import winreg

        for hive, key_path in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                path_values.append(os.path.expandvars(str(value)))
    except (ImportError, OSError):
        pass
    for directory in os.pathsep.join(path_values).split(os.pathsep):
        directory = directory.strip().strip('"')
        if not directory:
            continue
        for suffix in suffixes:
            candidate = Path(directory) / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None


def format_report(report: HealthReport) -> str:
    lines = [f"Material Scout: {report.status.value.upper()}"]
    for check in report.checks:
        lines.append(f"{check.status.value.upper():8} {check.name}: {check.detail}")
        if check.remediation:
            lines.append(f"         -> {check.remediation}")
    return "\n".join(lines)


def report_json(report: HealthReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "command failed"


def _version_at_least(value: str, minimum: tuple[int, int]) -> bool:
    match = re.search(r"v?(\d+)\.(\d+)", value)
    return bool(match and (int(match.group(1)), int(match.group(2))) >= minimum)
