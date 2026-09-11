# AgentScope Platform Server

用 AgentScope 2.0.7 搭的对话智能体服务端。一句话定位：把 Agent 当服务跑，而不是在本地起个 demo 脚本就收工。多用户、多实例、状态外置、工具调用全程有守卫——这几个是当时卡我的点，下面会具体说。

## 写这东西的起因

开源的 Agent 框架大多是个人向的。会话历史往本地 JSON 一丢，单机跑通就算完事。真要按服务来用，多用户加分布式一上来就撞两堵墙。

一是会话。本地文件撑不住多实例，所以我拆成两层：Redis 放会话状态和元数据，读写快、天然分布式；PostgreSQL 归档对话历史，理论上换 MySQL 或 MongoDB 也接得上。这套分层后来证明是对的——热数据和冷数据的访问模式差太多，搅一起反而更麻烦。

二是安全。Agent 能调工具、能执行命令，提示词注入绕不过去。我放了三道闸：`command_guard` 拦危险命令，`path_guard` 把文件操作锁进沙箱，不想让 Agent 碰的工具直接在 `configs/tools.yaml` 里关掉。别指望靠 prompt 说一句"你不准干坏事"就安全，得在工具层物理拦。

顺带一提，这也是我学 AgentScope 的练手项目。写到一半翻了下 Java 版，生产相关的配套（限流、可观测、部署）比 Python 版齐全不少，缺的部分只能自己补。所以这个 repo 一半是学习笔记，一半是补丁。


整个 8090 端口是个 FastAPI 应用。`main.py` 只做启动那点事：加载配置、初始化日志和 OTel，然后拉起 uvicorn。`server.py` 是应用本体，`create_app(config)` 工厂函数配 lifespan 资源管理，路由统一在这注册。平台层自带 SessionManager、DatabaseManager、RedisMessageBus、PostgresStorage、ChatService 这一套。

会话 key 统一走 `agentscope:session:` 前缀，每个会话的工作目录在 `workspaces/{user_id}/{session_id}/`。

中间件三件套，都是给工具调用兜底用的：

- `tool_guard.py` — 工具名级黑白名单，控制 Agent 能碰哪些工具
- `command_guard.py` — 命令内容级检查，拦 `rm -rf /`、反弹 shell 那类
- `path_guard.py` — 路径访问守卫，把文件操作圈在沙箱里

## 特性

- **多用户会话隔离** — 每个 (user_id, session_id) 维护独立 Agent 状态
- **会话分支（Fork）** — 基于已有会话创建分支，父子状态完全独立，支持多级 fork
- **多实例无状态** — RedisMessageBus 实现分布式锁、Pub/Sub 事件广播、回放日志，支持多进程部署
- **流式输出** — SSE + WebSocket 双通道实时推送文本、思考过程、工具调用事件
- **工具调用** — 内置 Bash/Read/Write/Edit/Glob/Grep + TaskCreate/TaskList/TaskGet/TaskUpdate
- **工具守卫** — 工具名级黑白名单（tool_guard）+ 命令内容级安全守卫（command_guard）
- **命令安全** — 命令内容级黑白名单，拦截危险命令（rm -rf /、管道执行、反弹 shell 等）
- **Redis 持久化** — 会话状态 + 元数据自动保存到 Redis，支持历史会话列表与消息回放
- **PostgreSQL 持久化** — `DatabaseManager` + `PostgresStorage` 管理 asyncpg 连接池，启动自动建表，归档对话历史与用户元数据
- **OTel 追踪** — 集成 OpenTelemetry，支持 Jaeger/Grafana 可视化
- **MCP 扩展** — 支持 stdio/http 两种 MCP 服务接入
- **自定义工作流** — WorkflowBase 抽象类，支持业务流程定制
- **SiliconFlow 兼容** — `core/formatter/SiliconFlowFormatter` 自动扁平化 content list 格式
- **新版 WebUI** — React 19 SPA，支持中英双语、会话管理、MCP/Skill 管理

## 快速开始

### 1. 环境准备

```bash
# Python 3.12+
uv sync
# 或
pip install -r requirements.txt
```

### 2. 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入 API Key
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_MODEL_NAME=Qwen/Qwen3.6-35B-A3B
```

### 3. 启动依赖服务

```bash
# 启动 Redis（会话持久化）+ PostgreSQL（对话历史）+ 可观测性栈（OTel + Jaeger + Grafana）
cd docker && docker-compose up -d

# 或仅启动单个依赖（模块化 compose 文件位于 docker/deploy_yml/）
docker-compose -f docker/deploy_yml/redis.yml up -d
docker-compose -f docker/deploy_yml/postgres.yml up -d
```

只想起单个也行，模块化的 compose 在 `docker/deploy_yml/`。默认连接：Redis 在 `redis://localhost:6379/0`，PostgreSQL 在 `postgresql://user:password@localhost:5432/ragdb`。

然后拉起服务：

```bash
APP_ENV=dev python main.py
# 或者用启动脚本
./scripts/start_8090.sh
```

### 5. 访问

| 前端 | 地址 | 说明 |
|------|------|------|
| 新版 WebUI | http://localhost:8090/webui | React 19 SPA（需先构建：`cd webui && npm run build`） |
| 旧版静态界面 | http://localhost:8090/ | api/static/index.html |
| Swagger 文档 | http://localhost:8090/docs | API 文档 |

## 服务架构

### main.py + server.py (端口 8090) — 自有平台层

- `main.py` — 启动入口：配置加载、日志初始化、OTel 初始化、uvicorn 启动
- `server.py` — FastAPI 应用：`create_app(config)` 工厂函数、lifespan 资源管理、路由注册
- 自有 SessionManager、DatabaseManager、RedisMessageBus、PostgresStorage、ChatService
- 会话存储：Redis `agentscope:session:*` 前缀
- 工作区：`workspaces/{user_id}/{session_id}/`

### 核心模块

| 模块 | 说明 |
|------|------|
| `core/session.py` | 会话管理器（Redis 持久化 + fork + refresh_state） |
| `core/chat_service.py` | Chat 服务层（Fire-and-Forget 模式，事件驱动） |
| `core/database.py` | PostgreSQL 管理器（asyncpg 连接池 + 自动建表） |
| `core/storage.py` | PostgreSQL 存储层（Agent/Session/MCP/Skill/Message CRUD） |
| `core/storage_models.py` | 数据模型定义（AgentRecord/MCPRecord/SkillRecord 等） |
| `core/redis_message_bus.py` | Redis 分布式消息总线（分布式锁 + Pub/Sub） |
| `core/workspace.py` | 自有 LocalWorkspaceManager（base_dir="./workspaces"） |
| `core/config/` | 配置加载（YAML + 环境变量） |
| `core/formatter/` | 自定义 Formatter（SiliconFlow 兼容） |
| `core/tracing/` | OTel 追踪初始化 |

### 中间件

| 模块 | 说明 |
|------|------|
| `middleware/tool_guard.py` | 工具名级黑白名单（控制可调用工具范围） |
| `middleware/command_guard.py` | 命令内容级安全守卫（拦截危险命令） |
| `middleware/path_guard.py` | 路径访问守卫 |
| `middleware/tool_manager.py` | 工具管理器（从 configs/tools.yaml 选择性加载） |

## API 端点（8090）

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

### 文档

| 路径 | 说明 |
|------|------|
| `/docs` | Swagger API 文档 |
| `/redoc` | ReDoc API 文档 |

### 流式对话事件类型

| 事件 | 说明 |
|------|------|
| `session` | 会话 ID（首条事件） |
| `text_delta` | 文本增量 |
| `thinking_delta` | 思考过程增量 |
| `tool_call` | 工具调用开始 |
| `tool_result` | 工具执行结果 |
| `reply_end` | 回复结束 |
| `error` | 错误 |

### WebSocket 消息协议

客户端 → 服务端：
- `{"type": "chat", "payload": {"message": "..."}}` — 发送消息
- `{"type": "cancel", "payload": {}}` — 取消回复
- `{"type": "ping", "payload": {}}` — 心跳

服务端 → 客户端：
- `{"type": "text_delta", "payload": {"delta": "..."}}` — 文本增量
- `{"type": "thinking_delta", "payload": {"delta": "..."}}` — 思考增量
- `{"type": "tool_call", "payload": {"tool_name", "tool_call_id", "tool_args"}}` — 工具调用
- `{"type": "tool_result", "payload": {"tool_call_id", "state", "result"}}` — 工具结果
- `{"type": "reply_end", "payload": {"finished_reason", "finished": true}}` — 回复结束

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
│   ├── config/                # 配置加载（YAML + 环境变量 + Pydantic Schema）
│   ├── database.py            # PostgreSQL 管理器（asyncpg 连接池 + 自动建表）
│   ├── storage.py             # PostgreSQL 存储层（Agent/Session/MCP/Skill CRUD）
│   ├── storage_models.py      # 数据模型定义
│   ├── chat_service.py        # Chat 服务层（Fire-and-Forget 事件驱动模式）
│   ├── session.py             # 会话管理器（Redis 持久化 + fork + refresh_state）
│   ├── redis_message_bus.py   # Redis 分布式消息总线（分布式锁 + Pub/Sub）
│   ├── message_bus.py         # 消息总线抽象
│   ├── formatter/             # 自定义 Formatter（SiliconFlow 兼容）
│   ├── workspace.py           # 自有 LocalWorkspaceManager
│   ├── log/                   # 日志初始化（loguru）
│   └── tracing/               # OTel 追踪初始化
├── middleware/
│   ├── tool_guard.py          # 工具名级黑白名单中间件
│   ├── command_guard.py       # 命令内容级安全守卫
│   ├── path_guard.py          # 路径访问守卫
│   └── tool_manager.py        # 工具管理器（从 tools.yaml 加载）
├── webui/                     # 新版 React 19 SPA（/webui）
│   ├── src/                   # 源码（React + TypeScript + TailwindCSS）
│   ├── dist/                  # 构建产物（由 server.py 直接服务）
│   ├── package.json           # 前端依赖
│   └── vite.config.ts         # Vite 构建配置
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
│   ├── deploy_yml/            # 模块化部署 compose（dev/各数据库独立）
│   └── exports/               # 导出的 Docker 镜像 tar 包
├── scripts/
│   └── start_8090.sh          # 启动 8090 自有平台层
├── health_check/              # 健康检查工具集（可单独执行）
├── skills/                    # 自定义 Skill 目录
├── docs/                      # 设计文档
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目依赖
└── requirements.txt           # 锁定依赖
```

## 配置说明

配置文件位于 `configs/`，通过 `APP_ENV` 环境变量选择环境。

### LLM 配置

```yaml
llm:
  api_key: "${LLM_API_KEY}"
  base_url: "https://api.siliconflow.cn/v1"
  model: "Qwen/Qwen3.6-35B-A3B"
  stream: true
  context_size: 128000
  max_tokens: 4096
  temperature: 0.7
```

### Redis 配置

```yaml
redis:
  url: "${REDIS_URL:-redis://localhost:6379/0}"
  key_prefix: "agentscope:session:"
  session_ttl: 1800    # 会话过期时间（秒）
```

Redis 中每个会话存储两类 key（共享 TTL）：
- `agentscope:session:{user_id}:{session_id}` — AgentState JSON
- `agentscope:session:{user_id}:{session_id}:meta` — 会话元数据（标题、消息数、时间），供 `/sessions/{user_id}` 列表扫描

### PostgreSQL 配置

```yaml
database:
  url: "${DATABASE_URL:-postgresql://user:password@localhost:5432/ragdb}"
  pool_size: 10
```

`DatabaseManager` 在服务启动时初始化 asyncpg 连接池并执行幂等 DDL（自动建 `users` / `conversations` / `sessions` 等表及索引），未配置 `url` 时跳过。

### 环境变量

所有外部依赖地址均支持 `${VAR:-default}` 语法，可在 `.env` 中覆盖（参考 `.env.example`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | — | LLM API 密钥（必填） |
| `LLM_BASE_URL` | — | LLM 接口地址（必填，参考 `.env.example`） |
| `LLM_MODEL_NAME` | `glm-5` | 模型名 |
| `OTEL_ENDPOINT` | `http://localhost:4317` | OTel OTLP gRPC 端点 |
| `DATABASE_URL` | `postgresql://user:password@localhost:5432/ragdb` | PostgreSQL 连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `APP_ENV` | `dev` | 环境（dev / prod） |

### 权限配置

```yaml
agent:
  permission_mode: "bypass"   # bypass = 自动批准所有工具调用
  tool_guard:
    enabled: false
    mode: "blocklist"          # blocklist / allowlist
    tools: []                  # 需要拦截的工具名
  command_guard:
    enabled: true
    mode: "blocklist"          # blocklist / allowlist
    rules:
      - "rm -rf /"             # 拦截删根目录
      - "*/dev/tcp/*"          # 拦截反弹 shell
      - "Invoke-Expression*"   # 拦截 PowerShell 远程执行
```


## 多实例部署

项目支持多实例无状态部署，通过 RedisMessageBus 实现跨实例一致性：

- **分布式锁**：同 session 并发请求时，Redis SETNX 保证互斥
- **Pub/Sub 事件广播**：SSE 事件跨实例分发
- **状态刷新**：每次 ChatService.run() 开始前从 Redis 加载最新 AgentState
- **回放日志**：Redis List 存储事件日志，支持新订阅者追赶历史

## 可观测性

启动 Docker 可观测性栈后：

- **Jaeger**: http://localhost:16686 — 查看分布式追踪
- **Grafana**: http://localhost:3000 (admin/admin) — 统一监控大盘
- **Prometheus**: http://localhost:9090 — 指标查询

TracingMiddleware 自动记录：
- LLM 调用的输入/输出消息
- 工具调用的名称、参数、结果
- 每次请求的完整调用链路

## 健康检查

`health_check/` 下能单独跑，也能一把全查：

```bash
.venv/Scripts/python.exe health_check/check_all.py
# 单独查：check_http / check_redis / check_postgres / check_llm
```

## WebUI

基于 React 19 + TypeScript + TailwindCSS 构建的 SPA 前端：

- 路径：`webui/`
- 访问：http://localhost:8090/webui
- 功能：会话管理、MCP/Skill 管理、中英双语
- 技术栈：React 19, Vite 8, Radix UI, i18next

```bash
# 构建前端
cd webui && npm install && npm run build

# 开发模式（独立 dev server）
cd webui && npm run dev
```

## 项目结构

```
platform-server-8090/
├── main.py              # 启动入口
├── server.py            # FastAPI 应用
├── api/                 # 路由层（chat / ws / mcp / skill / 旧版静态界面）
├── core/                # 会话、存储、消息总线、formatter、workspace、tracing
│   ├── agent/factory.py # Agent 工厂
│   ├── config/          # 配置加载
│   ├── database.py      # PostgreSQL 管理器
│   ├── storage*.py      # 存储层 + 数据模型
│   ├── chat_service.py  # Chat 服务（事件驱动）
│   ├── session.py       # 会话管理器
│   ├── redis_message_bus.py
│   └── formatter/       # SiliconFlow 兼容
├── middleware/          # tool_guard / command_guard / path_guard / tool_manager
├── webui/               # React 19 SPA
├── workflow/base.py     # 工作流基类
├── workspaces/          # 沙箱与工作路径
├── configs/            # dev.yaml / prod.yaml / tools.yaml
├── docker/             # compose + 模块化部署 + 镜像导出
├── scripts/start_8090.sh
├── health_check/        # 健康检查脚本
├── skills/              # 自定义 Skill
├── docs/                # 设计文档
└── pyproject.toml / requirements.txt
```

## 文档索引

开发、部署、API 参考、各种设计规划都在 `docs/` 


## TODO LIST

- 补文档。
- 换个项目名。
- 重新写前端界面，当前刚好凑合能用。
- 守卫测试，测试样例不是很多，也不确定这是不是真有用。
- 服务实例和沙箱和用户之间的分配问题，计划是一个用户分一个沙箱，但这边验证资源有限，先标记一下吧。
