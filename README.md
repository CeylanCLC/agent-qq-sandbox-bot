# Agent QQ Sandbox Bot

A QQ group AI bot bridge based on OpenClaw, NapCat OneBot, WebSocket, and Python.

## Features

- QQ group @ reply
- Keyword trigger: 弹性 / 张智豪 / 茶
- Private chat reply
- Multi-step task mode
- Send group images
- Upload group files
- Periodic group message review
- Group message cache
- Bot self-message cache to avoid repeated replies
- systemd service deployment
- NapCat OneBot WebSocket integration
- OpenClaw Gateway API integration

## Architecture

QQ Group
  -> NapCat OneBot WebSocket
  -> Python Bridge Service
  -> OpenClaw Gateway /v1/chat/completions
  -> QQ Group Reply

## Runtime Environment

- Ubuntu Server 22.04 LTS
- Python 3.10+
- Docker
- NapCat OneBot
- OpenClaw Gateway

## Install

cd /root/openclaw-qq
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

## Run

python qq_openclaw_bridge.py

## systemd Service

systemctl status qq-openclaw-bridge --no-pager
journalctl -u qq-openclaw-bridge -f --full

## Commands

Send image:

弹性发图：test.png

Upload group file:

弹性发文件：report.docx

Task mode:

弹性任务：分三步分析这个方案。

## Security Notice

Do not commit:

- QQ login state
- NapCat runtime files
- OpenClaw token
- .env
- periodic_state.json
- docker-compose.yml
- venv/
- files under share/

## Demo Screenshots

### Periodic group review logs

![Periodic group review logs](demo-pic-lo.png)

### Bot profile and historical replies

![Bot profile history](demo-profile-history.png)

### Hardware group context reply

![Hardware context reply 1](demo-hardware-context-1.png)

![Hardware context reply 2](demo-hardware-context-2.png)

### Memory-aware reply

![Memory context](demo-memory-context.png)

### Active reply with group context

![Active reply](demo-active-rply.png)
