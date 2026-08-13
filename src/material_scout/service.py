from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .catalog import Catalog
from .contact_sheet import render_discovery_artifacts
from .models import (
    AssetCandidate,
    ContentAsset,
    Provenance,
    Representation,
    RepresentationRole,
    RightsStatus,
    infer_asset_kind,
)
from .providers import (
    AcquisitionAdapter,
    CommandRunner,
    SearchAdapter,
    default_acquisition_adapters,
    default_adapters,
    run_command,
)


@dataclass(slots=True)
class SearchSession:
    output_dir: Path
    candidates: list[AssetCandidate]
    contact_sheets: list[Path]
    warnings: list[str] = field(default_factory=list)


class MaterialScout:
    """Deep module for discovering, acquiring, and registering content assets."""

    def __init__(
        self,
        library_root: Path,
        adapters: dict[str, SearchAdapter] | None = None,
        acquisition_adapters: dict[str, AcquisitionAdapter] | None = None,
        command_runner: CommandRunner = run_command,
    ):
        self.library_root = library_root.resolve()
        self.catalog_path = self.library_root / "catalog.sqlite3"
        self.adapters = adapters if adapters is not None else default_adapters(command_runner)
        self.acquisition_adapters = (
            acquisition_adapters
            if acquisition_adapters is not None
            else default_acquisition_adapters(command_runner)
        )
        self.command_runner = command_runner

    def search(
        self,
        queries: Iterable[str],
        sources: Iterable[str],
        limit_per_query: int = 10,
        output_dir: Path | None = None,
    ) -> SearchSession:
        clean_queries = [query.strip() for query in queries if query.strip()]
        clean_sources = list(dict.fromkeys(sources))
        if not clean_queries:
            raise ValueError("At least one non-empty search query is required.")
        unknown_sources = [source for source in clean_sources if source not in self.adapters]
        if unknown_sources:
            raise ValueError(f"Unknown sources: {', '.join(unknown_sources)}")

        destination = (output_dir or self._new_session_dir()).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        merged: dict[str, AssetCandidate] = {}
        for source in clean_sources:
            adapter = self.adapters[source]
            for query in clean_queries:
                try:
                    result = adapter.search(query, limit_per_query)
                    warnings.extend(result.warnings)
                    for candidate in result.candidates:
                        merged.setdefault(candidate.candidate_id, candidate)
                except RuntimeError as error:
                    warnings.append(str(error))

        candidates = list(merged.values())
        manifest = {
            "schema_version": 1,
            "created_at": _now(),
            "queries": clean_queries,
            "sources": clean_sources,
            "warnings": warnings,
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        (destination / "candidates.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sheets = render_discovery_artifacts(candidates, destination)
        return SearchSession(destination, candidates, sheets, warnings)

    def acquire(
        self,
        candidate: AssetCandidate,
        rights_status: RightsStatus,
        purpose: str = "research",
        media: str = "proxy",
    ) -> ContentAsset:
        if purpose == "production" and rights_status is RightsStatus.UNKNOWN:
            raise PermissionError("Production acquisition requires a recorded rights status.")
        if media == "master" and rights_status is RightsStatus.UNKNOWN:
            raise PermissionError("Master acquisition requires a recorded rights status.")

        asset_id = _safe_id(candidate.candidate_id)
        asset_dir = self.library_root / "assets" / asset_id
        representation_dir = asset_dir / "representations"
        representation_dir.mkdir(parents=True, exist_ok=True)
        adapter = self.acquisition_adapters.get(candidate.source)
        if adapter is None:
            raise ValueError(f"No acquisition adapter for source: {candidate.source}")
        acquisition = adapter.acquire(candidate.source_url, representation_dir, media)

        representations = [
            _representation_for(path, media)
            for path in sorted(representation_dir.rglob("*"))
            if path.is_file()
        ]
        asset = ContentAsset(
            asset_id=asset_id,
            kind=candidate.kind,
            title=candidate.title,
            provenance=Provenance(
                source=candidate.source,
                remote_id=candidate.remote_id,
                source_url=candidate.source_url,
                creator=candidate.creator,
                published_at=candidate.published_at,
                acquired_at=_now(),
            ),
            rights_status=rights_status,
            representations=representations,
            metadata={
                "purpose": purpose,
                "requested_media": media,
                "visible_mark_status": "unassessed",
                "search_query": candidate.search_query,
                "acquisition_adapter": acquisition.adapter,
                "acquisition_warnings": acquisition.warnings,
            },
        )
        (asset_dir / "asset.json").write_text(
            json.dumps(asset.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with Catalog(self.catalog_path) as catalog:
            catalog.upsert_asset(asset)
        return asset

    def register_local(
        self,
        path: Path,
        rights_status: RightsStatus,
        title: str | None = None,
    ) -> ContentAsset:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("Only individual files can be registered in version 1.")
        digest = _sha256(resolved)
        asset = ContentAsset(
            asset_id=f"local_{digest[:16]}",
            kind=infer_asset_kind(resolved),
            title=title or resolved.stem,
            provenance=Provenance(
                source="local",
                remote_id=None,
                source_url=resolved.as_uri(),
                acquired_at=_now(),
            ),
            rights_status=rights_status,
            representations=[
                Representation(
                    role=RepresentationRole.ORIGINAL,
                    path=str(resolved),
                    mime_type=mimetypes.guess_type(resolved.name)[0],
                    sha256=digest,
                )
            ],
            metadata={"visible_mark_status": "unassessed"},
        )
        with Catalog(self.catalog_path) as catalog:
            catalog.upsert_asset(asset)
        return asset

    def list_assets(self, limit: int = 100) -> list[dict[str, object]]:
        with Catalog(self.catalog_path) as catalog:
            return catalog.list_assets(limit)

    def _new_session_dir(self) -> Path:
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        return self.library_root / "sessions" / timestamp

def load_candidates(path: Path) -> list[AssetCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise TypeError("Candidate manifest must contain a candidates list.")
    return [AssetCandidate.from_dict(value) for value in values]


def select_candidates(candidates: list[AssetCandidate], identifiers: Iterable[str]) -> list[AssetCandidate]:
    selected: list[AssetCandidate] = []
    by_candidate_id = {candidate.candidate_id: candidate for candidate in candidates}
    by_remote_id = {candidate.remote_id: candidate for candidate in candidates}
    for identifier in identifiers:
        normalized = identifier.strip()
        candidate: AssetCandidate | None = None
        if normalized.startswith("#") and normalized[1:].isdigit():
            index = int(normalized[1:]) - 1
            if 0 <= index < len(candidates):
                candidate = candidates[index]
        candidate = candidate or by_candidate_id.get(normalized) or by_remote_id.get(normalized)
        if not candidate:
            raise KeyError(f"Candidate not found: {identifier}")
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _representation_for(path: Path, media: str) -> Representation:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus"}:
        role = RepresentationRole.PROXY if media == "proxy" else RepresentationRole.MASTER
    elif suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        role = RepresentationRole.THUMBNAIL
    elif suffix in {".srt", ".vtt", ".ass"}:
        role = RepresentationRole.SUBTITLE
    elif suffix in {".json", ".nfo"}:
        role = RepresentationRole.METADATA
    elif suffix in {".description", ".txt"}:
        role = RepresentationRole.DESCRIPTION
    else:
        role = RepresentationRole.DERIVATIVE
    return Representation(
        role=role,
        path=str(path.resolve()),
        mime_type=mimetypes.guess_type(path.name)[0],
        sha256=_sha256(path),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


def _now() -> str:
    return datetime.now(UTC).isoformat()
