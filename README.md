# Agent 自媒体工作区

这是一个面向 Codex 等编码 Agent 的视频生产工作区。默认策略是：本地完成研究整理、转录、字幕、时间线、动效和渲染；图片、视频、正式配音等生成能力按项目接远程 API。

## 当前能力

- 早饭精英视频助手：本地用户信息、风格范例和平台连接配置页，`uv run breakfast-assistant serve`，见 [使用说明](docs/breakfast-assistant.md)
- FFmpeg / ffprobe：剪辑、转码、混音、字幕、质检
- yt-dlp：获取有权使用的在线视频和字幕
- faster-whisper：本地语音转录与词级时间戳
- Remotion：React 驱动的确定性视频模板
- HyperFrames：HTML/CSS/GSAP 动效视频
- Agent Skills：视频编排、对话式剪辑、本地化
- Material Scout：通用内容资产搜索、候选拼图、归档和权利门禁
- `material-scout transcript`：复用本地凭据获取B站平台字幕，生成带时间戳的文案阅读稿，见 [使用说明](docs/bilibili-transcript.md)
- Comment Scout：给 Agent 使用的 B 站评论接口采集、断点恢复、CSV 导入与讨论串阅读包（无需操作浏览器）
- Zhihu Scout：知乎问题搜索、回答与评论采集、按赞数筛选和原文阅读包；Python + Node.js，无浏览器运行依赖，2026-09-16已完成真实Cookie小样本联调，见 [使用说明](docs/zhihu-scout.md)

评论工具使用及 Agent 状态处理见 [docs/comment-scout.md](docs/comment-scout.md)。2026-09-15已完成真实账号下的评论与字幕小样本联调；平台限制和采集范围见各次报告。

## 常用命令

```powershell
# 检查环境
npm run doctor

# 启动 Remotion Studio
npm run studio

# 渲染本地冒烟样片
npm run render:smoke

# 本地转录（首次运行会下载所选模型）
uv run python automation/transcribe.py input.mp4 --output projects/demo/transcript.srt

# 创建一个标准项目目录
powershell -ExecutionPolicy Bypass -File automation/new-project.ps1 -Slug my-topic

# 搜索 B 站和 YouTube，生成候选 JSON、封面拼图和 HTML
uv run material-scout doctor --network
uv run material-scout search --query "AI 数据中心" --source bilibili --source youtube --limit 10

# 将任意本地文件登记为内容资产
uv run material-scout register brand/logo.png --rights-status owned

# 启动本地 OpenChatCut（隐藏后台进程，默认端口 5199）
powershell -ExecutionPolicy Bypass -File automation/start-openchatcut.ps1
```

## 生产原则

1. 原始素材只读，所有派生文件进入项目目录。
2. 每个外部素材必须记录来源与授权状态。
3. 远程生成前先提交分镜、模型、预计调用次数与预算。
4. 高风险事实、版权、肖像、声音克隆和商业宣传必须人工审核。
5. 默认交付 H.264/AAC MP4，并使用 ffprobe 做结构检查。

详细规则见 `AGENTS.md`，品牌规范位于 `brand/`，项目模板位于 `templates/project/`。

OpenChatCut 源码位于本地忽略目录 `tools/OpenChatCut/`。Codex 的项目级 MCP 配置位于 `.codex/config.toml`；Bearer Token 保存在 Windows 用户环境变量 `OPENCHATCUT_MCP_TOKEN`，不会写进仓库。

内容资产的领域语言见 `CONTEXT.md`。`media-library/` 下的目录数据库、搜索会话和媒体文件是本地运行数据，默认不进入 Git。
