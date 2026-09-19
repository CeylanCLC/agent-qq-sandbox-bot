# 配置参考

## 品牌和人物

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SITE_DISPLAY_NAME` | `AI Bot Console` | 网站控制台显示名称 |
| `SITE_TAGLINE` | `BOT OPERATIONS` | 控制台副标题 |
| `BOT_DISPLAY_NAME` | `QQ AI 助手` | 机器人显示名称 |
| `BOT_PERSONA_NAME` | 跟随显示名称 | 人物名称 |
| `BOT_PERSONA` | 通用友善助手 | 系统人物设定 |
| `BOT_TRIGGER_WORDS` | 显示名称 | 逗号分隔群聊触发词 |
| `BOT_NODE_LABEL` | `机器人节点` | 控制台节点说明 |

## 链路

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ONEBOT_WS` | `ws://127.0.0.1:3001` | OneBot WebSocket |
| `ONEBOT_ACCESS_TOKEN` | 空 | OneBot 鉴权 |
| `OPENCLAW_URL` | 本机兼容接口 | OpenClaw Chat Completions 地址 |
| `OPENCLAW_GATEWAY_TOKEN` | 无 | Gateway token；也可从配置读取 |
| `CEYLAN_OPENCLAW_CONFIG` | OpenClaw 常见路径 | OpenClaw 配置路径 |
| `CEYLAN_ONEBOT_CONFIG` | 自动发现 | NapCat OneBot 配置路径 |

## 隐私与权限

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BLOCKED_GROUP_IDS` | 空 | 逗号分隔；永不上传具体 ID |
| `ADMIN_QQ_IDS` | 空 | 可触发任务模式的账号 |
| `CEYLAN_BOT_TOKEN` | 无 | 控制面共享随机令牌 |

## 控制台地址

推荐只设置 `BOT_CONTROL_BASE_URL=https://你的域名`。也可分别覆盖 `CEYLAN_HEARTBEAT_URL`、`CEYLAN_TELEMETRY_URL`、`CEYLAN_CONTROL_URL`、`CEYLAN_KEYS_URL` 和 `CEYLAN_FUN_URL`。

URL 必须是普通 URL，例如 `https://example.com/api/bot/status`，不能写成 Markdown 链接 `[https://...](https://...)`。

## 上下文和资源

常用变量包括 `MAX_HISTORY`、`ACTIVE_GROUP_CONTEXT_LIMIT`、`PERIODIC_REVIEW_INTERVAL_SECONDS`、`GROUP_MEMORY_COMPACT_INTERVAL_SECONDS`、`GROUP_MEMORY_MIN_NEW_MESSAGES`、`CEYLAN_MODEL_CONCURRENCY` 和 `CEYLAN_MAX_PENDING`。数值越大不一定越好；先使用示例默认值并观察模型延迟与 Token 用量。

## 视觉

`USE_DIRECT_MIMO_VISION=1` 时使用 `MIMO_VISION_BASE_URL`、`MIMO_VISION_API_KEY` 和 `MIMO_VISION_MODEL`。不开启时，图片可交由 OpenClaw 统一处理。任何 Key 都只能放在机器人主机的受保护环境文件中。
