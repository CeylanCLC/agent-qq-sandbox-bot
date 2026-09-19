# 给 AI / 自动化代理的一键部署指南

把本文件连同仓库交给具备终端权限的 AI。目标是增量部署，不重装 NapCat/OpenClaw，不破坏已有 Flask、数据库或反向代理。

## 强制规则

1. 修改前只读检查服务、环境文件、目标代码和磁盘空间。
2. 不输出任何 token、API Key、密码、Cookie、NapCat 登录态或群号。
3. 每次写入前创建带时间戳备份；不得使用 `rm -rf`、`git reset --hard` 或覆盖未知文件。
4. 已有 Flask 应用只能增量集成，除非操作者明确批准 `--replace-app`。
5. 群黑名单必须在消息入口过滤，且不得进入缓存、记忆、巡群、统计、遥测和 UI。
6. 先语法测试，再前台单次测试，再重启服务；失败立即停止并给出回滚命令。

## 建议执行顺序

```bash
# 机器人主机：只读检查
systemctl status qq-openclaw-bridge --no-pager -l || true
python3 --version
test -f /etc/qq-openclaw-bot.env && stat -c '%a %U:%G %n' /etc/qq-openclaw-bot.env || true

# 安装到新目录或明确目标
python3 deploy/install.py bot --target /opt/qq-openclaw-bot --venv
python3 -m compileall -q /opt/qq-openclaw-bot

# 配置由人填写，不读取或回显秘密
install -m 600 examples/bot.env.example /etc/qq-openclaw-bot.env.example
```

然后让操作者填写真实配置。不要替操作者猜测域名、QQ、模型、群号或密钥。

## 验证清单

- Python 全部编译通过。
- OneBot WebSocket 与 OpenClaw 只使用预期地址。
- 黑名单测试事件在入口被拒绝，状态快照不含其 ID。
- 普通群消息可收发，图片测试可用或明确降级。
- 控制台开启时，心跳返回 `{"ok": true}`；未开启时，机器人主链路不受影响。
- `systemctl is-active qq-openclaw-bridge` 为 `active`。
- 日志中无密钥、聊天正文泄漏和循环重启。

## 回滚

安装器会输出备份目录。停止服务，将目标目录改名留存，再把备份复制回原路径，运行 `systemctl daemon-reload` 并启动原服务。回滚时不得删除状态数据库和群记忆文件。
