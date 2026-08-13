# Content asset schema

## Domain objects

- **Content Asset**: Stable conceptual asset independent of file format or source platform.
- **Provenance**: Source, remote ID, URL, creator, publication time, and acquisition time.
- **Representation**: Concrete original, proxy, master, thumbnail, subtitle, transcript, metadata file, or derivative.
- **Fragment**: Selectable temporal range, spatial region, or text range within an asset.
- **Usage**: A project's reference to an asset or fragment.
- **Visible Mark**: Separately assessed platform, creator, stock, brand, or unknown on-screen mark.

## Invariants

1. Keep original representations immutable.
2. Default rights status to `unknown`; downloading does not change it.
3. Store every derivative as a new representation with its parent and operation metadata.
4. Let source adapters emit normalized candidates; never leak platform response shapes into projects.
5. Refer to assets by stable asset ID and fragments by locators, not copied filenames.

## Extension seams

- Add source adapters behind the search adapter seam.
- Add kind-specific inspection behind representation processors.
- Add semantic indexes behind the catalog without changing the CLI's asset model.
- Add temporal, spatial, and textual fragment locators without creating separate video/image/document catalogs.
