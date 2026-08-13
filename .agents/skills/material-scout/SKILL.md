---
name: material-scout
description: Discover, acquire, register, and catalog reusable content assets with provenance and rights controls. Use when Codex needs to search YouTube or Bilibili from a script or topic, create candidate contact sheets, download selected research proxies and subtitles, register local video/image/audio/document/font/template assets, inspect the material catalog, or assess whether a visible watermark or other mark may be altered.
---

# Material Scout

Use the workspace CLI as the single interface. Treat video, image, audio, document, font, and template files as content assets; do not create platform-specific project workflows.

## Discover candidates

Derive several concrete queries from the brief or script: named entities, literal actions, visual metaphors, locations, and negative constraints. Keep each query short.

```powershell
uv run material-scout search `
  --query "AI 数据中心 服务器" `
  --query "GPU server racks b-roll" `
  --source bilibili `
  --source youtube `
  --limit 10
```

Read `candidates.json`, then inspect the numbered contact sheets. Select candidates by `#number`, normalized candidate ID, or remote video ID.

## Acquire selected candidates

Acquire a low-resolution research proxy by default. This preserves bandwidth while providing media, descriptions, thumbnails, subtitles, and metadata for analysis.

```powershell
uv run material-scout acquire `
  --session media-library/sessions/SESSION/candidates.json `
  --id "#003" --id "youtube:VIDEO_ID" `
  --purpose research --media proxy --rights-status unknown
```

Use `--media none` to collect metadata and available captions without video. Use `--purpose production` or `--media master` only after recording a non-unknown rights status. Acquisition never proves permission to publish.

## Register local assets

Register any local file without copying or modifying it:

```powershell
uv run material-scout register brand/logo.png --rights-status owned
uv run material-scout list
```

Read [schema.md](references/schema.md) when adding an asset type, representation, fragment processor, source adapter, or catalog field.

## Handle visible marks

Record platform logos, creator marks, stock watermarks, and brand marks separately from asset rights:

```powershell
uv run material-scout mark record `
  --asset-id ASSET_ID --status present --kind platform --authorization unknown

uv run material-scout mark check --asset-id ASSET_ID --treatment inpaint
```

Read [visible-marks.md](references/visible-marks.md) before proposing crop, mask, inpaint, or logo removal. Do not alter a third-party mark merely because a file is downloadable. When treatment is allowed, preserve the original and create a new audited representation; use the `ffmpeg` skill for deterministic crop or mask operations.

## Handoff to editing

Keep acquired originals and proxies under `media-library/`. Copy or reference only approved fragments in a project manifest. Record the content asset ID, source URL, rights status, representation, in/out points, and intended purpose.
