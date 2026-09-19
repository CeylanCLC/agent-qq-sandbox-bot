# 故障排查

## 心跳 HTTP 403 / Cloudflare 错误

先打印经过脱敏的 URL 表示和 HTTP 状态，不要修改 token。最常见原因是 URL 被误写成 Markdown 链接，或 Cloudflare 根据 Python 默认 User-Agent/WAF 规则拦截。仓库心跳已使用明确 User-Agent；仍失败时对比同主机 `curl` 与 Python 请求，并检查 Cloudflare 事件。

## `KeyError: CEYLAN_BOT_TOKEN`

手工运行 Python 不会自动读取 systemd `EnvironmentFile`。使用：

```bash
set -a; . /etc/qq-openclaw-bot.env; set +a
python heartbeat.py
```

不要回显环境变量内容。

## OneBot 无法连接

确认 `ONEBOT_WS`、监听地址、端口和 token。WebSocket 建议只监听环回地址；若 NapCat 在容器中，检查端口映射而不是关闭鉴权。

## 图片请求 400

确认消息数组只有开头的 system 消息，后续图片内容使用服务商支持的 `image_url` 格式。测试 `VISION_IMAGE_URL_FORMAT=object` 与服务商文档要求，并限制图片大小和数量。

## 控制台看不到最新状态

基础心跳、详细遥测、控制同步是不同任务。先分别手工运行 `heartbeat.py` 与 `ceylan_monitor.py`，确认 URL 和返回体，再检查 timer。聊天正常不代表遥测 timer 正常，反之亦然。

## `IndentationError`

不要用盲目的字符串替换修改生产 Python。先从备份或仓库恢复文件，运行 `python -m py_compile 文件.py`，再通过明确补丁修改。
