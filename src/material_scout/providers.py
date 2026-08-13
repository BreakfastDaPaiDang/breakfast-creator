from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol

from .models import AssetCandidate, AssetKind


class SearchAdapter(Protocol):
    source: str

    def search(self, query: str, limit: int) -> list[AssetCandidate]: ...


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]
MetadataFetcher = Callable[[str], dict | None]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@dataclass(slots=True)
class YtDlpSearchAdapter:
    source: str
    search_prefix: str
    runner: CommandRunner = run_command
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0
    sleeper: Sleeper = time.sleep

    def search(self, query: str, limit: int) -> list[AssetCandidate]:
        target = f"{self.search_prefix}{limit}:{query}"
        command = [
            "yt-dlp",
            "--ignore-config",
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            target,
        ]
        result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(self.max_attempts):
            result = self.runner(command)
            if result.returncode == 0:
                break
            detail = result.stderr.strip() or result.stdout.strip() or "unknown yt-dlp error"
            if attempt + 1 >= self.max_attempts or not _is_transient(detail):
                raise RuntimeError(f"{self.source} search failed: {detail}")
            self.sleeper(self.retry_delay_seconds * (2**attempt))
        assert result is not None

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
                    },
                )
            )
        return candidates

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

    def search(self, query: str, limit: int) -> list[AssetCandidate]:
        candidates = YtDlpSearchAdapter.search(self, query, limit)
        for candidate in candidates:
            try:
                metadata = self.metadata_fetcher(candidate.remote_id)
            except Exception:
                metadata = None
            if not metadata:
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
                candidate.published_at = datetime.fromtimestamp(
                    int(metadata["pubdate"]), UTC
                ).isoformat()
            candidate.metadata.update(
                {
                    "aid": metadata.get("aid"),
                    "view_count": (metadata.get("stat") or {}).get("view"),
                }
            )
        return candidates


def fetch_bilibili_metadata(aid: str) -> dict | None:
    url = f"https://api.bilibili.com/x/web-interface/view?aid={aid}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MaterialScout/0.1",
            "Referer": "https://www.bilibili.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    return payload.get("data") if payload.get("code") == 0 else None


def default_adapters(runner: CommandRunner = run_command) -> dict[str, SearchAdapter]:
    return {
        "youtube": YtDlpSearchAdapter("youtube", "ytsearch", runner),
        "bilibili": BilibiliSearchAdapter("bilibili", "bilisearch", runner),
    }


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient(detail: str) -> bool:
    markers = ("HTTP Error 412", "HTTP Error 429", "HTTP Error 502", "HTTP Error 503", "timed out")
    return any(marker in detail for marker in markers)


def _https_url(value: object) -> str | None:
    if not value:
        return None
    url = str(value)
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url
