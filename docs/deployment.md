# 部署方案 — LLM Unified Platform

> 本文档覆盖本地 Docker Compose 起栈与单机服务器部署两种形态。

---

## 1. 本地 Docker Compose 起栈

### 1.1 服务依赖顺序

```
PostgreSQL (healthy) ──→ Alembic 迁移 ──→ App (uvicorn)
Redis (healthy) ─────────────────────────→ App
```

`docker-compose.yml` 中通过 `depends_on` + `condition: service_healthy` 保证顺序：

```yaml
services:
  postgres:
    image: postgres:16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U llm"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
```

### 1.2 启动步骤

```bash
# 1. 克隆并配置
git clone <repo-url> && cd llm-unified-platform
cp .env.example .env
# 编辑 .env 填入真实密钥

# 2. 起栈
docker compose up -d

# 3. 执行数据库迁移（首次部署必须）
docker compose exec app alembic upgrade head

# 4. 验证
curl http://localhost:8000/healthz   # {"status":"ok"}
curl http://localhost:8000/readyz    # {"status":"ready","checks":{"database":true,"redis":true}}
```

### 1.3 停止与清理

```bash
docker compose down        # 停止服务（保留数据卷）
docker compose down -v     # 停止并删除数据卷（慎用）
```

---

## 2. 单机服务器部署

### 2.1 前置条件

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 22.04+ / CentOS 8+ |
| Docker | 24.0+ |
| Docker Compose | 2.20+ |
| 内存 | ≥ 4GB（含 PostgreSQL + Redis + App） |
| 磁盘 | ≥ 50GB（含 call_log 存储） |
| CPU | ≥ 2 核 |

### 2.2 构建镜像

```bash
docker build -t llm-unified-platform:0.1.0 .
docker tag llm-unified-platform:0.1.0 llm-unified-platform:latest
```

### 2.3 部署顺序

```bash
# 1. 启动 PostgreSQL + Redis
docker compose up -d postgres redis

# 2. 等待健康检查通过
docker compose ps  # 确认 postgres/redis 为 healthy

# 3. 执行数据库迁移（必须在 App 启动前完成）
docker compose run --rm app alembic upgrade head

# 4. 启动应用
docker compose up -d app

# 5. 验证
curl http://localhost:8000/readyz
```

### 2.4 反向代理与 HTTPS

推荐使用 Nginx 反代 + Let's Encrypt：

```nginx
server {
    listen 443 ssl;
    server_name llm.yourcompany.com;

    ssl_certificate     /etc/letsencrypt/live/llm.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/llm.yourcompany.com/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

**注意事项**：
- SSE 流式响应必须设置 `proxy_buffering off`，否则首 token 延迟会显著增大
- `proxy_read_timeout` 建议 ≥ 300s，覆盖长推理请求
- 生产环境 `LLM_JWT_SECRET` 必须使用 `openssl rand -hex 32` 生成
- 建议限制 `/admin/v1/*` 仅内网访问（Nginx `allow/deny`）

### 2.5 资源建议

| 场景 | CPU | 内存 | 磁盘 | 说明 |
|------|-----|------|------|------|
| 开发/测试 | 2 核 | 4GB | 20GB | docker compose 单机 |
| 小规模生产 | 4 核 | 8GB | 50GB | 日调用 < 10 万次 |
| 中规模生产 | 8 核 | 16GB | 200GB | 日调用 10~100 万次，建议 PG 独立部署 |

---

## 3. 数据库迁移

```bash
# 查看当前版本
alembic current

# 升级到最新
alembic upgrade head

# 回退一步
alembic downgrade -1

# 查看迁移历史
alembic history
```

**迁移执行顺序**：`0001` → `0002`（必须按顺序，不可跳过）。

---

## 4. 备份与归档

### 4.1 PostgreSQL 备份

```bash
# 全量备份
docker compose exec postgres pg_dump -U llm llm_platform > backup_$(date +%Y%m%d_%H%M%S).sql

# 恢复
docker compose exec -T postgres psql -U llm llm_platform < backup_20261108.sql
```

建议通过 cron 每日自动备份：

```bash
0 2 * * * cd /opt/llm-platform && docker compose exec postgres pg_dump -U llm llm_platform | gzip > /backups/llm_$(date +\%Y\%m\%d).sql.gz
```

### 4.2 call_log 归档（T-90 天 Parquet）

call_log 按月分区（RANGE），历史数据可归档为 Parquet 后清理：

```bash
# 1. 导出 90 天前数据为 Parquet
python scripts/seed_call_log.py --help  # 参考数据生成逻辑
# 实际归档脚本示例：
python -c "
from llm_platform.db.session import engine
import pandas as pd
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
df = pd.read_sql(f\"SELECT * FROM call_log WHERE created_at < '{cutoff}'\", engine)
df.to_parquet(f'archive/call_log_before_{cutoff}.parquet', index=False)
print(f'归档 {len(df)} 条记录')
"

# 2. 确认归档文件完整后，删除旧分区数据
# （需 DBA 评估后执行）
```

### 4.3 配置版本备份

```bash
# 导出配置版本表
docker compose exec postgres psql -U llm llm_platform -c \
  "COPY config_revision TO STDOUT WITH CSV HEADER" > config_backup.csv
```

---

## 5. 升级流程

```bash
# 1. 拉取新代码
git pull origin main

# 2. 构建新镜像
docker compose build app

# 3. 执行迁移（如有新增）
docker compose run --rm app alembic upgrade head

# 4. 滚动重启
docker compose up -d app

# 5. 验证
curl http://localhost:8000/healthz
```

**注意**：升级前务必备份 PostgreSQL 数据。迁移操作不可逆（downgrade 可能丢失数据）。
