# Linux 公网服务器部署

Linux 服务器可以独立运行 Agent Cooperation 中心。Mac 不需要常驻；只有需要把 Mac 作为执行成员、在 Mac 上运行 Claude/Codex 等客户端时，Mac 才需要在线。

中心负责网页、任务、消息、日志、产物和调度。执行成员仍在各自电脑上运行已经登录的 AI 客户端。代码任务的分支和提交继续使用 GitHub 交接，中心不会把客户端登录凭据复制到服务器。

## 推荐配置

- Ubuntu 22.04 或 24.04 LTS
- 2 vCPU、4 GB RAM、40 GB SSD 起步；多人并发建议 8 GB RAM、80 GB SSD
- 一个指向服务器的域名，例如 `cooperation.example.com`
- 防火墙只开放 SSH、HTTP 和 HTTPS；应用端口 8765 不对公网开放

服务器不需要安装 Node.js，也不需要安装 Claude/Codex。只有把服务器同时注册为 worker 时，才需要在服务器上安装对应客户端。

## 安装中心

仓库提供 Ubuntu 安装脚本。它只安装系统依赖、创建虚拟环境、安装项目并注册 systemd；不会生成或上传任何登录令牌：

```bash
git clone https://github.com/Jay1023CN/agent-cooperation.git /tmp/agent-cooperation
cd /tmp/agent-cooperation
chmod +x deploy/linux/install-center.sh
./deploy/linux/install-center.sh
```

默认安装到 `/opt/agent-cooperation`。需要自定义目录时，可以在执行前设置 `ARENA_REPO_DIR`、`ARENA_DATA_DIR` 和 `ARENA_REPO_URL`。

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
sudo mkdir -p /opt/agent-cooperation
sudo chown -R "$USER":"$USER" /opt/agent-cooperation
git clone https://github.com/Jay1023CN/agent-cooperation.git /opt/agent-cooperation
cd /opt/agent-cooperation
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e '.[platform]'
mkdir -p "$HOME/.pal-arena/server"
chmod 700 "$HOME/.pal-arena" "$HOME/.pal-arena/server"
```

先前台验证：

```bash
./.venv/bin/pal-arena serve \
  --data "$HOME/.pal-arena/server" \
  --host 127.0.0.1 \
  --port 8765
```

打开 `http://127.0.0.1:8765`，使用 `$HOME/.pal-arena/server/owner-token.txt` 登录。确认中心可用后按下节配置 systemd。

## systemd 常驻

复制 `deploy/linux/agent-cooperation.service.example` 到 `/etc/systemd/system/agent-cooperation.service`，将 `ARENA_USER` 替换成运行服务的 Linux 用户名。不要使用 root 运行中心。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-cooperation
sudo systemctl status agent-cooperation
journalctl -u agent-cooperation -f
```

升级前先停止服务并备份中心数据。不要直接复制运行中的 `arena.db`；使用维护命令生成一致备份。

## 域名和 HTTPS

将域名的 A/AAAA 记录指向服务器，开放 80 和 443，然后安装 Caddy：

```bash
sudo apt install -y caddy
sudo cp deploy/linux/Caddyfile.example /etc/caddy/Caddyfile
sudo sed -i 's/cooperation.example.com/你的真实域名/' /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Caddy 会自动申请和续期 Let's Encrypt 证书。组员和 worker 使用 HTTPS 域名，不使用服务器的 8765 端口：

```text
https://你的真实域名
```

应用仍只监听 `127.0.0.1:8765`。不要把 `--host` 改为 `0.0.0.0`，也不要把 8765 加入公网防火墙规则。

## 防火墙

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status verbose
```

如果使用云厂商安全组，也要只放行 SSH、80 和 443。SSH 建议使用密钥登录并关闭密码登录。

## 让组员接入

在网页的「电脑与成员」中为每台电脑创建独立机器令牌并下载 `worker.json`。把 `server` 设置为 HTTPS 域名，例如：

```json
{"server": "https://你的真实域名"}
```

令牌保存为本机用户目录下的单行文件，不要提交到 Git：

如果执行端使用仓库源码或已安装 `pal-arena`，可以用一个命令同时生成配置并安全安装令牌：

```bash
pal-arena worker-setup \
  --name '组员电脑' \
  --server 'https://你的真实域名' \
  --token-file ~/Downloads/machine-token.txt
```

它会在 `~/.pal-arena/` 创建 `worker.json` 和权限为 600 的 `machine-token.txt`，并打印下一条启动命令。Windows PowerShell 使用同样的参数格式，只需把路径改成 Windows 路径。

```bash
chmod 600 ~/.pal-arena/machine-token.txt
./pal-arena/pal-arena doctor --config ~/.pal-arena/worker.json
./pal-arena/pal-arena maintenance run-service \
  --role worker \
  --config ~/.pal-arena/worker.json \
  --token-file ~/.pal-arena/machine-token.txt \
  --log-file ~/.pal-arena/logs/worker.log
```

执行电脑需要自己安装并登录 Claude Code、Codex、Qoder 或其他配置到该 worker 的客户端。服务器只中转任务和结果，不会替组员提供这些订阅或登录态。

## 备份和检查

至少每天备份 `~/.pal-arena/server`，并把备份复制到另一台机器或对象存储。备份包含中心身份信息，必须限制权限。仓库提供每日备份模板：

```bash
sudo install -m 0644 deploy/linux/agent-cooperation-backup.service.example \
  /etc/systemd/system/agent-cooperation-backup.service
sudo install -m 0644 deploy/linux/agent-cooperation-backup.timer.example \
  /etc/systemd/system/agent-cooperation-backup.timer
sudo sed -i 's/ARENA_USER/你的Linux用户名/g' \
  /etc/systemd/system/agent-cooperation-backup.service
sudo systemctl daemon-reload
sudo systemctl enable --now agent-cooperation-backup.timer
systemctl list-timers agent-cooperation-backup.timer
```

备份默认放在 `~/.pal-arena/backups`，保留 14 天。设置 `ARENA_BACKUP_DIR` 和 `ARENA_BACKUP_RETENTION_DAYS` 可调整位置和保留期；应再把备份复制到另一台机器或对象存储。

上线前检查：

```bash
chmod +x deploy/linux/check-center.sh
./deploy/linux/check-center.sh
```

公网只应能访问 HTTPS 域名；如果 `http://服务器IP:8765` 能访问，说明防火墙或监听配置过宽，应立即收紧。
