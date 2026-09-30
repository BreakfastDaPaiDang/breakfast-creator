# 早饭精英视频助手

本地创作配置页：暖白编辑台与平台连接卡片。一个自由文本「用户信息」是唯一必填项；灰色提示里的四个角度仅供参考。没有对话助手按钮，没有四项校验。

## 打开

```powershell
uv run breakfast-assistant serve
```

访问 <http://localhost:8765> 或 <http://127.0.0.1:8765>。服务只监听本机。也可运行工作区根目录的 `启动早饭助手.ps1`，它在后台启动并打开页面。已有服务时直接复用。

不需要构建前端，也没有新增Python或Node依赖。HTML、CSS、JavaScript随Python包提供。本地工具继续独立工作，不需要保持配置页打开。

## 共用的本地数据

| 内容 | 路径 | 读写方式 |
|---|---|---|
| 用户信息 | `config/creator-profile.json` | 前端或`profile-set` |
| 满意的文案／视频 | `assets/references/style-examples/` | 直接放文件；页面可打开文件夹或复制路径 |
| B站、知乎凭据 | `.env` | 前端或既有工具的`auth set --stdin` |

用户信息格式：

```json
{
  "schema_version": 1,
  "user_info": "用户自己确认的介绍，自由文本",
  "updated_at": "由工具保存时填写的UTC时间"
}
```

`serve`首次启动仅创建空用户信息，不推断画像；以后启动不覆盖已有内容。真实信息和凭据均被Git忽略。Agent在每次会话开头检查：

```powershell
uv run breakfast-assistant status
```

JSON提供`ready`、`missing_required`、`missing_optional`和当前用户信息。只有`user_info`为空时才属于必填缺失；范例和平台连接只作提醒。`status`为本地检查，不联网，也不返回Cookie值。

Agent通过stdin保存文本，`--revision`取刚读取的状态，避免与前端相互覆盖：

```powershell
Get-Content -Raw -Encoding UTF8 "用户已确认的介绍.txt" | uv run breakfast-assistant profile-set --revision "刚读取的revision"
```

这里的路径和revision由Agent替换，不要求用户自行执行。前端在重新获得焦点时读取最新配置；正在输入时保留草稿，遇到并发改动提示载入最新内容。保存是显式操作，不自动保存未完成输入。

## 平台连接

- B站：填写`SESSDATA`完整值。
- 知乎：填写`z_c0`、`d_c0`，`_xsrf`选填。
- 两种连接均有就地展开的三步示意：登录后F12 → 应用程序／Cookie／域名 → 点选字段并从底部复制完整值。
- 输入默认遮蔽，页面不会读取或回显已保存的值。更新失败保留原配置；写入一个平台时保留`.env`里的其他设置。
- 页面显示「已配置」，仅表示存在格式有效的凭据；没有冒充「登录有效」或所有接口已验证。实际采集时由既有工具验证。此次没有新增网络验证按钮，页面加载与保存也不发出平台网络请求。
- 环境变量优先于`.env`；如果连接由环境变量提供，前端显示该状态并停止无效的本地替换，由Agent处理对应环境配置。

前端收益说明分别对应视频搜索／字幕／评论，和问题／回答／评论。平台权限、字幕是否存在等仍由具体采集结果决定。

## 本地接口与验证

只提供固定页面文件和配置接口，不开放工作区文件服务器。API使用页面会话令牌，检查Host和Origin；不提供跨域访问，拒绝外站调用配置写入。原子保存与本地写锁避免文件写坏；用户信息另用版本比较防止覆盖并发修改。

```powershell
uv run python -m unittest discover -s tests -p test_breakfast_assistant.py -v
node --check src/breakfast_assistant/web/app.js
```

测试覆盖：自由文本保存和重新读取、空内容校验、版本冲突、损坏文件保留、两个平台凭据与其他配置共存、凭据不回传、环境变量优先、范例文件发现、HTTP保存、目录暴露与跨站请求拒绝。测试使用临时目录和虚构凭据。

首次实现的浏览器视觉检查被Chrome的`ERR_BLOCKED_BY_CLIENT`阻止；本地HTTP访问正常。这一项尚未验收，未修改浏览器安全设置。
