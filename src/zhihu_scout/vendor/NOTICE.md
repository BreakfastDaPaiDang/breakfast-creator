# Local request signing helper

`zse-signer.mjs` is an unchanged copy of `zse-signer.js` from
https://github.com/meurz/zhihu-mcp-server (formerly iteng007/zhihu-mcp-server).
That project declares AGPL-3.0 and credits its implementation to
https://github.com/zly2006/zhihu-plus-plus (AGPL-3.0).

The source URL and SHA-256 are in `source.json`. The full license is included in
`LICENSE-AGPL-3.0.txt`. Preserve these notices when redistributing this component.
No browser or MCP server from those projects is installed or executed.

`sign-stdin.mjs` is the workspace's local adapter, added 2026-09-16; it accepts
URL and d_c0 through stdin and emits only request signature headers. It does not
access accounts, send network requests, or store credentials. This adapter is
provided under AGPL-3.0 together with the signing helper.
