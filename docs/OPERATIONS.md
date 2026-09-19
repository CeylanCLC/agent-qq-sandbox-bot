# 日常运维

## 状态与日志

```bash
systemctl status qq-openclaw-bridge --no-pager -l
journalctl -u qq-openclaw-bridge -n 100 --no-pager
systemctl list-timers --all | grep -E 'openclaw|heartbeat'
```

## 更新

1. 记录当前 commit 和服务状态。
2. 备份代码目录、systemd 单元和环境文件；数据库与状态文件单独备份。
3. 在临时目录拉取新版本并运行 `pytest -q`。
4. 使用安装器更新，它会再次生成代码备份。
5. `python3 -m compileall -q` 后重启服务并检查日志。

## 健康判断

- Bridge `active` 且没有连续重启。
- NapCat 登录、OneBot API 与 OpenClaw TCP 可达。
- 心跳新鲜；详细遥测允许短暂落后。
- 模型异常率、平均延迟、队列和磁盘没有持续恶化。
- 黑名单启用状态正确，具体 ID 不出现在网站或日志导出中。

## 回滚

停止服务，把当前目录改名保留，然后从安装器输出的备份目录恢复。恢复后先编译，再启动，并验证原状态文件和数据库仍在。不要用空目录覆盖状态数据。
