# 生产部署手册（腾讯云）

> ⚠️ **红线（首行）：全项目任何环节禁止执行 `docker compose down -v`** — `-v` 会删除 qdrant 数据卷，知识库全丢，无法恢复。

## 1. 服务器初始化

```bash
# 安装 Docker + Compose（腾讯云 Ubuntu 可用 apt 或官方脚本）
curl -fsSL https://get.docker.com | sh

# 拉代码
git clone https://github.com/xiongaming879-create/RAG-To-Wechat-Robot.git
cd RAG-To-Wechat-Robot
```

## 2. 配置 .env（生产值）

```bash
cp .env.example .env && vim .env
```

与本地测试的差异项：

| 变量 | 生产值 | 说明 |
|------|--------|------|
| `QDRANT_URL` | `http://qdrant:6333` | **必须用容器主机名**，不能 localhost |
| `WX_CORP_ID` / `WX_AGENT_ID` / `WX_SECRET` | 公司企微自建应用真实值 | 公司注册后从企微后台获取 |
| `WX_TOKEN` / `WX_AES_KEY` | 回调配置随机生成 | AESKey 必须 43 位 |
| `WX_BOT_USERID` | 机器人应用.userid | 群聊 @ 判定用 |
| `ADMIN_USER_LIST` | 真实管理员企微 userid 数组 | 注意是 userid 不是昵称 |
| `ADMIN_API_TOKEN` | 强随机串 | `openssl rand -hex 32` |

## 3. 启动与验证

```bash
docker compose up -d --build
curl http://localhost:8000/health   # {"status":"ok",...,"vector_store":"ok"}
docker compose ps                   # qdrant 不得有宿主机端口映射
```

## 4. Nginx 反代（宿主机 80/443 → 8000）

```nginx
server {
    listen 443 ssl;
    server_name rag.example.com;                      # 换成你的域名
    ssl_certificate     /etc/nginx/ssl/fullchain.pem; # 证书
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    client_max_body_size 20M;                         # 企微文件 + 管理 API 上传上限
    proxy_read_timeout   60s;                         # 企微回调 5s，但管理 API/聊天接口需要

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;      # 真实 IP 透传（限流按 IP 记录依赖）
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server {
    listen 80;
    server_name rag.example.com;
    return 301 https://$host$request_uri;             # HTTP → HTTPS
}
```

## 5. 企业微信回调接入（公司注册后）

1. 企微管理后台 → 应用管理 → 自建应用 → 接收消息 → 设置 API 接收
2. URL 填 `https://rag.example.com/wx/callback`，Token/EncodingAESKey 与 `.env` 一致
3. 保存时企微发 GET 校验请求，服务端验签回 echostr（我们的 `/wx/callback` GET 已实现）
4. **Web 聊天接口（/api/chat、/static/*）公网部署前必须加鉴权或限制来源 IP** — 目前无鉴权，仅限本地/内网使用

## 6. 重启自愈验证

```bash
docker compose restart rag_wechat_bot   # 应用重启
sudo reboot                              # 整机重启后 compose restart always 应自动拉起
docker compose ps                        # 两个容器 Up
curl http://localhost:8000/health        # 数据仍在（qdrant 卷持久化）
```

## 7. 每周备份

`/opt/backup-kb.sh`（crontab: `0 3 * * 0 /opt/backup-kb.sh`）：

```bash
#!/bin/bash
set -e
BACKUP_DIR=/opt/kb-backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
docker exec rag-qdrant tar czf - /qdrant/storage > "$BACKUP_DIR/qdrant_storage.tar.gz"
tar czf "$BACKUP_DIR/uploads.tar.gz" -C "$(dirname "$(pwd)")" uploads 2>/dev/null || true
find /opt/kb-backups -mtime +30 -delete    # 保留 30 天
```

恢复（新机器）：解压 tar 到对应卷路径后 `docker compose up -d`。**永远不要 down -v。**

## 8. 常用运维

```bash
docker compose logs -f rag_wechat_bot    # 看日志
docker compose restart rag_wechat_bot    # 改 .env 后重启生效
docker compose up -d --build             # 改代码后重建
docker compose down                      # 停止（保留卷，安全）
docker compose down -v                   # ☠️ 禁止：删除数据卷
```

## 9. 更新部署

```bash
git pull
docker compose up -d --build
curl http://localhost:8000/health
.venv 不存在于服务器 — 测试在开发机跑，服务器只跑容器
```
