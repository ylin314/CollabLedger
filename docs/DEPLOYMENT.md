# 部署与运维

## 1. 本地或内网 HTTP

默认只把后端绑定到宿主机回环地址，避免开发环境意外暴露到局域网：

```powershell
Copy-Item .env.example .env
# 填写 .env 中需要的配置
docker compose up -d --build
```

访问 `http://127.0.0.1:8000`。查看健康状态与日志：

```powershell
docker compose ps
docker compose logs -f --tail 200 collab-ledger
```

日志使用 Docker `json-file` 驱动，单文件最大 10 MB，保留 5 个文件。

## 2. 生产 HTTPS

生产环境需要一个解析到服务器公网 IP 的真实域名。修改 `.env`：

```dotenv
COLLAB_DOMAIN=collab.example.com
COLLAB_CORS_ORIGINS=https://collab.example.com
COLLAB_COOKIE_SECURE=true
COLLAB_TRUST_PROXY=true
APP_BIND_HOST=127.0.0.1
```

开放防火墙 TCP 80/443 和 UDP 443，然后启动应用与 Caddy：

```powershell
docker compose -f compose.yaml -f compose.https.yaml --profile https up -d --build
```

Caddy 自动申请和续期公开证书，并将请求反向代理到应用容器。后端 8000 端口仍只绑定宿主机回环地址。首次上线后验证：

```powershell
docker compose -f compose.yaml -f compose.https.yaml --profile https ps
docker compose -f compose.yaml -f compose.https.yaml --profile https logs --tail 200 caddy
```

不要把 `.env`、证书数据卷或真实 API Key 提交到 Git。


## 3. PostgreSQL 生产数据库

`.env` 中配置强密码：

```dotenv
POSTGRES_DB=collab_ledger
POSTGRES_USER=collab
POSTGRES_PASSWORD=使用高强度随机密码
```

启动 PostgreSQL overlay：

```powershell
docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres up -d --build
```

应用将通过 `DATABASE_URL` 使用 PostgreSQL；默认 SQLite 数据卷仍保留但不会作为运行时数据库。首次启动后检查迁移版本：

```powershell
docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres exec -T collab-ledger python -m alembic upgrade head
```

HTTPS 与 PostgreSQL 可以同时组合：

```powershell
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml --profile postgres --profile https up -d --build
```

## 4. 数据库迁移

容器镜像包含 Alembic。容器入口会在启动 Uvicorn 前自动执行 `python -m alembic upgrade head`。版本升级时仍应先手工备份，并观察迁移日志。

升级前先备份：

```powershell
.\scripts\backup.ps1
docker compose exec -T collab-ledger python -m alembic upgrade head
```

查看版本：

```powershell
docker compose exec -T collab-ledger python -m alembic current
```

## 5. 启停与升级

```powershell
# 安全停止
docker compose down

# 拉取代码后重建
docker compose up -d --build

# 健康检查
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

不要使用 `docker compose down -v`，除非已经验证备份且明确要删除数据库卷。

## 6. 生产检查清单

- `COLLAB_COOKIE_SECURE=true`。
- `COLLAB_CORS_ORIGINS` 只包含正式前端域名。
- 只有在 Caddy 等可信代理是唯一入口时才设置 `COLLAB_TRUST_PROXY=true`。
- `.env` 权限仅限运维账号。
- 每日执行备份，并将 `backups/` 复制到另一台机器或对象存储。
- 定期执行恢复演练，而不只是检查备份文件存在。
- 监控 `/api/health`、容器重启次数、磁盘空间和 5xx 日志。
