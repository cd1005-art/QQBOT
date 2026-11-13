# QQBOT — QQ 官方机器人群管理控制台

<p align="center">
  <a href="https://qm.qq.com/cgi-bin/qm/qr?k=&jump_from=webapi&group_code=556269215">
    <img src="https://img.shields.io/badge/QQ%E7%BE%A4-556269215-12B7F5?style=for-the-badge&logo=tencentqq&logoColor=white" alt="QQ群 556269215"></a>
  <a href="https://github.com/cd1005-art/QQBOT/releases/latest">
    <img src="https://img.shields.io/github/v/release/cd1005-art/QQBOT?style=for-the-badge" alt="Release"></a>
  <a href="https://github.com/cd1005-art/QQBOT/stargazers">
    <img src="https://img.shields.io/github/stars/cd1005-art/QQBOT?style=for-the-badge&logo=github" alt="Stars"></a>
</p>

用 QQ 开放平台的官方能力，把群管理做成一个可自托管的控制台：**收事件、存证据、执行处置、留全痕**。
不接第三方协议端，不模拟客户端。可选接入大模型：看图、对话、文生图，全部按需开关。

```
QQ 平台 --HTTPS POST--> https://你的域名/qqbot/webhook
                              │  nginx（443，TLS）
                              ▼
                     127.0.0.1:18900  webhook 接收端（验签 + 异步落库）
                     127.0.0.1:18901  面板 API
                              │
                              ▼
                     SQLite（群 / 成员 / 消息 / 审计 / 原始事件）
```

面板静态资源由 nginx 直接读站点目录，只有 `/panel/api/` 反代到 Python。

## 界面

![控制台总览](docs/shots/panel.png)

<p align="center">
  <img src="docs/shots/landing.png" width="49%" alt="落地页">
  <img src="docs/shots/docs.png" width="49%" alt="文档页">
</p>


## 能力

| 能力 | 状态 |
|---|---|
| 全量群消息接收（含非 @ 消息） | 可用 |
| 撤回消息（2 分钟窗口） | 可用 |
| 成员禁言 / 解禁 / 全员禁言 | 可用 |
| 发送消息 / @全体成员（官方标签 + 每日配额） | 可用 |
| 入群申请列表 / 审批 | 可用 |
| 群信息 / 机器人群内状态 | 可用 |
| Web 控制台（总览 / 群管理 / 规则 / 定时 / 事件 / 审计 / 设置） | 可用 |
| 关键词自动处置（撤回 / 禁言 / 撤回+禁言 / 关键词回复 / 撤回+回复） | 可用 |
| 定时任务（cron，多群，执行历史） | 可用 |
| 群级自动处置开关 | 可用 |
| 大模型群聊对话（任意 OpenAI 兼容接口） | 可用，需自填 key |
| 回复时 @ 提问者（markdown 消息） | 可用，开关控制 |
| 群图片识别与违规审核（撤回 / 禁言） | 可用，需视觉模型 |
| 网络找图发群（来张二次元图片） | 可用，图源 360 |
| 文生图（画一张…） | 可用，需另配生图模型 |
| 图片归档到图床（审核/识别/生图/找图） | 可用，默认开启 |
| 移出成员 / 群黑名单 / 成员列表接口 | **平台未开放（11253）** |

## 快速开始

### 一键安装（推荐）

```bash
wget https://github.com/cd1005-art/QQBOT/releases/latest/download/qqbot.tar.gz
tar -xzf qqbot.tar.gz -C qqbot && cd qqbot
sudo bash install.sh
```

脚本会完成：检查 Python 3.9+ → 建 venv → 装依赖 → 生成 `config.toml` → 注册并启动 systemd 服务。

常用参数：

```bash
bash install.sh --check                 # 只体检，不改任何文件
bash install.sh --dir ~/qqbot           # 装到自定义目录
bash install.sh --no-service            # 只准备环境，不装服务
bash install.sh --pip-index https://mirrors.aliyun.com/pypi/simple/   # 国内加速
bash install.sh --upgrade               # 升级：自动备份，失败自动回滚
bash install.sh --restore <备份目录>     # 回滚；目录可省略，会从备份路径推断
bash install.sh --service-name qqbot2   # 同机多实例时给服务改名
```

升级前会自动把当前代码、`config.toml`、`data/qqbot.db` 打包到 `<安装目录>/.backup/<时间戳>/`，
依赖安装失败或启动后健康检查不通过，会自动回滚并重启旧版本；只保留最近 5 份备份。

升级时重跑同一条命令即可，`config.toml` 与 `data/` 不会被覆盖。

### 手动安装

```bash
git clone https://github.com/cd1005-art/QQBOT.git
cd QQBOT
python -m pip install -r requirements.txt
cp config.example.toml config.toml   # 填 app_id / app_secret / panel.password
python tests/smoke.py                # 离线自检
python -m qqbot check                # 本地模拟平台校验与事件推送
python -m qqbot serve                # 同时启动 webhook(18900) 与面板(18901)
```

打开 `http://127.0.0.1:18901/panel/`。若 `app_id` / `app_secret` / 面板密码任一为空，会直接进入**初始化引导**，填完即写 `config.toml` 并热生效，不需要重启。

初始化完成后再调 `/panel/api/setup` 一律返回 403，防止远程改写凭证。

## 主体类型与权限差异

QQ 开放平台把开发者分为个人主体与企业主体，两者在**使用范围**上差异明显，功能代码本身不需要改。

| 能力 | 个人主体 | 企业主体 | 说明 |
|---|---|---|---|
| 群聊消息接收（全量） | ✅ 仅限自己是群主/管理员的群 | ✅ | 个人主体不能公开被添加，只能由创建者手动加入自己管理的群 |
| 公开使用（可被搜索/他人添加） | ❌ | ✅ | 官方 FAQ：企业主体已开放公开使用，个人暂不支持 |
| 单聊（私聊） | ⚠️ 仅「开发体验用户」 | ✅ 任意用户 | 个人主体需在管理端「开发设置」里逐个添加体验用户号码 |
| 撤回 / 禁言 / 全员禁言 / @全体 | ✅ | ✅ | 与主体无关，取决于机器人是否为该群管理员 |
| 主动消息频控 | 身份证认证 10 qps；未认证 5 qps + 30 qpm | 10 qps | 被动回复：单聊 60 分钟内最多 4 次 |
| 机器人创建名额 | 5 个 | 10 个（同主体累计 50） | |
| 成员管理（踢人 / 黑名单 / 成员列表） | ❌ | ❌ | 需平台内邀，企业认证也不够 |

**对本项目的实际影响：**

1. 个人主体可以完整体验 QQBOT 的群管理能力——规则处置、图片审核、文生图、定时公告都可用。
2. 个人主体无法让别人把机器人拉进群，也不能对任意用户私聊，只适合自己群里用或做开发测试。
3. 想对外提供使用，必须企业主体。

来源：[QQ Bot 常见问题答疑](https://bot.qq.com/wiki/bot_FAQ)、[发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)

## 平台侧配置

| 项 | 值 |
|---|---|
| 接入方式 | Webhook |
| 回调地址 | `https://你的域名/qqbot/webhook` |
| 接收事件 | 全部接收（代码只解析群事件，其余原样留档） |
| 群聊消息范围 | 手机 QQ 群设置里改为「获取群内全部消息」 |
| 主动发言 | 同上，打开「允许机器人主动在群聊内发言」 |
| 群内身份 | 机器人需设为群管理员 |

保存回调地址时平台会立刻发 `op=13` 校验，服务没起来就保存失败。

## Docker

**直接拉现成镜像（推荐）**

```bash
docker pull ghcr.io/cd1005-art/qqbot:latest

docker run -d --name qqbot \
  -p 127.0.0.1:18900:18900 -p 127.0.0.1:18901:18901 \
  -v ./config:/app/config -v ./data:/app/data \
  -e QQBOT_APP_ID=你的AppID \
  -e QQBOT_APP_SECRET=你的AppSecret \
  -e QQBOT_PANEL_PASSWORD=面板密码 \
  ghcr.io/cd1005-art/qqbot:latest
```

标签：`latest`、版本号（如 `v1.0.0`）、`sha-<短哈希>`。镜像为 linux/amd64，公开无需登录。

**本地构建**

```bash
docker compose up -d
```

等价的手写方式：

```bash
docker build -t qqbot:latest .
docker run -d --name qqbot \
  -p 127.0.0.1:18900:18900 -p 127.0.0.1:18901:18901 \
  -v ./config:/app/config -v ./data:/app/data \
  qqbot:latest
```

容器内监听 `0.0.0.0`（`QQBOT_BIND`），宿主机端口仍绑 `127.0.0.1`，对外统一由 nginx 进来。首次启动会自动从 `config.example.toml` 生成配置。

## 部署

nginx 参考 `deploy/nginx-qqbot-webhook.conf`：

- `/qqbot/webhook` → `127.0.0.1:18900`
- `/panel/api/` → `127.0.0.1:18901`
- `/panel/` → 站点目录静态文件

```bash
sudo cp deploy/qqbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now qqbot
journalctl -u qqbot -f
```

把 `deploy/` 里的路径、域名换成你自己的。

## 命令行

写操作需同时满足 `safety.dry_run = false` 且加 `--apply`。

```bash
python -m qqbot groups                          # 已知群
python -m qqbot members --group <g>             # 成员索引
python -m qqbot types                           # 事件类型统计
python -m qqbot events --limit 10               # 原始事件
python -m qqbot reindex                         # 重放原始事件回填索引
python -m qqbot audit                           # 操作审计

python -m qqbot recall  --group <g> --msg <id> | --last
python -m qqbot mute    --group <g> --member <m> --minutes 60
python -m qqbot unmute  --group <g> --member <m>
python -m qqbot mute-all --group <g> --on | --off
python -m qqbot joins   --group <g>
python -m qqbot approve --group <g> --member <m> --action approve|decline
python -m qqbot send    --group <g> --text "内容"
python -m qqbot at-all  --group <g> --text "内容"
```

## 控制台

| 页面 | 内容 |
|---|---|
| 总览 | 状态条、6 张统计卡、24 小时消息趋势、实时事件流（可自动刷新）、事件类型分布 |
| 群管理 | 群切换；概览（群信息 / 趋势 / 快捷操作）、成员（禁言解禁）、消息（剩余撤回时间 + 逐条撤回）、处置记录 |
| 规则 | 新建/**编辑**/启停/删除，包含或正则匹配，撤回/禁言/两者/回复，按群生效，命中测试 |
| 定时 | 5 段 cron，多群目标，下次执行预览，上次结果 |
| 事件 | 类型筛选、条数控制、原始 JSON 抽屉 |
| 审计 | 全量留痕，前端导出 CSV |
| 设置 | 凭证掩码、dry_run 开关、白名单、改面板密码、运行信息 |

## 自动处置（规则）

命中即处置，规则表 5 秒缓存，改规则不用重启。处置在独立线程执行，不阻塞 Webhook 的 3 秒响应。

```
match_type = contains | regex
action     = recall | mute | recall_mute | reply | recall_reply
params     = {"minutes": 10, "reply": "命中后回复的文本"}
groups     = []                  # 空 = 全部群
```

- `reply` / `recall_reply` 走被动回复（带原 `msg_id`），受 5 分钟 / 5 次窗口限制
- 回复类规则优先于模型对话：同一条消息命中回复规则后，不再交给大模型，避免双重回复

- 同一 `(rule_id, message_id)` 只处置一次，平台重推不会重复禁言
- `dry_run = true` 时只记一条「未执行」的处置记录，不调接口
- 群详情页可单独关闭本群的自动处置
- 每次命中写 `disposals` 表，群管理 → 处置 可查

## 定时任务

标准 5 段 cron：`分 时 日 月 周`

```
0 9 * * *       每天 09:00
*/30 * * * *    每 30 分钟
0 9 * * 1-5     工作日 09:00
```

调度线程每 20 秒扫一次到期任务；`dry_run = true` 时不发送，`last_result` 记为「dry_run 锁定，未发送」。执行结果同时写入审计。

## 对话（大模型）

可选能力，默认关闭。任意 OpenAI 兼容接口都能接：OpenAI、DeepSeek、Moonshot、通义、Ollama、one-api。

```toml
[llm]
enabled = false
base_url = "https://api.deepseek.com/v1"
api_key = ""
model = "deepseek-chat"
trigger = "at"          # at = 仅 @机器人；all = 所有消息（慎用）
keywords = []           # 命中即回，不需要 @
context_turns = 6       # 每群携带的上下文轮数
cooldown = 3            # 每群冷却秒数
daily_limit = 200       # 每群每天最多回复次数
```

- 回复走**被动消息**（带原 `msg_id`），落在平台 5 分钟 / 5 次窗口内
- 每群独立冷却与日限额，`chat_usage` 表按天累计调用数与 token
- `chat_history` 保留最近对话，既做上下文也做留档，可在面板「对话」页查看
- 每条记录带 `model` 与 `kind`：对话 / 生图 / 找图，面板直接显示是哪个模型答的
- 群聊与私聊都会记录，`scene` 区分；私聊走 `/v2/users/{openid}/messages`，需平台开通私聊权限
- 消息正文入库前会清洗：`<@openid>` → `@昵称`，`<faceType=…>` → `[表情]`
- 面板内可直接改配置、点「发送测试」验证连通性，不用改文件重启

## 图片能力：识别 vs 审核

两个独立开关，用途不同：

| 开关 | 作用 | 触发 | 风险 |
|---|---|---|---|
| `chat_enabled` 图片识别 | 让模型看懂图并参与对话 | 只有 @机器人（或触发词）时才送模型 | 消耗 token |
| `enabled` 图片审核 | 判断图片是否违规并处置 | 群里**每张**图片都会送模型 | 误判可能误禁言 |

```toml
[vision]
chat_enabled = false   # 识别：对话用
enabled = false        # 审核：合规用
```

两者共用模型配置与图片下载缓存（同一张图只下載一次）。

## 图片识别与审核

群里发图片时，事件里带 `attachments[].url`（带签名的临时地址）。开启后流程是：

```
下载图片（默认上限 8MB） → 视觉模型判断 → 违规就撤回 / 禁言 / 提示
```

```toml
[vision]
enabled = false
model = ""          # 留空跟随 [llm] 的 model
base_url = ""       # 留空跟随 [llm]
api_key = ""        # 留空跟随 [llm]
action = "recall_mute"   # recall | mute | recall_mute | none
minutes = 10
notice = ""         # 违规后补一句提示，留空则只处置
```

- 模型必须支持图像输入（`/v1/chat/completions` 的 `image_url` 内容块）
- 要求模型只输出 JSON，解析失败按「不违规」处理，宁可漏判不误伤
- 兼容 `violation` / `is_violation`、`summary` / `description` 等字段名差异
- 推理型模型会先消耗 reasoning token，`max_tokens` 默认给到 800
- 同一消息只审一次；结果写 `disposals` 与审计，面板「规则 → 图片审核」可查

## 图片归档

审核、识别、生图、找图拿到的图，都会上传到配置的图床，发送时改用图床直链。

```toml
[storage]
enabled = true                                  # 默认开启；未配 token 时自动不生效
base_url = "https://img.oddpp.com/api/v1"       # Lsky Pro 兼容接口
token = ""
timeout = 30
```

- 协议：`POST {base_url}/upload`，请求头 `X-API-TOKEN`，multipart 字段 `file`
- 图床走 CDN 且 CDN 下发的证书链不完整时，可加 `resolve_ip` 直连源站，证书仍按域名校验：

  ```toml
  [storage]
  base_url = "https://img.oddpp.com/api/v1"
  resolve_ip = "1.2.3.4"   # 可选，留空走 DNS
  ```
- 本地按 sha256 去重，同一张图只传一次；服务端也有秒传复用
- 归档记录写在 `image_archive` 表，面板「规则 → 图片识别 → 图片归档」可看总数与最近记录
- **发送改用图床直链**：腾讯侧转存成功率明显高于原始图源（图源常有防盗链，会报 40093007）
- 上传失败不影响发图，退回原链路

> 默认开启意味着群内图片会上传到配置的图床。README 与面板均有提示，可随时关闭。

## 文生图

和对话模型是**两套接口**，不能互相替代，但可以共存：聊天走 `/chat/completions`，
收到「画一张…」走 `/images/generations`。

```toml
[imagegen]
enabled = false
base_url = ""        # 留空继承 [llm]，同厂商时省事；跨厂商分别填
api_key = ""         # 留空继承 [llm]
model = ""           # 必填，例如 dall-e-3 / flux.1 / wanx-v1
size = "1024x1024"
prefix = ""          # 提示词前缀，例如「动漫风格，高清」
```

- 群里 @机器人 说「画一张赛博朋克城市夜景」即触发，不经过对话模型
- 返回 `url` 就让平台转存；返回 `b64_json` 就走二进制上传
- 需要独立的生图服务：多数对话网关**没有** images 接口，要另接厂商

## 网络找图

群友说「来张二次元图片」「来几张漫画图」「随机来张图片」时，机器人从图源找一张发到群里。
没指定类别时从已勾选的类别里随机挑一个；类别没勾选会直接说明，不交给模型猜。

```toml
[image]
search_enabled = false
max_bytes = 5242880   # 二进制直传平台上限 5MB
```

支持类别：二次元、动漫、漫画、插画、壁纸、头像、表情包、萌图、搞笑、沙雕、恐怖、风景、宠物、猫、狗、游戏、汽车、美食、美女。

`categories` 是**白名单**，只放行列表里的类别，默认只开安全项：

```toml
[image]
categories = ["二次元", "动漫", "漫画", "插画", "壁纸", "搞笑", "风景", "宠物", "游戏", "表情包"]
```

**必须勾选才生效，空数组表示全部禁止**。面板「规则 → 图片识别」用复选框维护，美女/恐怖/沙雕标 ⚠。

用户要了未放行的类别（比如「美女」）时，机器人会直接回「这类图没开，当前能发：…」，
不会把问题丢给大模型去瞎猜。

流程：`360 图片 JSON → 选第一张可用图 → /v2/groups/{g}/files 让平台转存 → msg_type=7 发送`。
URL 转存失败（850026）会自动退回二进制上传。

> ⚠️ 图源是公开搜索，**内容不受控**。平台对低俗、血腥内容审查严格，「美女」「恐怖」这类
> 关键词存在被平台判违规的风险，生产环境建议只保留二次元、搞笑、风景、宠物等安全类别。
> 若同时开启图片审核，机器人会审核自己发出的图。

## 回复时 @ 提问者

`[reply] at_user` 开关，默认关。

群聊里 @ 某人**只支持 markdown 消息**，所以开启后回复会以 `msg_type=2` 发出，内容前缀
`<qqbot-at-user id="member_openid" />`。markdown 发送失败会自动退回纯文本，不会因此丢回复。

```toml
[reply]
at_user = false
```

面板「设置 → 回复行为」可直接切换，改完立即生效。

## 验签实现

```
seed = AppSecret 反复翻倍补到 32 字节
回调校验: hex(Ed25519.sign(event_ts + plain_token))
事件验签: verify(sig, timestamp + raw_body)
```

已用官方测试向量校验（`tests/smoke.py` 内含断言）。

## 平台限制（实测）

| 限制 | 数值 |
|---|---|
| 撤回窗口 | 发出后 2 分钟 |
| 被动回复 | 5 分钟内、每条消息最多 5 次 |
| 主动消息 | Bot 60 qpm，单群 20 qpm、每天 1000 条 |
| @全体成员 | 每群每天 1 次（自然日），超限 `40034142` |
| 禁言 | 上限 30 天，仅普通成员 |
| 成员管理接口 | 需平台内邀，返回 `11253` |

## 目录

```
qqbot/          后端：配置 / 鉴权 / API / 验签 / 落库 / 事件 / webhook / 面板 / 规则 / 定时 / CLI
web/panel/      控制台：index.html + app.css + app.js + setup.html（原生，无构建）
web/landing.html 项目落地页
web/docs.html    文档页
docker/         entrypoint.sh
docs/shots/     README 用截图
deploy/         systemd 单元、nginx 片段
Dockerfile / docker-compose.yml
tests/smoke.py  离线测试：官方向量、落库、去重、cron、规则、调度
tools/deploy.py 一键部署（可选，含 SSH 上传）
PLAN.md         版本路线（P0 / P1 / P2）
```

## Star 提示

两套机制共存，默认走软提示：

| 机制 | 行为 | 配置 |
|---|---|---|
| 软提示 | 登录后弹窗，点「以后再说」按间隔再弹 | `soft_prompt` / `soft_interval_hours` |
| 硬门槛 | 未点 Star 直接阻断面板 | `require_star` / `star_user` / `github_token` |

```toml
[about]
repo = "cd1005-art/QQBOT"
soft_prompt = true          # 软提示总开关
soft_interval_hours = 24    # 间隔小时；0 = 只弹一次
require_star = false        # 硬门槛，开启后不再弹软提示
```

设置页「开源项目」卡片按身份与状态显示一行：

| 场景 | 显示 |
|---|---|
| 仓库作者本人使用（`star_user` 等于仓库 owner） | 弹窗提示：关闭 · 尊敬的作者您好，祝您身体健康，全家幸福安康 |
| 已点星的其他使用者 | 弹窗提示：关闭 · 感谢您的支持 |
| 未点星 | 弹窗提示：开启 · 间隔：24 小时 |

作者身份通过「`star_user` 是否等于仓库 owner」判断——GitHub 不允许给自己的仓库点星，用 star 数无法区分。
作者本人使用时软提示自动关闭。

## Star 门槛（可选，默认关闭）

```toml
[about]
repo = "cd1005-art/QQBOT"
require_star = false      # 开启后未点 Star 无法进入面板
star_user = ""            # 要校验的 GitHub 用户名
github_token = ""         # 必填，否则无法校验
```

**注意三点：**

1. GitHub 的 `/stargazers` 接口现在**要求鉴权**，不配 `github_token` 会返回 401，程序会 fail-open（放行）。
2. 自托管项目里这层门槛只能算仪式感——fork 后删掉即可绕过，离线部署也连不上 GitHub。
3. 你不能给自己仓库点 Star，所以 `star_user` 填自己的话会把自己锁在外面；改 `config.toml` 把 `require_star` 置 false 即可恢复。

## 许可

MIT

## 交流群

有问题、想提需求、想看后续更新，都可以进群：

**QQ 群：556269215**

[点此加群](https://qm.qq.com/cgi-bin/qm/qr?k=&jump_from=webapi&group_code=556269215)
