from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .models import AssetCandidate, AssetKind


@dataclass(slots=True)
class ProviderSearchResult:
    candidates: list[AssetCandidate]
    warnings: list[str] = field(default_factory=list)
    adapter: str | None = None

    def __iter__(self):
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    def __getitem__(self, index: int) -> AssetCandidate:
        return self.candidates[index]


class SearchAdapter(Protocol):
    source: str

    def search(self, query: str, limit: int) -> ProviderSearchResult: ...


@dataclass(slots=True)
class AcquisitionResult:
    adapter: str
    warnings: list[str] = field(default_factory=list)


class AcquisitionAdapter(Protocol):
    source: str

    def acquire(self, source_url: str, output_dir: Path, media: str) -> AcquisitionResult: ...


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]
MetadataFetcher = Callable[[str], dict | None]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    environment = _command_environment()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def _command_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if os.name != "nt":
        return environment
    persisted_paths: list[str] = []
    try:
        import winreg

        for hive, key_path in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                persisted_paths.append(os.path.expandvars(str(value)))
    except (ImportError, OSError):
        pass
    if persisted_paths:
        environment["PATH"] = os.pathsep.join([environment.get("PATH", ""), *persisted_paths])
    return environment


def yt_dlp_command() -> list[str]:
    """Use the project-locked package instead of a possibly stale global executable."""
    return [sys.executable, "-m", "yt_dlp"]


def youtube_js_args() -> list[str]:
    path = _command_environment().get("PATH")
    runtime = "deno" if shutil.which("deno", path=path) else "node"
    return ["--js-runtimes", runtime]


@dataclass(slots=True)
class YtDlpSearchAdapter:
    source: str
    search_prefix: str
    runner: CommandRunner = run_command
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0
    sleeper: Sleeper = time.sleep
    executable: list[str] = field(default_factory=yt_dlp_command)

    def search(self, query: str, limit: int) -> ProviderSearchResult:
        target = f"{self.search_prefix}{limit}:{query}"
        command = [
            *self.executable,
            "--ignore-config",
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
        ]
        if self.source == "youtube":
            command.extend(youtube_js_args())
        command.append(target)
        result = _run_with_retry(
            command,
            self.source,
            "search",
            self.runner,
            self.max_attempts,
            self.retry_delay_seconds,
            self.sleeper,
        )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{self.source} returned invalid JSON") from error

        entries = payload.get("entries") or [payload]
        candidates: list[AssetCandidate] = []
        for entry in entries:
            if not entry or not entry.get("id"):
                continue
            remote_id = str(entry["id"])
            candidates.append(
                AssetCandidate(
                    candidate_id=f"{self.source}:{remote_id}",
                    kind=AssetKind.VIDEO,
                    source=self.source,
                    remote_id=remote_id,
                    title=entry.get("title") or remote_id,
                    source_url=self._source_url(entry, remote_id),
                    search_query=query,
                    creator=entry.get("uploader") or entry.get("channel"),
                    duration_seconds=_as_float(entry.get("duration")),
                    published_at=entry.get("upload_date") or entry.get("release_date"),
                    thumbnail_url=entry.get("thumbnail") or self._default_thumbnail(remote_id),
                    description=entry.get("description"),
                    metadata={
                        "view_count": entry.get("view_count"),
                        "channel_id": entry.get("channel_id"),
                        "search_adapter": "yt-dlp",
                    },
                )
            )
        return ProviderSearchResult(candidates, adapter="yt-dlp")

    def _source_url(self, entry: dict, remote_id: str) -> str:
        webpage_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
        if webpage_url and str(webpage_url).startswith(("http://", "https://")):
            return str(webpage_url)
        if self.source == "youtube":
            return f"https://www.youtube.com/watch?v={remote_id}"
        if self.source == "bilibili":
            return f"https://www.bilibili.com/video/{remote_id}"
        return str(entry.get("url") or remote_id)

    def _default_thumbnail(self, remote_id: str) -> str | None:
        if self.source == "youtube":
            return f"https://i.ytimg.com/vi/{remote_id}/hqdefault.jpg"
        return None


@dataclass(slots=True)
class BilibiliSearchAdapter(YtDlpSearchAdapter):
    metadata_fetcher: MetadataFetcher = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata_fetcher is None:
            self.metadata_fetcher = fetch_bilibili_metadata

    def search(self, query: str, limit: int) -> ProviderSearchResult:
        result = super(BilibiliSearchAdapter, self).search(query, limit)
        warnings = _enrich_bilibili_candidates(result.candidates, self.metadata_fetcher)
        result.warnings.extend(warnings)
        return result


@dataclass(slots=True)
class BilibiliCliSearchAdapter:
    source: str = "bilibili"
    runner: CommandRunner = run_command
    metadata_fetcher: MetadataFetcher = None  # type: ignore[assignment]
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0
    sleeper: Sleeper = time.sleep
    executable: list[str] = field(default_factory=lambda: ["bili"])

    def __post_init__(self) -> None:
        if self.metadata_fetcher is None:
            self.metadata_fetcher = fetch_bilibili_metadata

    def search(self, query: str, limit: int) -> ProviderSearchResult:
        command = [*self.executable, "search", query, "--type", "video", "--max", str(limit), "--json"]
        result = _run_with_retry(
            command,
            self.source,
            "search via bilibili-cli",
            self.runner,
            self.max_attempts,
            self.retry_delay_seconds,
            self.sleeper,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("bilibili-cli returned invalid JSON") from error
        if payload.get("ok") is False:
            error = payload.get("error") or {}
            raise RuntimeError(f"bilibili-cli search failed: {error.get('message') or error}")
        data = payload.get("data") or []
        entries = data.get("items", []) if isinstance(data, dict) else data
        candidates: list[AssetCandidate] = []
        for entry in entries:
            remote_id = str(entry.get("bvid") or entry.get("id") or "")
            if not remote_id:
                continue
            candidates.append(
                AssetCandidate(
                    candidate_id=f"bilibili:{remote_id}",
                    kind=AssetKind.VIDEO,
                    source="bilibili",
                    remote_id=remote_id,
                    title=entry.get("title") or remote_id,
                    source_url=f"https://www.bilibili.com/video/{remote_id}",
                    search_query=query,
                    creator=entry.get("author") or entry.get("uploader"),
                    duration_seconds=_parse_duration(entry.get("duration")),
                    thumbnail_url=_https_url(entry.get("pic") or entry.get("cover")),
                    description=entry.get("description") or entry.get("desc"),
                    metadata={
                        "view_count": entry.get("play") or entry.get("view_count"),
                        "search_adapter": "bilibili-cli",
                    },
                )
            )
        warnings = _enrich_bilibili_candidates(candidates, self.metadata_fetcher)
        return ProviderSearchResult(candidates, warnings, "bilibili-cli")


@dataclass(slots=True)
class FallbackSearchAdapter:
    source: str
    adapters: list[SearchAdapter]

    def search(self, query: str, limit: int) -> ProviderSearchResult:
        failures: list[str] = []
        for index, adapter in enumerate(self.adapters):
            try:
                result = adapter.search(query, limit)
                if not result.candidates and index + 1 < len(self.adapters):
                    failures.append(
                        f"{self.source} primary adapter returned no candidates; trying fallback"
                    )
                    continue
                if failures:
                    result.warnings[:0] = failures
                return result
            except RuntimeError as error:
                failures.append(str(error))
        raise RuntimeError(f"{self.source} search exhausted all adapters: {' | '.join(failures)}")


@dataclass(slots=True)
class YtDlpAcquisitionAdapter:
    source: str
    runner: CommandRunner = run_command
    executable: list[str] = field(default_factory=yt_dlp_command)

    def acquire(self, source_url: str, output_dir: Path, media: str) -> AcquisitionResult:
        _run_with_retry(
            self.command(source_url, output_dir, media),
            self.source,
            "yt-dlp acquisition",
            self.runner,
            3,
            1.0,
            time.sleep,
        )
        warnings: list[str] = []
        subtitle_result = self.runner(self.subtitle_command(source_url, output_dir))
        if subtitle_result.returncode != 0:
            warnings.append(
                _command_error(
                    subtitle_result, f"{self.source} subtitle acquisition degraded"
                )
            )
        return AcquisitionResult("yt-dlp", warnings)

    def command(self, source_url: str, output_dir: Path, media: str) -> list[str]:
        command = [
            *self.executable,
            "--ignore-config",
            "--no-playlist",
            "--windows-filenames",
            "--no-overwrites",
            "--write-info-json",
            "--write-description",
            "--write-thumbnail",
            "--paths",
            str(output_dir),
            "--output",
            "source.%(ext)s",
        ]
        if self.source == "youtube":
            command.extend(youtube_js_args())
        if media == "none":
            command.append("--skip-download")
        elif media == "proxy":
            command.extend(["--format", "bestvideo[height<=480]+bestaudio/best[height<=480]", "--merge-output-format", "mp4"])
        elif media == "master":
            command.extend(["--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]", "--merge-output-format", "mp4"])
        else:
            raise ValueError(f"Unknown media mode: {media}")
        command.append(source_url)
        return command

    def subtitle_command(self, source_url: str, output_dir: Path) -> list[str]:
        command = [
            *self.executable,
            "--ignore-config",
            "--no-playlist",
            "--windows-filenames",
            "--no-overwrites",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "zh.*,en.*",
            "--convert-subs",
            "srt",
            "--paths",
            str(output_dir),
            "--output",
            "source.%(ext)s",
        ]
        if self.source == "youtube":
            command.extend(youtube_js_args())
        command.append(source_url)
        return command


@dataclass(slots=True)
class YuttoAcquisitionAdapter:
    source: str = "bilibili"
    runner: CommandRunner = run_command
    executable: list[str] = field(default_factory=lambda: ["yutto"])

    def acquire(self, source_url: str, output_dir: Path, media: str) -> AcquisitionResult:
        if media == "none":
            raise RuntimeError("yutto metadata-only mode is not used because caption coverage varies")
        quality = "32" if media == "proxy" else "80"
        command = [
            *self.executable,
            "download",
            source_url,
            "--dir",
            str(output_dir),
            "--video-quality",
            quality,
            "--output-format",
            "mp4",
            "--no-danmaku",
            "--save-cover",
            "--with-metadata",
            "--no-color",
            "--no-progress",
        ]
        result = self.runner(command)
        if result.returncode != 0:
            raise RuntimeError(_command_error(result, "bilibili yutto acquisition failed"))
        if not any(path.is_file() for path in output_dir.rglob("*")):
            raise RuntimeError("bilibili yutto acquisition produced no files")
        return AcquisitionResult("yutto")


@dataclass(slots=True)
class FallbackAcquisitionAdapter:
    source: str
    adapters: list[AcquisitionAdapter]

    def acquire(self, source_url: str, output_dir: Path, media: str) -> AcquisitionResult:
        failures: list[str] = []
        for adapter in self.adapters:
            try:
                result = adapter.acquire(source_url, output_dir, media)
                if failures:
                    result.warnings[:0] = failures
                return result
            except RuntimeError as error:
                failures.append(str(error))
        raise RuntimeError(f"{self.source} acquisition exhausted all adapters: {' | '.join(failures)}")


def fetch_bilibili_metadata(remote_id: str) -> dict | None:
    parameter = "bvid" if str(remote_id).upper().startswith("BV") else "aid"
    url = f"https://api.bilibili.com/x/web-interface/view?{parameter}={remote_id}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MaterialScout/0.2",
            "Referer": "https://www.bilibili.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    return payload.get("data") if payload.get("code") == 0 else None


def default_adapters(runner: CommandRunner = run_command) -> dict[str, SearchAdapter]:
    return {
        "youtube": YtDlpSearchAdapter("youtube", "ytsearch", runner),
        "bilibili": FallbackSearchAdapter(
            "bilibili",
            [
                BilibiliCliSearchAdapter(runner=runner),
                BilibiliSearchAdapter("bilibili", "bilisearch", runner),
            ],
        ),
    }


def default_acquisition_adapters(
    runner: CommandRunner = run_command,
) -> dict[str, AcquisitionAdapter]:
    return {
        "youtube": YtDlpAcquisitionAdapter("youtube", runner),
        "bilibili": FallbackAcquisitionAdapter(
            "bilibili",
            [YuttoAcquisitionAdapter(runner=runner), YtDlpAcquisitionAdapter("bilibili", runner)],
        ),
    }


def _enrich_bilibili_candidates(
    candidates: list[AssetCandidate], metadata_fetcher: MetadataFetcher
) -> list[str]:
    failed = 0
    for candidate in candidates:
        try:
            metadata = metadata_fetcher(candidate.remote_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            metadata = None
        if not metadata:
            failed += 1
            continue
        bvid = str(metadata.get("bvid") or candidate.remote_id)
        candidate.remote_id = bvid
        candidate.candidate_id = f"bilibili:{bvid}"
        candidate.source_url = f"https://www.bilibili.com/video/{bvid}"
        candidate.title = metadata.get("title") or candidate.title
        candidate.creator = (metadata.get("owner") or {}).get("name") or candidate.creator
        candidate.duration_seconds = _as_float(metadata.get("duration")) or candidate.duration_seconds
        candidate.thumbnail_url = _https_url(metadata.get("pic")) or candidate.thumbnail_url
        candidate.description = metadata.get("desc") or candidate.description
        if metadata.get("pubdate"):
            candidate.published_at = datetime.fromtimestamp(int(metadata["pubdate"]), UTC).isoformat()
        candidate.metadata.update({"aid": metadata.get("aid"), "view_count": (metadata.get("stat") or {}).get("view")})
    if failed:
        return [f"bilibili metadata enrichment failed for {failed}/{len(candidates)} candidates; basic search data was kept"]
    return []


def _run_with_retry(
    command: list[str],
    source: str,
    operation: str,
    runner: CommandRunner,
    max_attempts: int,
    retry_delay_seconds: float,
    sleeper: Sleeper,
) -> subprocess.CompletedProcess[str]:
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max_attempts):
        result = runner(command)
        if result.returncode == 0:
            return result
        detail = _compact_detail(
            result.stderr.strip() or result.stdout.strip() or "unknown command error"
        )
        if attempt + 1 >= max_attempts or not _is_transient(detail):
            raise RuntimeError(f"{source} {operation} failed: {detail}")
        sleeper(retry_delay_seconds * (2**attempt))
    assert result is not None
    return result


def _command_error(result: subprocess.CompletedProcess[str], prefix: str) -> str:
    detail = _compact_detail(
        result.stderr.strip() or result.stdout.strip() or "unknown command error"
    )
    return f"{prefix}: {detail}"


def _compact_detail(detail: str, limit: int = 2000) -> str:
    compact = "\n".join(line for line in detail.splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 15] + "... [truncated]"


def _parse_duration(value: object) -> float | None:
    if value is None:
        return None
    text = str(value)
    if ":" not in text:
        return _as_float(value)
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient(detail: str) -> bool:
    normalized = detail.lower()
    markers = (
        "http error 412",
        "http error 429",
        "http error 502",
        "http error 503",
        "ratelimiterror",
        "rate limit",
        "timed out",
        "timeout",
        "network_error",
    )
    return any(marker in normalized for marker in markers)


def _https_url(value: object) -> str | None:
    if not value:
        return None
    url = str(value)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url
