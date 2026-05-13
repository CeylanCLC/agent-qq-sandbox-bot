# Agent QQ Sandbox Bot

一个基于 **OpenClaw + NapCat OneBot + Python WebSocket + MiMo-V2.5** 的 QQ 群 AI Agent 桥接项目。

本项目可以把一个 QQ 账号接入 QQ 群，使其具备群内 @ 回复、关键词触发、私聊回复、定时巡群、任务模式、发送图片、上传群文件、群消息缓存、机器人自身发言记忆、MiMo-V2.5 视觉读图、群长期压缩记忆等能力。

项目适合用于学习：

- QQ Bot 接入
- OneBot 协议
- NapCat 部署
- WebSocket 长连接
- OpenAI-compatible API 调用
- OpenClaw Gateway
- MiMo-V2.5 多模态模型调用
- Linux 后台服务部署
- systemd 守护进程
- Docker 容器路径挂载
- 群聊上下文管理
- 简单 Agent 行为设计

---

## 1. 项目背景

本项目最初用于将 OpenClaw 的大模型能力接入 QQ 群，使机器人能够像普通群成员一样参与群聊。

机器人设定为“弹性 / 张智豪”，可以通过 `BOOTSTRAP.md` 维护长期人格设定。项目部署在 Ubuntu Server 上，NapCat 负责 QQ 登录和 OneBot 协议事件上报，Python 桥接脚本负责接收 QQ 消息、判断触发条件、调用模型接口，再把回复发送回 QQ 群或私聊。

后来项目逐步扩展了：

- 定时巡群
- 群消息缓存
- 机器人自身发言缓存
- 发图与上传群文件
- MiMo-V2.5 视觉读图
- 群长期压缩记忆
- 任务模式
- systemd 后台运行
- 日志排查与异常恢复

---

## 2. 功能特性

### 2.1 基础对话能力

- 支持 QQ 群内 @ 机器人后自动回复
- 支持关键词触发，例如“弹性”“张智豪”“茶”
- 支持私聊回复
- 支持读取 OpenClaw workspace 中的 `BOOTSTRAP.md` 作为人格设定
- 支持通过 OpenClaw Gateway 调用文本模型
- 支持通过 MiMo-V2.5 直连接口处理图片理解请求

### 2.2 群聊增强能力

- 支持定时巡群
- 支持主动判断某个群是否值得插话
- 支持群消息缓存
- 支持机器人自身发言写入缓存，避免重复说相似内容
- 支持群长期压缩记忆
- 支持过滤无效回复，例如 `SILENCE`、OpenClaw 后台提示、初始化废话等
- 支持避免机器人自己触发自己

### 2.3 视觉读图能力

- 支持私聊直接发图并读图
- 支持私聊先发图，再发送“看图”“这图是什么”等追问
- 支持群里先发图，再 @ 机器人问图
- 支持群里关键词触发读图
- 支持图片转 `data_url` 后直连 MiMo-V2.5 视觉模型
- 支持群图片缓存和私聊图片缓存
- 支持等待图片事件，缓解 QQ/NapCat 图文拆分导致的顺序问题

### 2.4 文件与图片能力

- 支持发送群图片
- 支持上传群文件
- 支持通过宿主机目录和 NapCat 容器目录挂载共享文件

### 2.5 运维能力

- 支持 systemd 后台运行
- 支持服务异常后自动重启
- 支持 WebSocket 断线重连
- 支持 journalctl 日志排查
- 支持 OpenClaw HTTP API 调用异常处理
- 支持 MiMo Vision API 调用异常处理

---

## 3. 总体架构

```text
QQ 群 / QQ 私聊
      |
      v
NapCat OneBot
      |
      | WebSocket 事件上报
      | ws://127.0.0.1:3001
      v
qq_openclaw_bridge.py
      |
      | 解析消息
      | - 文本
      | - @
      | - 图片
      | - 表情
      | - 私聊
      | - 群聊
      v
触发判断
      |
      | - @ 触发
      | - 关键词触发
      | - 私聊触发
      | - 发图命令
      | - 发文件命令
      | - 任务模式
      | - 定时巡群
      v
模型调用路由
      |
      | 纯文本消息
      | -> OpenClaw Gateway
      |
      | 带图消息
      | -> 图片转 data_url
      | -> 直连 MiMo-V2.5
      v
模型回复
      |
      v
通过 OneBot API 发回 QQ 群 / 私聊
```

---

## 4. 项目结构

```text
agent-qq-sandbox-bot/
├── qq_openclaw_bridge.py        # 主程序，负责 QQ 消息桥接和模型调用
├── README.md                    # 项目说明文档
├── requirements.txt             # Python 依赖
├── .gitignore                   # Git 忽略规则，避免上传敏感信息
├── share/                       # 宿主机与 NapCat 容器共享目录
├── docs/images/                 # README 演示截图，可选
└── BOOTSTRAP.md.save            # 人格设定示例，可选
```

注意：

```text
share/ 目录用于运行时发图、发文件，不建议提交里面的真实文件。
periodic_state.json 是运行时状态文件，不应提交。
group_memory_state.json 是长期记忆文件，不应提交。
docker-compose.yml 可能包含账号或本地路径，不应提交。
```

---

## 5. 核心文件说明

### 5.1 `qq_openclaw_bridge.py`

项目主程序，负责：

- 连接 NapCat OneBot WebSocket
- 接收 QQ 群聊和私聊事件
- 解析消息内容
- 判断触发条件
- 记录群消息缓存
- 记录机器人自身发言
- 管理群图片缓存
- 管理私聊图片缓存
- 调用 OpenClaw Gateway
- 调用 MiMo-V2.5 视觉模型
- 定时巡群
- 群长期记忆压缩
- 发图
- 上传群文件
- 任务模式执行

### 5.2 `requirements.txt`

Python 依赖：

```text
httpx
websockets
```

### 5.3 `.gitignore`

用于避免上传敏感文件，例如：

```text
venv/
__pycache__/
*.pyc
*.log
.env
.env.*
*.token
*.key
periodic_state.json
group_memory_state.json
napcat/
ntqq/
docker-compose.yml
share/*
!share/.gitkeep
*.bak
*.bak.*
qq_openclaw_bridge.py.bak*
```

---

## 6. 工作原理

### 6.1 NapCat 的作用

NapCat 负责登录 QQ，并提供 OneBot 协议接口。

常用接口：

```text
OneBot HTTP API:
http://127.0.0.1:3000

OneBot WebSocket:
ws://127.0.0.1:3001
```

桥接脚本通过：

```text
ws://127.0.0.1:3001
```

接收实时 QQ 消息事件。

### 6.2 Python Bridge 的作用

`qq_openclaw_bridge.py` 是中间桥接层。

它负责：

```text
QQ 消息事件
  -> 解析消息内容
  -> 判断是否触发机器人
  -> 构造模型上下文
  -> 调用模型
  -> 发送回复到 QQ
```

### 6.3 OpenClaw 的作用

OpenClaw Gateway 负责处理纯文本模型调用。

默认接口：

```text
http://127.0.0.1:29306/v1/chat/completions
```

### 6.4 MiMo-V2.5 的作用

MiMo-V2.5 用于处理带图片的视觉理解请求。

由于 OpenClaw Gateway 在部分情况下不能稳定透传多模态 `image_url`，所以项目采用：

```text
纯文本消息 -> OpenClaw Gateway
带图消息 -> 直连 MiMo-V2.5
```

这种双通道方式。

---

## 7. 主程序模块逻辑

### 7.1 配置模块

主要配置包括：

```python
ONEBOT_WS = "ws://127.0.0.1:3001"
OPENCLAW_URL = "http://127.0.0.1:29306/v1/chat/completions"

HOST_SHARE_DIR = "/root/openclaw-qq/share"
CONTAINER_SHARE_DIR = "/share"

TRIGGER_WORDS = ["弹性", "张智豪", "茶"]
```

管理员 QQ 号不建议写死在代码里，推荐用环境变量：

```bash
ADMIN_QQ_IDS=123456789
```

OpenClaw Gateway token 也推荐用环境变量：

```bash
OPENCLAW_GATEWAY_TOKEN=your_token_here
```

MiMo Vision API Key 也必须用环境变量：

```bash
MIMO_VISION_API_KEY=your_api_key_here
```

不要把真实 token、API Key、QQ 号、群号提交到 GitHub。

---

### 7.2 人格设定模块

机器人会尝试读取：

```text
/root/.openclaw/workspace/BOOTSTRAP.md
```

这个文件用于保存“弹性 / 张智豪”的人格设定，例如：

- 名字
- 身份
- 说话风格
- 禁止提及后台初始化
- 群聊发言风格
- 文言/半文言语气

如果 `BOOTSTRAP.md` 不存在，脚本会使用兜底人格提示词，避免机器人在群里说自己没有身份。

---

### 7.3 消息解析模块

NapCat 上报的 OneBot 消息可能包含：

```text
text    文本消息
at      @消息
image   图片消息
face    表情消息
reply   引用回复
```

脚本会解析：

- 是否 @ 机器人
- 文本内容
- 图片 URL
- 表情占位
- 群 ID
- 用户 ID
- 发送者昵称

图片消息会被转换成：

```text
[图片]
```

同时保存图片 URL，用于后续视觉模型读取。

---

### 7.4 触发逻辑模块

群聊中主要有三种触发方式。

#### 1. @ 触发

示例：

```text
@弹性 你是谁
```

机器人会回复，并且回复时 @ 对方。

#### 2. 关键词触发

默认关键词：

```text
弹性
张智豪
茶
```

示例：

```text
弹性出来
张智豪何在
这茶不错
```

关键词触发时，机器人不会 @ 对方。

#### 3. 私聊触发

私聊机器人时，默认会直接回复。

---

### 7.5 主动回复上下文模块

普通 @ 和关键词触发时，机器人不仅看当前这句话，还会读取：

```text
群长期压缩记忆
+
当前群最近若干条消息
+
普通对话历史
+
人格设定
```

这样可以让机器人：

- 理解群聊上下文
- 记住自己刚刚说过什么
- 避免重复回复
- 更像真实群友
- 不每次都从零开始回答

---

### 7.6 群消息缓存模块

脚本会为每个群维护一个消息缓存：

```python
group_message_cache = defaultdict(lambda: deque(maxlen=GROUP_CACHE_MAX_MESSAGES))
```

默认每个群最多保留：

```text
250 条最近消息
```

缓存内容包括：

```text
seq          消息序号
time         时间
user_id      用户 ID
nickname     用户昵称
text         文本内容
image_urls   图片 URL 列表
is_bot       是否为机器人自己
```

作用：

- 给主动回复提供上下文
- 给定时巡群提供判断依据
- 让机器人知道自己刚才说过什么
- 支持图片追问
- 支持长期记忆压缩

---

### 7.7 机器人自身发言缓存模块

机器人自己发出去的话也会写入群缓存。

日志示例：

```text
[缓存机器人消息] group=xxx seq=123 text=...
```

这一步很重要。否则机器人不知道自己刚刚说过什么，容易重复回复。

---

## 8. MiMo-V2.5 视觉读图能力

### 8.1 为什么要直连 MiMo

OpenClaw Gateway 在部分情况下不能稳定透传多模态 `image_url` 内容，因此本项目采用双通道：

```text
纯文本消息：
QQ / NapCat
  -> Python Bridge
  -> OpenClaw Gateway
  -> 文本模型回复

带图片消息：
QQ / NapCat
  -> Python Bridge
  -> 图片转 data_url
  -> 直连 MiMo-V2.5
  -> 视觉模型回复
```

### 8.2 什么时候会读图

以下情况会触发视觉理解：

```text
1. 私聊直接发送图片
2. 私聊先发图片，再发送“看图”“这图是什么”
3. 群里当前消息带图片，并且 @ 机器人或包含关键词
4. 群里先发图片，再发送“@弹性 这图是什么”
5. 群里先发图片，再发送“弹性，这图讲了啥”
6. 定时巡群时，新增消息中包含图片且模型判断值得接话
```

群聊中推荐最稳定用法：

```text
先发图片
再发：@弹性 这图是什么
```

原因是 QQ/NapCat 有时会把“文字”和“图片”拆成两条事件。

---

### 8.3 图文拆分问题

你在 QQ 客户端里看到的是：

```text
@弹性 这图是什么 + 图片
```

但 NapCat 可能实际上上报为：

```text
第一条：@弹性 这图是什么 images=0
第二条：[图片] images=1
```

如果第一条先触发，图片还没进缓存，机器人可能会说“没看到图”。

为缓解这个问题，脚本加入了：

```text
IMAGE_WAIT_AFTER_TEXT_SECONDS
RECENT_IMAGE_MAX_AGE_SECONDS
```

也就是：问图时如果暂时没找到图片，会等一小会儿再找一次；同时只取最近一定时间内的图片，避免拿很久以前的图乱答。

---

### 8.4 图片缓存机制

项目不会永久保存图片文件本体。

图片处理流程：

```text
NapCat 收到图片
  -> 提取图片 URL
  -> 写入群图片缓存 / 私聊图片缓存
  -> 需要读图时下载图片
  -> 转成 base64 data_url
  -> 发送给 MiMo-V2.5 视觉模型
```

相关缓存：

```text
group_image_cache       群聊最近图片缓存，内存缓存
private_image_cache     私聊最近图片缓存，内存缓存
group_message_cache     群消息缓存，也会保存 image_urls 字段
```

注意：

```text
图片文件本体不落盘保存。
图片专用缓存主要存在内存里。
服务重启后，图片专用缓存会丢失。
```

---

### 8.5 视觉相关环境变量

建议通过 systemd drop-in 配置，不要写死在代码里：

```ini
[Service]
Environment=IMAGE_SEND_MODE=data_url
Environment=MAX_IMAGES_PER_MESSAGE=1
Environment=MAX_CONTEXT_IMAGES=1
Environment=MAX_IMAGE_DOWNLOAD_BYTES=4194304
Environment=VISION_IMAGE_URL_FORMAT=object
Environment=USE_DIRECT_MIMO_VISION=1
Environment=MIMO_VISION_BASE_URL=https://your-mimo-base-url/v1
Environment=MIMO_VISION_API_KEY=your_api_key
Environment=MIMO_VISION_MODEL=mimo-v2.5
Environment=IMAGE_WAIT_AFTER_TEXT_SECONDS=2.0
Environment=RECENT_IMAGE_MAX_AGE_SECONDS=180
```

其中：

```text
IMAGE_SEND_MODE=data_url
表示先下载图片，再转成 base64 data_url 发给视觉模型，更稳定但更耗带宽。

MAX_IMAGES_PER_MESSAGE=1
单次请求最多带 1 张图，避免请求体过大。

MAX_CONTEXT_IMAGES=1
用户追问“这图是什么”时，只补最近 1 张图。

MAX_IMAGE_DOWNLOAD_BYTES=4194304
单张图最大下载 4MB。

VISION_IMAGE_URL_FORMAT=object
使用 OpenAI-compatible 的 image_url 对象格式。

USE_DIRECT_MIMO_VISION=1
带图消息绕过 OpenClaw，直连 MiMo。

MIMO_VISION_MODEL=mimo-v2.5
使用 MiMo-V2.5 视觉模型。
```

---

## 9. 群长期压缩记忆

除了每个群保留最近 250 条原始消息外，本项目还支持“群长期压缩记忆”。

### 9.1 设计目标

```text
保留最近 250 条原始消息
同时把更长时间范围内的聊天压缩成摘要
普通回复时只带“长期摘要 + 最近少量消息”
减少输入 token 消耗
让机器人记住更久的群聊背景
```

### 9.2 记忆结构

```text
group_message_cache
  每个群最近 250 条原始消息，内存缓存。

group_memory_summary
  每个群一份长期摘要，压缩保存。

group_memory_state.json
  长期记忆落盘文件，默认不提交 GitHub。
```

### 9.3 压缩策略

默认参数：

```text
ACTIVE_GROUP_CONTEXT_LIMIT=25
GROUP_MEMORY_COMPACT_INTERVAL_SECONDS=18000
GROUP_MEMORY_MIN_NEW_MESSAGES=80
GROUP_MEMORY_RAW_LIMIT=140
GROUP_MEMORY_MAX_CHARS=1800
GROUP_MEMORY_CONTEXT_MAX_CHARS=1200
```

含义：

```text
普通回复只携带最近 25 条原始群消息
每 5 小时尝试压缩一次长期记忆
某个群新增满 80 条消息才触发压缩
单次最多压缩 140 条新消息
每个群长期摘要最多 1800 字
每次回复最多带 1200 字长期摘要
```

### 9.4 长期记忆相关环境变量

```ini
[Service]
Environment=ACTIVE_GROUP_CONTEXT_LIMIT=25
Environment=GROUP_MEMORY_COMPACT_INTERVAL_SECONDS=18000
Environment=GROUP_MEMORY_MIN_NEW_MESSAGES=80
Environment=GROUP_MEMORY_RAW_LIMIT=140
Environment=GROUP_MEMORY_MAX_CHARS=1800
Environment=GROUP_MEMORY_CONTEXT_MAX_CHARS=1200
```

### 9.5 手动生成长期记忆

```bash
cd /root/openclaw-qq

PYTHONPATH=/root/openclaw-qq /root/openclaw-qq/venv/bin/python - <<'PY'
import asyncio
import qq_openclaw_bridge as b

b.load_periodic_state()
b.load_group_memory_state()

async def main():
    group_ids = list(b.group_message_cache.keys())
    print("groups:", group_ids)

    for gid in group_ids:
        ok = await b.compact_group_memory_once(gid, force=True)
        print("compact", gid, ok)

asyncio.run(main())
PY
```

查看长期记忆文件：

```bash
ls -lh /root/openclaw-qq/group_memory_state.json
```

查看摘要内容：

```bash
python3 - <<'PY'
import json

p = "/root/openclaw-qq/group_memory_state.json"
data = json.load(open(p))

for gid, text in data.get("group_memory_summary", {}).items():
    print("GROUP", gid)
    print(text[:800])
    print("-" * 60)
PY
```

---

## 10. 定时巡群

定时巡群是本项目的核心功能之一。

默认逻辑：

```text
每隔 N 秒
  -> 遍历已缓存的群
  -> 读取上次巡群后新增消息
  -> 判断群友新增消息数量
  -> 不足阈值：继续累计
  -> 达到阈值：交给模型判断是否接话
```

典型日志：

```text
[定时巡群] group=xxx no-new-message
[定时巡群] group=xxx human_new_count=1, total_new_count=1, accumulate
[定时巡群] group=xxx silence
[定时巡群发言] group=xxx text=...
```

字段含义：

```text
human_new_count：新增的群友消息数量
total_new_count：新增总消息数量，包括机器人自身消息
accumulate：消息数量不够，继续累计
no-new-message：没有新增消息
silence：模型判断不适合插话
定时巡群发言：机器人主动接话
```

机器人自身消息只作为上下文，不参与凑触发门槛，避免机器人自己触发自己。

---

## 11. SILENCE 与 bad-answer

定时巡群时，模型不一定每次都应该说话。

如果模型判断不该插话，就应该输出：

```text
SILENCE
```

脚本检测到 `SILENCE` 后不会发群。

`bad-answer` 表示模型返回了不适合发到群里的内容，例如：

```text
BOOTSTRAP.md is missing
OpenClaw 初始化提示
No response from OpenClaw
workspace 相关后台提示
```

这些内容会被脚本拦截，不会发到 QQ 群里。

---

## 12. 任务模式

任务模式用于执行复杂、多步骤任务。

示例：

```text
弹性任务：帮我分三步分析这个方案。
```

或者：

```text
@弹性 任务：帮我写一个群公告，先列思路，再写初稿，再润色。
```

任务模式通常会多轮调用模型，直到模型输出完成标记，或达到最大执行轮数。

任务模式建议只允许管理员触发，避免群友滥用消耗 token。

管理员通过环境变量配置：

```ini
[Service]
Environment=ADMIN_QQ_IDS=your_qq_id
```

---

## 13. 发图功能

项目支持从服务器共享目录发送图片到 QQ 群。

图片应放到：

```text
/root/openclaw-qq/share/
```

群内命令：

```text
弹性发图：test.png
```

或者：

```text
@弹性 发图：test.png
```

NapCat 容器内看到的路径通常不是宿主机路径，而是挂载后的容器路径。例如：

```text
宿主机路径：/root/openclaw-qq/share/test.png
容器内路径：/share/test.png
```

如果路径写错，可能出现：

```text
rich media transfer failed
```

---

## 14. 上传群文件

文件同样放到：

```text
/root/openclaw-qq/share/
```

群内命令：

```text
弹性发文件：report.docx
```

上传成功后，机器人会调用 OneBot 的群文件上传接口，把文件发送到群文件中。

---

## 15. 运行环境

推荐环境：

```text
Ubuntu Server 22.04 LTS
Python 3.10+
Docker / Docker Compose
NapCat OneBot
OpenClaw Gateway
MiMo-V2.5 API
```

安装 Python 依赖：

```bash
cd /root/openclaw-qq

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

如果创建 venv 报错，需要安装：

```bash
apt install -y python3-venv python3-full
```

---

## 16. NapCat 配置

NapCat 负责 QQ 登录和 OneBot 协议接入。

常用端口：

```text
3000：OneBot HTTP API
3001：OneBot WebSocket
6099：NapCat WebUI / 登录相关端口
```

桥接脚本默认连接：

```text
ws://127.0.0.1:3001
```

如果使用 Docker 部署 NapCat，建议挂载共享目录：

```yaml
volumes:
  - ./share:/share
```

这样宿主机的：

```text
/root/openclaw-qq/share/
```

就能映射到容器内：

```text
/share/
```

---

## 17. OpenClaw Gateway 配置

脚本默认调用：

```text
http://127.0.0.1:29306/v1/chat/completions
```

测试 OpenClaw Gateway 是否可用：

```bash
TOKEN=$(python3 - <<'PY'
import json
print(json.load(open("/root/.openclaw/openclaw.json"))["gateway"]["auth"]["token"])
PY
)

curl -i http://127.0.0.1:29306/v1/models \
  -H "Authorization: Bearer $TOKEN"
```

如果返回模型列表，说明 Gateway 可用。

测试聊天接口：

```bash
curl -i http://127.0.0.1:29306/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw/default",
    "messages": [
      {
        "role": "user",
        "content": "你好，测试一下"
      }
    ],
    "stream": false
  }'
```

---

## 18. 启动项目

前台运行：

```bash
cd /root/openclaw-qq

source venv/bin/activate
python qq_openclaw_bridge.py
```

看到：

```text
连接 NapCat OneBot: ws://127.0.0.1:3001
已连接 NapCat OneBot
```

说明桥接脚本已经连接到 NapCat。

---

## 19. systemd 后台运行

创建 systemd 服务：

```bash
cat > /etc/systemd/system/qq-openclaw-bridge.service <<'EOF'
[Unit]
Description=QQ OpenClaw Bridge
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/openclaw-qq
ExecStart=/root/openclaw-qq/venv/bin/python /root/openclaw-qq/qq_openclaw_bridge.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

启动服务：

```bash
systemctl daemon-reload
systemctl enable --now qq-openclaw-bridge
```

查看状态：

```bash
systemctl status qq-openclaw-bridge --no-pager
```

实时日志：

```bash
journalctl -u qq-openclaw-bridge -f --full
```

只看关键日志：

```bash
journalctl -u qq-openclaw-bridge -f --full \
  | grep --line-buffered -E "连接 NapCat|已连接|缓存群消息|缓存机器人消息|收到@|收到关键词|群聊补图|视觉请求|直连MiMo视觉|定时巡群|回复"
```

---

## 20. 推荐 systemd 配置拆分

推荐把不同配置拆成多个 drop-in 文件，避免覆盖 API Key 或运行参数：

```text
/etc/systemd/system/qq-openclaw-bridge.service.d/
├── override.conf        # 管理员 QQ、MiMo API Key、OpenClaw token 等敏感配置
├── memory.conf          # 长期记忆参数
└── vision-stable.conf   # 视觉读图稳定性参数
```

### 20.1 `override.conf` 示例

```ini
[Service]
Environment=ADMIN_QQ_IDS=your_qq_id
Environment=OPENCLAW_GATEWAY_TOKEN=your_openclaw_gateway_token
Environment=USE_DIRECT_MIMO_VISION=1
Environment=MIMO_VISION_BASE_URL=https://your-mimo-base-url/v1
Environment=MIMO_VISION_API_KEY=your_mimo_api_key
Environment=MIMO_VISION_MODEL=mimo-v2.5
```

### 20.2 `memory.conf` 示例

```ini
[Service]
Environment=ACTIVE_GROUP_CONTEXT_LIMIT=25
Environment=GROUP_MEMORY_COMPACT_INTERVAL_SECONDS=18000
Environment=GROUP_MEMORY_MIN_NEW_MESSAGES=80
Environment=GROUP_MEMORY_RAW_LIMIT=140
Environment=GROUP_MEMORY_MAX_CHARS=1800
Environment=GROUP_MEMORY_CONTEXT_MAX_CHARS=1200
```

### 20.3 `vision-stable.conf` 示例

```ini
[Service]
Environment=IMAGE_SEND_MODE=data_url
Environment=MAX_IMAGES_PER_MESSAGE=1
Environment=MAX_CONTEXT_IMAGES=1
Environment=MAX_IMAGE_DOWNLOAD_BYTES=4194304
Environment=VISION_IMAGE_URL_FORMAT=object
Environment=IMAGE_WAIT_AFTER_TEXT_SECONDS=2.0
Environment=RECENT_IMAGE_MAX_AGE_SECONDS=180
```

修改后执行：

```bash
systemctl daemon-reload
systemctl restart qq-openclaw-bridge
```

查看环境变量是否生效：

```bash
systemctl show qq-openclaw-bridge -p Environment \
  | tr ' ' '\n' \
  | grep -E "MIMO|IMAGE|GROUP_MEMORY|ACTIVE_GROUP"
```

不要把包含真实 API Key 的输出贴到公开地方。

---

## 21. 常见使用命令

### 21.1 @ 回复

```text
@弹性 你是谁
```

### 21.2 关键词触发

```text
弹性出来
张智豪何在
这茶不错
```

### 21.3 私聊

```text
你好
```

### 21.4 私聊读图

```text
直接发送图片
```

或者：

```text
先发图片
再发：看图
```

### 21.5 群聊读图

推荐：

```text
先发图片
再发：@弹性 这图是什么
```

或者：

```text
先发图片
再发：弹性，这图讲了啥
```

### 21.6 任务模式

```text
弹性任务：帮我分三步分析这个方案。
```

### 21.7 发送图片

```text
弹性发图：test.png
```

### 21.8 上传群文件

```text
弹性发文件：report.docx
```

---

## 22. 日志说明

常见日志含义如下：

```text
[缓存群消息]
表示脚本收到并缓存了一条群友消息。

[缓存机器人消息]
表示机器人自己的发言已经写入缓存。

[收到@]
表示有人 @ 机器人。

[收到关键词]
表示有人发送关键词触发消息。

[群图片缓存]
表示群里的图片 URL 已经进入图片缓存。

[群聊补图]
表示用户问图时，脚本从缓存里找到了最近图片。

[群聊等图]
表示用户先发了问图文本，但图片事件还没到，脚本等待一小会儿再找图。

[群聊无图可补]
表示用户在问图，但缓存里没有可用图片。

[视觉请求]
表示即将把图片发送给视觉模型。

[直连MiMo视觉]
表示带图请求绕过 OpenClaw，直接调用 MiMo-V2.5。

[回复]
表示模型返回了回复，脚本准备发回 QQ。

[定时巡群]
表示巡群任务正在检查某个群。

[定时巡群发言]
表示巡群判断后，机器人主动向群里发送了一句话。

[群长期记忆已更新]
表示某个群的长期压缩记忆已经更新。
```

---

## 23. 常见问题排查

### 23.1 机器人不回消息

检查服务：

```bash
systemctl status qq-openclaw-bridge --no-pager
```

检查日志：

```bash
journalctl -u qq-openclaw-bridge -n 200 --no-pager --full
```

检查 NapCat WebSocket 是否可用：

```bash
ss -lntp | grep 3001
```

---

### 23.2 OpenClaw HTTP 401

通常是 token 没传或 token 不对。

检查 OpenClaw 配置：

```bash
grep -n '"auth"' -A10 /root/.openclaw/openclaw.json
```

或者设置环境变量：

```ini
Environment=OPENCLAW_GATEWAY_TOKEN=your_token_here
```

---

### 23.3 OpenClaw HTTP 404

通常是 OpenClaw Gateway endpoint 没开启，或者接口路径写错。

需要确认：

```text
/v1/chat/completions
```

已经可用。

---

### 23.4 图片读不出来

先看日志：

```bash
journalctl -u qq-openclaw-bridge -n 200 --no-pager --full \
  | grep -E "图片段|群图片缓存|群聊补图|群聊无图可补|视觉请求|直连MiMo视觉|MiMo Vision"
```

判断方法：

```text
有 [群图片缓存]
说明 NapCat 收到了图片。

有 [群聊补图]
说明问图时找到了缓存图片。

有 [视觉请求]
说明图片已经准备传给视觉模型。

有 [直连MiMo视觉]
说明已经调用 MiMo-V2.5。

没有 [群图片缓存]
说明 NapCat 没上报可用图片 URL。

有 [群聊无图可补]
说明用户问图时，缓存里没有可用的近期图片。

有 MiMo Vision HTTP 错误
说明 MiMo API Key、Base URL、模型名或请求格式可能有问题。
```

群聊中最稳定测试方式：

```text
先发图片
再发：@弹性 这图是什么
```

---

### 23.5 发图失败 rich media transfer failed

常见原因：

```text
1. 图片路径写成了宿主机路径，而不是容器内路径
2. 图片文件不存在
3. NapCat 没有权限读取图片
4. 图片格式或大小异常
```

检查容器内路径：

```bash
docker exec napcat sh -lc "ls -l /share"
```

---

### 23.6 定时巡群不运行

查看是否有巡群日志：

```bash
journalctl -u qq-openclaw-bridge -n 300 --no-pager --full | grep "定时巡群"
```

如果服务还在收消息但没有巡群日志，可能是巡群 task 异常退出。可以看：

```bash
journalctl -u qq-openclaw-bridge -n 300 --no-pager --full \
  | grep -E "Task exception|NameError|定时巡群内部错误|定时巡群主循环错误"
```

---

### 23.7 群里消息很多但日志显示 new_count 少

脚本只能统计它上线之后通过 NapCat 收到并缓存的消息，不会自动读取 QQ 历史消息。

如果脚本没有在线，或者 NapCat 没上报，那些消息不会进入缓存。

---

---

## 24. MiMo 视觉读图故障排查补充

### 24.1 偶发 ASCII 编码错误

如果日志中出现类似：

    吾调用模型失利：'ascii' codec can't encode characters in position ...

或者：

    MIMO_VISION_API_KEY 含非 ASCII 字符

通常不是 NapCat 没收到图片，也不是图片缓存失败，而是 MiMo 视觉直连接口的请求中存在非 ASCII 字符，常见来源包括：

1. systemd 环境变量里存在多个 MIMO_VISION_API_KEY 来源
2. 某个旧配置文件中残留了中文占位符
3. API Key 前后混入隐藏字符
4. 请求体中包含中文、emoji、特殊昵称，被某些 OpenAI-compatible 网关按 ASCII 编码处理

当前项目已将 MiMo Vision 请求改为 ASCII-safe JSON：

    json.dumps(payload, ensure_ascii=True).encode("utf-8")

这样中文、emoji、特殊昵称会被转义为 Unicode 形式，能减少编码类错误。

### 24.2 检查运行中的 MiMo Key

不要直接打印 API Key。推荐只检查长度和编码：

    PID=$(systemctl show -p MainPID --value qq-openclaw-bridge)

    PID="$PID" python3 - <<'PY'
    import os
    from pathlib import Path

    pid = os.environ["PID"]
    items = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")

    vals = [
        x.split(b"=", 1)[1]
        for x in items
        if x.startswith(b"MIMO_VISION_API_KEY=")
    ]

    print("MIMO_VISION_API_KEY count:", len(vals))

    for i, v in enumerate(vals):
        try:
            v.decode("ascii")
            ascii_ok = True
        except UnicodeDecodeError:
            ascii_ok = False

        print(f"#{i}: len={len(v)} ascii={ascii_ok}")
    PY

理想结果：

    MIMO_VISION_API_KEY count: 1
    #0: len=正常长度 ascii=True

如果 count 大于 1，说明有多个配置来源；如果 ascii=False，说明 key 中有非 ASCII 脏字符。

### 24.3 推荐的 MiMo Key 管理方式

建议把 MiMo API Key 单独放到本地文件：

    /root/openclaw-qq/mimo-secret.env

文件内容：

    MIMO_VISION_API_KEY=your_real_api_key

systemd 中使用：

    [Service]
    EnvironmentFile=/root/openclaw-qq/mimo-secret.env

不要把 mimo-secret.env、model.env 或任何 .env 文件提交到 GitHub。


## 24. 安全注意事项

不要提交以下内容到 GitHub：

```text
QQ 登录态
NapCat runtime 文件
ntqq/
napcat/
docker-compose.yml
periodic_state.json
group_memory_state.json
.env
OpenClaw token
MiMo API Key
真实 QQ 号
真实群号
服务器公网 IP
share/ 目录里的临时文件
venv/
日志文件
备份文件
```

提交前建议扫描：

```bash
git grep --cached -n -E "QQ号|群号|公网IP|sk-|tp-|token|auth|MIMO_VISION_API_KEY|OPENCLAW_GATEWAY_TOKEN" || true
```

如果扫描结果只出现变量名或安全说明，一般可以提交。  
如果出现真实 API Key、QQ 号、群号或服务器 IP，应先删除或改成环境变量。

---

## 25. Demo Screenshots

截图建议放在：

```text
docs/images/
```

README 中可以引用：

```markdown
![Periodic group review logs](docs/images/demo-periodic-log.png)

![Bot profile history](docs/images/demo-profile-history.png)

![Hardware context reply 1](docs/images/demo-hardware-context-1.png)

![Hardware context reply 2](docs/images/demo-hardware-context-2.png)

![Memory context](docs/images/demo-memory-context.png)

![Active reply](docs/images/demo-active-reply.png)
```

---

## 26. 适合学习的知识点

通过这个项目可以学习：

```text
Linux 服务部署
systemd 守护进程
journalctl 日志排查
Docker 容器路径挂载
OneBot 协议
NapCat QQ 接入
WebSocket 长连接
HTTP API 调用
OpenAI-compatible Chat Completions
OpenClaw Gateway
MiMo-V2.5 多模态调用
异步 Python
asyncio
群消息缓存
图片 URL 缓存
长期记忆压缩
定时任务
简单 Agent 行为设计
AI 群聊上下文管理
```

---

## 27. 项目定位

这个项目不是一个完整的商业级机器人平台，而是一个面向学习、自用和技术验证的 QQ 群 AI Agent 桥接工程。

它的重点是：

```text
把 QQ 消息接入 OpenClaw
让模型具备群聊上下文
让机器人能定时巡群
让机器人能读图
让机器人能记住更长的群聊背景
让机器人能像群友一样自然发言
让整个服务能在 Linux 服务器上长期运行
```

后续可以继续扩展：

```text
Web 管理后台
更完整的权限系统
数据库持久化
Redis 缓存
多机器人账号支持
多模型路由
群配置热更新
长期记忆精简
更强的 Agent 工具调用
沙箱执行环境
```

---

## 28. License

本项目仅供学习、自用和技术交流。

使用本项目时，请遵守 QQ、NapCat、OpenClaw、MiMo 以及相关平台的使用规范。