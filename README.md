# Agent 自媒体工作区

面向 Codex、Claude Code 等编码 Agent 的深度知识类视频工作区。当前以文案创作为主线：研究、对标分析、评论采集和文案写作。视频制作能力按实际需要再接入，不预装。

## 当前能力

- 早饭精英视频助手：本地用户信息、风格范例和平台连接配置页，`uv run breakfast-assistant serve`，见 [使用说明](docs/breakfast-assistant.md)
- Zhihu Scout：知乎问题搜索、回答与评论采集、按赞数筛选和原文阅读包；Python + Node.js，无浏览器运行依赖，见 [使用说明](docs/zhihu-scout.md)
- Comment Scout：B 站评论接口采集、断点恢复、CSV 导入与讨论串阅读包，见 [使用说明](docs/comment-scout.md)
- `material-scout transcript`：复用本地凭据获取 B 站平台字幕，生成带时间戳的文案阅读稿，见 [使用说明](docs/bilibili-transcript.md)
- Material Scout：内容资产搜索、候选拼图、归档和权利门禁

2026-09-15/16 已完成 B 站评论、字幕和知乎的真实账号小样本联调；平台限制和采集范围见各次报告。

## 常用命令

```powershell
# 会话配置检查
uv run breakfast-assistant status

# 检查素材来源与网络
uv run material-scout doctor --network

# 创建文案阶段项目目录
powershell -ExecutionPolicy Bypass -File automation/new-project.ps1 -Slug my-topic

# 运行测试
uv run python -m unittest discover -s tests
```

## 视频制作

短动画用项目内的 Python 脚本逐帧渲染（PIL + numpy + ffmpeg），流程见 `docs/short-animation.md`。口播剪辑、配音、本地转录等能力在实际需要时再添加。

## 生产原则

1. 原始素材只读，所有派生文件进入项目目录。
2. 每个外部素材必须记录来源与授权状态。
3. 远程生成前先提交分镜、模型、预计调用次数与预算。
4. 高风险事实、版权、肖像、声音克隆和商业宣传必须人工审核。
5. 默认交付 H.264/AAC MP4，并使用 ffprobe 做结构检查。

详细规则见 `AGENTS.md`，品牌规范位于 `brand/`，项目模板位于 `templates/project/`，内容资产的领域语言见 `CONTEXT.md`。`media-library/` 下的目录数据库、搜索会话和媒体文件是本地运行数据，默认不进入 Git。
