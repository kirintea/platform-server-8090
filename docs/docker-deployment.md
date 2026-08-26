# Docker 容器化部署指南

## 概述

项目支持两种 Docker 部署模式：

| 模式 | Compose 文件 | 说明 |
|------|-------------|------|
| **基础模式** | `docker-compose.yaml` | 仅 app 容器，数据库连宿主机 |
| **沙箱模式** | `docker-compose.sandbox.yml` | app + sandbox 容器隔离，工具在沙箱中执行 |

数据库（Redis / PostgreSQL）独立部署，不放在应用 compose 中。

## 前置条件

- Docker Engine 20.10+
- Docker Compose v2
- 宿主机已运行 Redis（端口 6390）和 PostgreSQL（端口 5432）

## 基础模式

### 文件说明

| 文件 | 用途 |
|------|------|
| `Dockerfile` | app 镜像（python:3.12-slim + 依赖安装） |
| `docker-compose.yaml` | app 服务定义 |

### 启动

```bash
# 构建镜像
docker compose build

# 启动容器（后台）
docker compose up -d

# 进入容器
docker compose exec app bash

# 在容器内启动服务
python main.py
```

### 关键设计

- **代码外挂**：项目目录通过 volume 挂载到 `/workspace`，修改代码后重启服务即可生效，无需重建镜像
- **容器启动 ≠ 服务启动**：容器默认 `sleep infinity`，服务在容器内手动启动
- **环境变量优先**：`load_dotenv(override=False)` 让 Docker 注入的环境变量优先于 `.env` 文件
- **宿主机通信**：通过 `host.docker.internal` 访问宿主机的 Redis / PostgreSQL / OTel

### 环境变量

在 `docker-compose.yaml` 的 `environment` 中配置：

```yaml
environment:
  - APP_ENV=dev
  - REDIS_URL=redis://host.docker.internal:6390/0
  - DATABASE_URL=postgresql://user:password@host.docker.internal:5432/ragdb
  - OTEL_ENDPOINT=http://host.docker.internal:4317
```

## 沙箱模式

### 架构

```
┌─────────────────────────────────┐
│  app 容器 (platform-server-8090)│
│  ├── FastAPI 服务               │
│  ├── 完整项目代码 (/workspace)  │
│  └── DockerSandboxProxy 中间件  │
│       │                         │
│       │ docker exec (SDK)       │
│       ▼                         │
│  ┌─────────────────────────┐    │
│  │ sandbox 容器             │    │
│  │ (platform-sandbox)       │    │
│  │ ├── workspaces/ (仅此目录)│    │
│  │ └── skills/ (只读)       │    │
│  └─────────────────────────┘    │
└─────────────────────────────────┘
```

sandbox 容器只能看见 `workspaces/` 和 `skills/`，无法访问项目源码和配置文件。

### 文件说明

| 文件 | 用途 |
|------|------|
| `Dockerfile` | app 镜像 |
| `Dockerfile.sandbox` | sandbox 镜像（含 bash/curl/git 等基础工具） |
| `docker-compose.sandbox.yml` | app + sandbox 服务定义 |

### 启动

```bash
# 构建两个镜像
docker compose -f docker-compose.sandbox.yml build

# 启动（sandbox 默认 local 模式，不启用容器沙箱）
docker compose -f docker-compose.sandbox.yml up -d

# 进入 app 容器启动服务
docker compose -f docker-compose.sandbox.yml exec app python main.py
```

### 启用沙箱

沙箱默认关闭（`backend: local`），需要显式启用：

**方式一：环境变量**

```bash
SANDBOX_BACKEND=docker docker compose -f docker-compose.sandbox.yml up -d
```

**方式二：修改配置文件**

```yaml
# configs/dev.yaml
sandbox:
  backend: "docker"    # 改为 docker 即启用
  container: "platform-sandbox"
  project_root: "/workspace"
  extra_mounts:
    - host: "skills"
      container: "/workspace/skills"
      readonly: true
```

### 沙箱配置

```yaml
sandbox:
  backend: "local"           # local = 本机执行（默认）| docker = 沙箱容器执行
  container: "platform-sandbox"  # 沙箱容器名
  project_root: "/workspace"     # 容器内项目根路径
  extra_mounts:                  # 额外挂载目录
    - host: "skills"             # 宿主机相对路径
      container: "/workspace/skills"  # 容器内路径
      readonly: true             # 只读挂载
```

### 工具执行流程

当 `backend: docker` 时，`DockerSandboxProxy` 中间件拦截以下工具：

| 工具 | 拦截方式 |
|------|---------|
| Bash / PowerShell | 命令通过 Docker SDK 在 sandbox 容器执行 |
| Read / Write / Edit | 文件路径翻译后在 sandbox 容器执行 |
| Glob / Grep | 搜索路径翻译后在 sandbox 容器执行 |
| Task* | 任务管理在 sandbox 容器执行 |

其他工具（MCP 等）不拦截，在 app 容器执行。

### 常用命令

```bash
# 查看容器状态
docker compose -f docker-compose.sandbox.yml ps

# 进入 app 容器
docker compose -f docker-compose.sandbox.yml exec app bash

# 进入 sandbox 容器
docker compose -f docker-compose.sandbox.yml exec sandbox bash

# 查看 sandbox 容器内容（验证隔离）
docker compose -f docker-compose.sandbox.yml exec sandbox ls -la /workspace/

# 重建单个服务
docker compose -f docker-compose.sandbox.yml build sandbox
docker compose -f docker-compose.sandbox.yml up -d sandbox

# 停止所有
docker compose -f docker-compose.sandbox.yml down
```

## 网络说明

| 宿主机服务 | 容器内地址 | 说明 |
|-----------|-----------|------|
| Redis (6390) | `host.docker.internal:6390` | 通过 extra_hosts 映射 |
| PostgreSQL (5432) | `host.docker.internal:5432` | 通过 extra_hosts 映射 |
| OTel Collector (4317) | `host.docker.internal:4317` | 通过 extra_hosts 映射 |

`host.docker.internal` 在 Linux 上需要 `extra_hosts` 配置，macOS/Windows 自动可用。

## 常见问题

### Q: 修改了 requirements.txt 怎么办？

重建镜像：
```bash
docker compose -f docker-compose.sandbox.yml build
docker compose -f docker-compose.sandbox.yml up -d
```

### Q: 修改了 configs/*.yaml 需要重建吗？

不需要。配置文件通过 volume 挂载，重启服务即可：
```bash
docker compose -f docker-compose.sandbox.yml exec app python main.py
```

### Q: sandbox 容器报 "No such file or directory: docker"？

已改用 Python Docker SDK（`docker` 包），不需要 Docker CLI。确保 app 容器已安装 `docker` 包：
```bash
docker compose -f docker-compose.sandbox.yml exec app pip install docker==7.1.0
```

### Q: 如何查看 sandbox 容器是否正常？

```bash
docker compose -f docker-compose.sandbox.yml exec sandbox cat /etc/os-release
docker compose -f docker-compose.sandbox.yml exec sandbox python3 -c "import agentscope; print('OK')"
```
