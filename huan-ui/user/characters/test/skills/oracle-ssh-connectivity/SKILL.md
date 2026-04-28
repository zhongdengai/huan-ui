---
name: oracle-ssh-connectivity
description: Oracle ARM 服务器 SSH 连接方法、关键路径、网络特点
category: devops
---

# Oracle ARM 服务器 SSH 连接

## 服务器信息
- Oracle ARM (4核/24GB/136GB)
- IP: 140.245.105.176
- SSH key: ~/Downloads/arm-key.key
- 用户: ubuntu
- 桌面快捷方式: ~/Desktop/梯知服务器.command

## SSH 连接方法
```bash
ssh -i ~/Downloads/arm-key.key -o StrictHostKeyChecking=no ubuntu@140.245.105.176 "echo OK"
```

## 重要：-t 参数
快捷方式里有 `-t` 是因为要用 tmux：
```bash
ssh -i ~/Downloads/arm-key.key ubuntu@140.245.105.176 -t "tmux attach -t tizhi || tmux new -s tizhi && claude --dangerously-skip-permissions"
```

单命令执行不需要 `-t`。

## 网络特点
- Hiddify 代理 HTTP 流量，但 SSH 直连 22 端口即可通
- Oracle 防火墙可能只允许特定 IP 的 22 端口访问
- HTTP(80/443) 和 SSH(22) 端口都可通

## tmux 会话
- `tizhi`: Claude Code 在跑，--dangerously-skip-permissions
- `main`: 另一个会话

## 关键目录
- 主站代码: ~/tizhi/
- PM2 日志: ~/.pm2/logs/
- 应用日志: ~/logs/
- Nginx 配置: /etc/nginx/sites-enabled/
- Discourse: /var/discourse/
- 备份: ~/backups/
