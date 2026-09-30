# B站平台字幕阅读稿

用于已授权的对标文案研究；读取平台提供的字幕，不把视频简介、AI总结冒充全文。

```powershell
uv run material-scout transcript BV17x411w7KC --output media-library/research/示例字幕
```

- 默认分P为1，使用 `--part` 指定其他分P。
- 复用 `.env` 的 `BILIBILI_COOKIE`，不需要浏览器；缺少凭据时向用户索取，由Agent配置。
- 与comment-scout共用同一库中的网络锁、请求状态与冷却期，默认请求间隔4秒。
- 保存 `captions.json`（平台原始字幕）、`transcript.md`（带时间戳的阅读稿）、`manifest.json`（来源、哈希、字幕类型、研究用途、权利状态）。
- 优先中文轨、同语言优先非AI轨。自动字幕明确标注；不能把字幕视为已校对的作者脚本。
- 下载字幕CDN文件时不携带账号Cookie，不把临时字幕地址写入manifest。
- 没有字幕时返回 `captions_unavailable`，不输出空文案；需另行获取音频并转录，按用户授权范围执行。
- `transcript` 返回JSON。调用前遵守工作区doctor规则；发生限流停止，不高频切换工具继续访问。
- 输出目录须不存在。第一版仅支持B站平台字幕；可通过现有catalog登记产物。
