from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class AssetKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    FONT = "font"
    TEMPLATE = "template"
    OTHER = "other"


class RightsStatus(StrEnum):
    UNKNOWN = "unknown"
    OWNED = "owned"
    LICENSED = "licensed"
    PERMISSION = "permission"
    CREATIVE_COMMONS = "creative-commons"
    PUBLIC_DOMAIN = "public-domain"


class RepresentationRole(StrEnum):
    ORIGINAL = "original"
    PROXY = "proxy"
    MASTER = "master"
    THUMBNAIL = "thumbnail"
    SUBTITLE = "subtitle"
    TRANSCRIPT = "transcript"
    METADATA = "metadata"
    DESCRIPTION = "description"
    DERIVATIVE = "derivative"


@dataclass(slots=True)
class Provenance:
    source: str
    remote_id: str | None
    source_url: str
    creator: str | None = None
    published_at: str | None = None
    acquired_at: str | None = None


@dataclass(slots=True)
class Representation:
    role: RepresentationRole
    path: str
    mime_type: str | None = None
    sha256: str | None = None
    derived_from: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssetCandidate:
    candidate_id: str
    kind: AssetKind
    source: str
    remote_id: str
    title: str
    source_url: str
    search_query: str
    creator: str | None = None
    duration_seconds: float | None = None
    published_at: str | None = None
    thumbnail_url: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetCandidate":
        value = dict(value)
        value["kind"] = AssetKind(value["kind"])
        return cls(**value)


@dataclass(slots=True)
class ContentAsset:
    asset_id: str
    kind: AssetKind
    title: str
    provenance: Provenance
    rights_status: RightsStatus
    representations: list[Representation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".tif", ".tiff"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
DOCUMENT_EXTENSIONS = {".md", ".txt", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".csv", ".json", ".yaml", ".yml"}
FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
TEMPLATE_EXTENSIONS = {".mogrt", ".aep", ".prproj"}


def infer_asset_kind(path: Path) -> AssetKind:
    extension = path.suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return AssetKind.VIDEO
    if extension in IMAGE_EXTENSIONS:
        return AssetKind.IMAGE
    if extension in AUDIO_EXTENSIONS:
        return AssetKind.AUDIO
    if extension in DOCUMENT_EXTENSIONS:
        return AssetKind.DOCUMENT
    if extension in FONT_EXTENSIONS:
        return AssetKind.FONT
    if extension in TEMPLATE_EXTENSIONS:
        return AssetKind.TEMPLATE
    return AssetKind.OTHER
