# 安全策略

## 不应提交的内容

API Key、Gateway token、后台密码、Cookie、私钥、环境文件、SQLite 数据库、NapCat 登录态、聊天记录、群记忆状态、真实群号和服务器地址。

如果秘密曾进入 Git 历史，仅从最新提交删除不够；应立即在服务商侧吊销并轮换，再清理历史。

## 部署基线

- 环境文件权限设为 `0600`，仅服务账号可读。
- OneBot 与 OpenClaw 优先监听环回地址。
- Web 控制台必须置于 HTTPS 后，管理员 Cookie 使用 Secure/HttpOnly/SameSite。
- 控制面令牌至少 32 字节随机值，不复用管理员密码或模型 Key。
- 机器人主机只发起出站 HTTPS；不要为控制台暴露 SSH 或机器人内部端口。
- API Key 操作应保留审计元数据，但绝不记录 Key 明文。

## 群黑名单不变量

命中 `BLOCKED_GROUP_IDS` 的群必须在事件入口立即丢弃，并在任何状态落盘前再次清除。其 ID、名称、消息、图片、统计、记忆和事件不得发送至控制台。

## 报告漏洞

请通过 GitHub Security Advisory 私下报告。不要在公开 Issue 中粘贴真实密钥、群号、日志或用户消息。
