# Source reliability

## Adapter order

| Operation | Primary | Fallback | Manual recovery |
| --- | --- | --- | --- |
| YouTube search | project-locked yt-dlp | none | add a known URL as a candidate later |
| YouTube media | project-locked yt-dlp + Node/EJS | none | import an authorized local file |
| YouTube captions | yt-dlp caption stage | none | report the gap; no local ASR installed |
| Bilibili search | bilibili-cli | yt-dlp | supply a BV URL |
| Bilibili media | yutto | yt-dlp | import an authorized local file |

## Interpret failures

- `HTTP 412` or `RateLimitError`: Bilibili anti-automation. Reduce result count and request rate; wait before retrying. Do not loop aggressively.
- `bilibili yutto acquisition produced no files`: yutto exits 0 even when it fails. On some hosts (verified on the Linux dev box, 2026-10-08) Bilibili's `x/web-interface/view` returns 412 to yutto with or without a cookie; acquisition then falls back to yt-dlp, which is expected rather than a missing dependency.
- `HTTP 429`: source throttling. Keep media already acquired. Retry captions later.
- YouTube JavaScript/EJS error: run `uv sync`, then `material-scout doctor`; Node 22+ or Deno 2.3+ is required.
- YouTube PO Token/403 error: install and configure a trusted PO Token provider only then. Tokens may be video-bound; never persist a token in a manifest or log.
- `UnicodeEncodeError` on Windows: all Material Scout subprocesses force UTF-8. If it reappears, run through Material Scout instead of invoking the upstream CLI directly.
- Empty results: inspect `candidates.json.warnings`; empty is not proof that no matching content exists.

## Health expectations

`doctor` reports `error` for a missing required dependency, `degraded` when a fallback remains usable, and `ok` after a live source query succeeds. Use `--json` when another Agent or automation needs to branch on the report.
