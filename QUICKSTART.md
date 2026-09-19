# 快速上手

## 1. 先确认现有链路

部署前至少应确认：NapCat 已登录、OneBot WebSocket 仅监听本机或受控网络、OpenClaw Gateway 可访问。不要为了安装本项目重新安装一个已经正常工作的 NapCat 或 OpenClaw。

## 2. 安装机器人文件

```bash
git clone https://github.com/CeylanCLC/agent-qq-sandbox-bot.git
cd agent-qq-sandbox-bot
sudo python3 deploy/install.py bot --target /opt/qq-openclaw-bot --venv
```

安装器会先备份非空目标目录。它不会创建密钥，也不会修改 NapCat 或 OpenClaw 配置。

## 3. 创建配置

```bash
sudo cp examples/bot.env.example /etc/qq-openclaw-bot.env
sudo chmod 600 /etc/qq-openclaw-bot.env
sudoedit /etc/qq-openclaw-bot.env
```

至少填写 `BOT_DISPLAY_NAME`、`BOT_TRIGGER_WORDS`、`ONEBOT_WS`、`OPENCLAW_URL` 和 `OPENCLAW_GATEWAY_TOKEN`。需要群黑名单时填写 `BLOCKED_GROUP_IDS`；不要把真实值提交到 Git。

## 4. 前台测试

```bash
set -a; . /etc/qq-openclaw-bot.env; set +a
/opt/qq-openclaw-bot/venv/bin/python /opt/qq-openclaw-bot/qq_openclaw_bridge.py
```

在测试群 @ 机器人，确认文本和图片链路。按 `Ctrl+C` 停止。

## 5. systemd

```bash
sudo cp bot/qq-openclaw-bridge.service.example /etc/systemd/system/qq-openclaw-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now qq-openclaw-bridge.service
sudo systemctl status qq-openclaw-bridge.service --no-pager -l
```

示例单元默认路径是 `/opt/qq-openclaw-bot`。使用其他路径时先编辑单元文件。

## 6. 可选 Web 控制台

控制台是参考实现，适合并入已有 Flask 管理后台。全新独立部署可运行：

```bash
sudo python3 deploy/install.py website --target /opt/qq-bot-console
python3 -m venv /opt/qq-bot-console/venv
/opt/qq-bot-console/venv/bin/pip install -r /opt/qq-bot-console/requirements.txt
```

若目标目录已有 `app.py`，安装器会拒绝覆盖；请按 [迁移指南](docs/MIGRATION.md) 增量合并。

控制台接入后，在两端使用同一个随机 `CEYLAN_BOT_TOKEN`，并在机器人端设置 `BOT_CONTROL_BASE_URL=https://你的域名`。不要把 Markdown 链接语法写进 URL。
