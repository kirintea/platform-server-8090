# CLAUDE.md — AgentScope Platform Server 项目指引

## 项目概述

基于 **AgentScope 2.0.5** 构建的 AI Agent 平台服务，使用 FastAPI 提供 HTTP 接口。

- **Python**: >=3.12
- **核心依赖**: agentscope 2.0.5, fastapi, uvicorn, dashscope, openai, anthropic
- **详细文档**: [docs/agentscope-guide.md](docs/agentscope-guide.md)

## 项目结构

```
platform-server-8090/
├── main.py                    # 启动入口 — 配置加载 + 日志 + OTel + uvicorn
├── server.py                  # FastAPI 应用 — create_app(config) + lifespan + 路由注册
├── api/
│   ├── chat.py                # 对话 API 路由（/chat/stream, /chat/, /sessions/*）
│   ├── ws_chat.py             # WebSocket 对话端点（/ws/chat）
│   ├── mcp.py                 # MCP 管理（/mcp）
│   ├── skill.py               # Skill 管理（/skill）
│   └── static/                # 旧版前端对话界面（index.html + chat.js）
├── core/
│   ├── agent/factory.py       # Agent 工厂（模型/工具/中间件组装）
│   ├── config/                # 配置加载（YAML + 环境变量）
│   │   ├── schemas.py         # Pydantic Schema
│   │   ├── loader.py          # YAML 加载器
│   │   ├── manager.py         # ConfigManager 单例
│   │   └── resolver.py        # 环境变量解析器
│   ├── database.py            # PostgreSQL 管理器（asyncpg 连接池 + 自动建表）
│   ├── storage.py             # PostgreSQL 存储层（Agent/Session/MCP/Skill/Message CRUD）
│   ├── storage_models.py      # 数据模型定义（AgentRecord/MCPRecord/SkillRecord 等）
│   ├── chat_service.py        # Chat 服务层（Fire-and-Forget 事件驱动模式）
│   ├── session.py             # 会话管理器（Redis 持久化 + 元数据 + 消息历史 + fork）
│   ├── redis_message_bus.py   # Redis 分布式消息总线（分布式锁 + Pub/Sub）
│   ├── message_bus.py         # 消息总线抽象
│   ├── formatter/             # 自定义 Formatter（SiliconFlow 兼容）
│   │   ├── __init__.py
│   │   └── siliconflow.py     # SiliconFlowFormatter（content list 扁平化）
│   ├── workspace.py           # 自有 LocalWorkspaceManager（base_dir="./workspaces"）
│   ├── log/                   # 日志初始化（loguru + 文件轮转）
│   └── tracing/               # OTel 追踪初始化 + 装饰器
├── middleware/
│   ├── tool_guard.py          # 工具名级黑白名单中间件
│   ├── command_guard.py       # 命令内容级安全守卫（拦截危险命令）
│   ├── path_guard.py          # 路径访问守卫
│   └── tool_manager.py        # 工具管理器（从 configs/tools.yaml 选择性加载）
├── webui/                     # 新版 React 19 SPA（/webui）
│   ├── src/                   # 源码（React + TypeScript + TailwindCSS）
│   ├── dist/                  # 构建产物（由 server.py 直接服务）
│   └── package.json           # 前端依赖
├── health_check/              # 健康检查工具集（可单独执行）
│   ├── check_all.py           # 总入口：一键运行所有检查
│   ├── check_http.py          # HTTP 服务 + API 端点检查
│   ├── check_redis.py         # Redis 连接读写检查
│   ├── check_postgres.py      # PostgreSQL 连接表结构检查
│   └── check_llm.py           # LLM API 可达性检查
├── workflow/
│   └── base.py                # 自用工作流基类
├── workspaces/                # 统一沙箱与工作路径
├── tests/                     # 单元测试
├── configs/
│   ├── dev.yaml               # 开发环境配置
│   ├── prod.yaml              # 生产环境配置
│   └── tools.yaml             # 工具选择性加载配置
├── docker/
│   ├── docker-compose.yaml    # 可观测性栈 + Redis + PostgreSQL
│   ├── deploy_yml/            # 模块化部署 compose 文件（dev/各数据库独立）
│   └── *.yaml / *.yml         # OTel/Prometheus/Loki 配置
├── docs/                      # 设计文档
│   ├── agentscope-guide.md    # AgentScope 使用指南
│   ├── refactor-plan.md       # 重构规划
│   ├── health-check-plan.md   # 健康检查规划
│   ├── webui-plan.md          # WebUI 设计
│   ├── websocket-plan.md      # WebSocket 通道设计
│   ├── sandbox-plan.md        # 沙箱隔离设计
│   └── tool-middleware-plan.md # 工具守卫设计
├── scripts/
│   └── start_8090.sh          # 启动 8090 自有平台层（VENV_PATH 可配置）
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目依赖
└── requirements.txt           # 锁定依赖
```

## 服务架构

### main.py + server.py (端口 8090) — 自有平台层

- **main.py** — 启动入口：配置加载、日志初始化、OTel 初始化、uvicorn 启动
- **server.py** — FastAPI 应用：`create_app(config)` 工厂函数、lifespan 资源管理、路由注册
- 自有 SessionManager、DatabaseManager、RedisMessageBus、PostgresStorage、ChatService
- 会话存储：Redis `agentscope:session:*` 前缀
- 工作区：`workspaces/{user_id}/{session_id}/`

### 访问入口

| 前端 | 地址 | 说明 |
|------|------|------|
| 新版 WebUI | `http://localhost:8090/webui` | React 19 SPA（需先构建） |
| 旧版静态界面 | `http://localhost:8090/` | api/static/index.html |
| Swagger 文档 | `http://localhost:8090/docs` | API 文档 |

### 统一沙箱与工作路径 — workspaces/

8090 的自有 `LocalWorkspaceManager(base_dir="./workspaces")` 共享同一 `workspaces/` 根目录：

- 8090 路径结构：`workspaces/{user_id}/{session_id}/`（自有 workspace.py `_get_workdir`）
- 旧目录 `webui_workspaces/` 已废弃，数据已迁移，目录已删除

## 关键配置

### 环境变量（.env.example）

```bash
# LLM 配置（必填）
LLM_API_KEY=sk-xxx
LLM_BASE_URL=http://your-llm-api-base-url/v1
LLM_MODEL_NAME=glm-5

# OTel 追踪（可选，默认 http://localhost:4317）
OTEL_ENDPOINT=http://localhost:4317

# 数据库（可选，默认连本地 PostgreSQL）
DATABASE_URL=postgresql://user:password@localhost:5432/ragdb

# Redis（可选，默认本地）
REDIS_URL=redis://localhost:6379/0

# 应用环境：dev / prod
APP_ENV=dev
```

> YAML 配置中所有外部依赖地址均支持 `${VAR:-default}` 语法，未设置环境变量时使用默认值。

## API 端点

### 对话（chat）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式对话（SSE 事件流） |
| POST | `/chat/` | Fire-and-Forget 触发（事件驱动） |
| GET | `/sessions/{session_id}/stream` | SSE 事件流订阅 |
| GET | `/health` | 健康检查 |

### WebSocket 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/ws/chat?user_id={user_id}&session_id={session_id}` | 全双工 WebSocket 对话通道 |

### 会话（session）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | 列出内存中活跃会话（可选 `user_id` 过滤） |
| GET | `/sessions/{user_id}` | 列出用户所有历史会话（Redis SCAN） |
| GET | `/sessions/{user_id}/{session_id}/messages` | 获取会话消息历史 |
| POST | `/sessions/{user_id}/{session_id}/fork` | 基于父会话创建分支 |
| DELETE | `/sessions/{user_id}/{session_id}` | 删除会话 |

### MCP 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/mcp` | 列出已安装 MCP |
| POST | `/mcp` | 添加 MCP |
| PATCH | `/mcp/{mcp_id}` | 更新 MCP（启用/禁用、改名） |
| DELETE | `/mcp/{mcp_id}` | 删除 MCP |

### Skill 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/skill` | 列出已安装 Skill |
| POST | `/skill` | 添加 Skill |
| GET | `/skill/{skill_id}` | 获取单个 Skill |
| DELETE | `/skill/{skill_id}` | 删除 Skill |

## 会话与持久化

### 架构

```
SessionManager
  ├── 内存缓存: (user_id, session_id) → SessionEntry
  └── Redis
      ├── agentscope:session:{user_id}:{session_id}      → AgentState JSON (TTL 1800s)
      └── agentscope:session:{user_id}:{session_id}:meta → 会话元数据 JSON (TTL 1800s)
          {session_id, user_id, title, created_at, last_active, message_count}

PostgresStorage (PostgreSQL)
  └── DatabaseManager asyncpg 连接池
      ├── users 表           — 用户元数据
      ├── conversations 表   — 对话历史（user_id, session_id, role, content, metadata）
      ├── sessions 表        — 会话记录（parent_session_id, depth）
      ├── agents 表          — Agent 配置
      ├── mcps 表            — MCP 配置
      └── skills 表          — Skill 配置
```

### 关键流程

1. **会话创建**: 先查 Redis，命中则恢复 AgentState，未命中则新建
2. **会话回复**: reply/stream 完成后，调用 `session_mgr.save()` 同时写入 AgentState 与 `:meta` 元数据，异步写入 PG
3. **会话过期**: 内存中 TTL 过期后清理，Redis 中 key 保留至 TTL 到期
4. **会话列表**: `list_sessions()` 仅返回内存中活跃会话；`list_user_sessions()` 通过 SCAN 遍历 Redis `:meta` key 返回历史会话
5. **消息历史**: `get_session_messages()` 从 Redis 加载 AgentState，提取 user/assistant 文本消息返回
6. **会话删除**: `delete_session()` 彻底删除（内存 + Redis AgentState + Redis 元数据）

### Redis 配置

```yaml
redis:
  url: "${REDIS_URL:-redis://localhost:6379/0}"
  key_prefix: "agentscope:session:"
  session_ttl: 1800    # 秒
```

### PostgreSQL 配置

```yaml
database:
  url: "${DATABASE_URL:-postgresql://user:password@localhost:5432/ragdb}"
  pool_size: 10
```

`DatabaseManager` 在 `server.py` lifespan 中初始化，启动时自动执行幂等 DDL（建表 + 索引），未配置 URL 时跳过。提供 `execute / fetch / fetchrow / fetchval` 通用查询接口，以及 `insert_conversation / get_conversation_history` 便捷方法。

## 健康检查

```bash
# 一键检查所有组件
.venv/Scripts/python.exe health_check/check_all.py

# 单独检查
.venv/Scripts/python.exe health_check/check_http.py
.venv/Scripts/python.exe health_check/check_redis.py
.venv/Scripts/python.exe health_check/check_postgres.py
.venv/Scripts/python.exe health_check/check_llm.py
```

## 相关文档

| 文档 | 说明 |
|------|------|
| [docs/agentscope-guide.md](docs/agentscope-guide.md) | AgentScope API 速查与使用指南 |
| [docs/development-guide.md](docs/development-guide.md) | 开发指南 |
| [docs/deployment-guide.md](docs/deployment-guide.md) | 部署与运维指南 |
| [docs/persistence-design.md](docs/persistence-design.md) | 会话持久化设计 |
| [docs/health-check-plan.md](docs/health-check-plan.md) | 健康检查工具规划 |
| [docs/refactor-plan.md](docs/refactor-plan.md) | main.py 拆分重构规划 |
| [docs/webui-plan.md](docs/webui-plan.md) | 新版 WebUI 设计 |
| [docs/websocket-plan.md](docs/websocket-plan.md) | WebSocket 通道设计 |
| [docs/sandbox-plan.md](docs/sandbox-plan.md) | 沙箱隔离设计 |
| [docs/tool-middleware-plan.md](docs/tool-middleware-plan.md) | 工具守卫设计 |
| [docs/dangerous-commands.md](docs/dangerous-commands.md) | 命令安全守卫说明 |
| [docs/docker-deployment.md](docs/docker-deployment.md) | Docker 容器化部署指南 |
| [docs/sandbox-guide.md](docs/sandbox-guide.md) | 沙箱隔离指南 |
