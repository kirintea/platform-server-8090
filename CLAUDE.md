# CLAUDE.md — AgentScope Platform Server 项目指引

## 项目概述

基于 **AgentScope 2.0.5** 构建的 AI Agent 平台服务，使用 FastAPI 提供 HTTP 接口。

- **Python**: >=3.12
- **核心依赖**: agentscope 2.0.5, fastapi, uvicorn, dashscope, openai, anthropic
- **文档**: https://docs.agentscope.io/
- **源码**: https://github.com/agentscope-ai/agentscope

## 项目结构

```
platform-server-8090/
├── main.py                    # FastAPI 入口 (端口 8090) — 自有平台服务（SessionManager + DatabaseManager）
├── api/
│   ├── chat.py                # 对话 API 路由（/chat, /chat/stream, /sessions/*）
│   └── static/index.html      # 前端对话界面（侧边栏会话历史）
├── core/
│   ├── agent/factory.py       # Agent 工厂（模型/工具/中间件组装）
│   ├── config/                # 配置加载（YAML + 环境变量）
│   │   └── schemas.py         # Pydantic Schema
│   ├── database.py            # PostgreSQL 管理器（asyncpg 连接池 + 自动建表）
│   ├── formatter/             # 自定义 Formatter（SiliconFlow 兼容）
│   │   ├── __init__.py
│   │   └── siliconflow.py     # SiliconFlowFormatter（content list 扁平化）
│   ├── session.py             # 会话管理器（Redis 持久化 + 元数据 + 消息历史）
│   ├── workspace.py           # 自有 LocalWorkspaceManager（base_dir="./workspaces"）
│   └── tracing/               # OTel 追踪初始化
├── middleware/
│   └── tool_guard.py          # 工具黑白名单中间件
├── workflow/
│   └── base.py                # 自用工作流基类
├── workspaces/                # 统一沙箱与工作路径
│   └── {agent_id}/            # Agent 工作区（skills/.mcp 等）
├── configs/
│   ├── dev.yaml               # 开发环境配置
│   └── prod.yaml              # 生产环境配置
├── docker/
│   ├── docker-compose.yaml    # 可观测性栈 + Redis + PostgreSQL
│   ├── deploy_yml/            # 模块化部署 compose 文件（dev/各数据库独立）
│   ├── exports/               # 导出的 Docker 镜像 tar 包
│   └── *.yaml / *.yml         # OTel/Prometheus/Loki 配置
├── docs/                      # 设计文档
├── skills/                    # 自定义 Skill 目录
├── scripts/
│   └── start_8090.sh          # 启动 8090 自有平台层（VENV_PATH 可配置）
├── .env                       # 环境变量（LLM_API_KEY 等）
├── pyproject.toml             # 项目依赖
└── requirements.txt           # 锁定依赖
```

## AgentScope 核心 API 速查

### 模块导入

```python
# Agent 核心
from agentscope.agent import Agent, ReActConfig, ContextConfig, ModelConfig, InjectionConfig

# 模型层
from agentscope.model import OpenAIChatModel, DashScopeChatModel, AnthropicChatModel
from agentscope.credential import OpenAICredential, DashScopeCredential, AnthropicCredential

# 消息类型
from agentscope.message import Msg, UserMsg, AssistantMsg, SystemMsg
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock, ThinkingBlock

# 工具系统
from agentscope.tool import Toolkit, Bash, Read, Write, Edit, Glob, Grep
from agentscope.tool import TaskCreate, TaskList, TaskGet, TaskUpdate
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig

# Skill 系统
from agentscope.skill import Skill, LocalSkillLoader

# 中间件
from agentscope.middleware import TracingMiddleware, RAGMiddleware, AgenticMemoryMiddleware

# 事件系统
from agentscope.event import EventType, AgentEvent

# 权限系统
from agentscope.permission import PermissionMode, PermissionBehavior

# 工作区/沙箱
from agentscope.workspace import DockerWorkspace, E2BWorkspace
```

### 创建 Agent（最简模式）

```python
from agentscope.agent import Agent
from agentscope.model import OpenAIChatModel
from agentscope.credential import OpenAICredential
from agentscope.tool import Toolkit, Bash, Read, Write

agent = Agent(
    name="my_agent",
    system_prompt="你是一个有帮助的助手。",
    model=OpenAIChatModel(
        credential=OpenAICredential(api_key="sk-xxx"),
        model="gpt-4o",
    ),
    toolkit=Toolkit(tools=[Bash(), Read(), Write()]),
)
```

### 调用 Agent

```python
from agentscope.message import UserMsg

# 非流式
msg = await agent.reply(UserMsg("user", "你好"))

# 流式
async for event in agent.reply_stream(UserMsg("user", "你好")):
    if hasattr(event, 'type'):
        print(f"Event: {event.type}")
```

### 消息构造

```python
from agentscope.message import UserMsg, AssistantMsg, SystemMsg

# 用户消息
msg = UserMsg(name="user", content="解释量子计算")

# 多模态消息（文本+图片）
msg = UserMsg(name="user", content=[
    TextBlock(text="描述这张图片"),
    DataBlock(source=URLSource(url="https://example.com/img.png")),
])
```

## 关键配置

### 环境变量（.env）

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

### ReAct 配置

```python
from agentscope.agent import ReActConfig

react_config = ReActConfig(
    max_iters=20,                    # 最大推理-行动循环次数
    stop_on_reject=False,            # 工具被拒绝时是否停止
    interruption_message="已中断",
)
```

### 上下文压缩配置

```python
from agentscope.agent import ContextConfig

context_config = ContextConfig(
    trigger_ratio=0.8,    # token 达到 80% 时触发压缩
    reserve_ratio=0.1,    # 保留 10% 的最近上下文
    tool_result_limit=50000,  # 工具结果最大 token 数
)
```

## 服务架构

### main.py (端口 8090) — 自有平台层

- **职责**：平台服务，自有 SessionManager、PostgresStorage、消息总线
- **状态**：保留，不改动，面向 `api/static/index.html` 前端
- **会话存储**：Redis `agentscope:session:*` 前缀
- **工作区**：`LocalWorkspaceManager(base_dir="./workspaces")` — 自有实现 `core/workspace.py`

### 访问入口

| 前端 | 地址 | 后端 |
|------|------|------|
| 旧版静态界面 | `http://localhost:8090/` | main.py (8090) |

### 统一沙箱与工作路径 — workspaces/

8090 的自有 `LocalWorkspaceManager(base_dir="./workspaces")` 共享同一 `workspaces/` 根目录：

- 8090 路径结构：`workspaces/{user_id}/{session_id}/`（自有 workspace.py `_get_workdir`）
- 旧目录 `webui_workspaces/` 已废弃，数据已迁移，目录已删除

## 常见模式

### 注册 MCP 工具

```python
from agentscope.mcp import MCPClient, StdioMCPConfig

mcp_client = MCPClient(
    config=StdioMCPConfig(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/path"],
    )
)
toolkit = Toolkit(mcps=[mcp_client])
```

### 使用 Skill

```python
from agentscope.skill import LocalSkillLoader

loader = LocalSkillLoader(directory="./skills", scan_subdir=True)
toolkit = Toolkit(skills_or_loaders=[loader])
```

### 添加中间件

```python
from agentscope.middleware import TracingMiddleware

agent = Agent(
    name="my_agent",
    system_prompt="...",
    model=model,
    middlewares=[TracingMiddleware()],
)
```

## 注意事项

1. **AgentScope 2.0 是异步优先的** — `reply()` 和 `reply_stream()` 都是 async 方法
2. **消息用 `UserMsg`/`AssistantMsg`/`SystemMsg`** — 不要直接用 dict
3. **Tool 用 Toolkit 管理** — 不要手动注册工具函数
4. **Skill 不是工具** — 需要通过 `SkillViewer` 工具在对话中检索
5. **上下文压缩自动触发** — 通过 `ContextConfig` 配置阈值
6. **权限系统默认开启** — 工具调用可能需要用户确认
7. **会话状态通过 Redis 持久化** — 每次 reply 后自动保存 AgentState + 会话元数据，重启后可恢复
8. **对话历史通过 PostgreSQL 持久化** — `DatabaseManager` 管理连接池，启动时自动建表（users / conversations）
9. **SiliconFlow Formatter 已模块化** — 位于 `core/formatter/`，不在 `factory.py` 内联

## 会话与持久化

### 架构

```
SessionManager
  ├── 内存缓存: (user_id, session_id) → SessionEntry
  └── Redis
      ├── agentscope:session:{user_id}:{session_id}      → AgentState JSON (TTL 1800s)
      └── agentscope:session:{user_id}:{session_id}:meta → 会话元数据 JSON (TTL 1800s)
          {session_id, user_id, title, created_at, last_active, message_count}

DatabaseManager (PostgreSQL)
  └── asyncpg 连接池
      ├── users 表           — 用户元数据
      └── conversations 表   — 对话历史（user_id, session_id, role, content, metadata）
```

### 关键流程

1. **会话创建**: 先查 Redis，命中则恢复 AgentState，未命中则新建
2. **会话回复**: reply/stream 完成后，调用 `session_mgr.save()` 同时写入 AgentState 与 `:meta` 元数据
3. **会话过期**: 内存中 TTL 过期后清理，Redis 中 key 保留至 TTL 到期
4. **会话列表**: `list_sessions()` 仅返回内存中活跃会话；`list_user_sessions()` 通过 SCAN 遍历 Redis `:meta` key 返回历史会话
5. **消息历史**: `get_session_messages()` 从 Redis 加载 AgentState，提取 user/assistant 文本消息返回
6. **会话删除**: `delete_session()` 彻底删除（内存 + Redis AgentState + Redis 元数据）

### Session API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | 列出内存中活跃会话（可选 user_id 过滤） |
| GET | `/sessions/{user_id}` | 列出用户所有历史会话（Redis 扫描 `:meta`） |
| GET | `/sessions/{user_id}/{session_id}/messages` | 获取会话消息历史 |
| DELETE | `/sessions/{user_id}/{session_id}` | 彻底删除会话（内存 + Redis） |
| POST | `/sessions/{user_id}/{session_id}/fork` | 基于父会话创建分支，返回新会话 ID |

### Fork / 会话分支

- **API**: `POST /sessions/{user_id}/{parent_session_id}/fork`
  返回: `{session_id, parent_session_id, title}`
- **存储**:
  - Redis: 父 `{prefix}{uid}:{parent_sid}` JSON → 子 `{prefix}{uid}:{child_sid}`（值拷贝，完全独立）
  - Redis meta: 子 meta 带 `parent_session_id` 标记
  - PostgreSQL `sessions`: `parent_session_id` FK 指向父, `depth` = 父 depth+1, `source='fork'`
- **多实例一致性**:
  - MessageBus 统一使用 `RedisMessageBus(url=config.redis.url)`；无任何进程内状态
  - ChatService.run() 每轮开始 refresh_state 从 Redis 加载最新 AgentState
  - SessionManager 内存缓存 _sessions 仅为性能 LRU；锁、回放日志、事件分发均走 Redis

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

`DatabaseManager` 在 `main.py` lifespan 中初始化，启动时自动执行幂等 DDL（建表 + 索引），未配置 URL 时跳过。提供 `execute / fetch / fetchrow / fetchval` 通用查询接口，以及 `insert_conversation / get_conversation_history` 便捷方法。

### AgentState 序列化

```python
# 保存
state_json = agent.state.model_dump_json()
await redis.set(key, state_json, ex=session_ttl)

# 恢复
state = AgentState.model_validate_json(state_json)
agent = Agent(..., state=state)
```

### 注意事项

- AgentState 是 Pydantic BaseModel，支持 JSON 序列化/反序列化
- MCP 客户端、Workspace 等运行时对象不序列化，恢复后需重新创建
- Redis 连接失败时降级为纯内存模式（不持久化）
- 会话元数据 `:meta` 与 AgentState 共享 TTL，独立 key 存储，便于 SCAN 扫描列表
- PostgreSQL 用于长期对话历史归档，与 Redis 热会话状态分工（详见 `docs/persistence-design.md`）

## 源码参考

本地安装路径：`.venv/Lib/site-packages/agentscope/`

| 模块 | 路径 | 说明 |
|------|------|------|
| agent | `agentscope/agent/` | Agent 类和配置 |
| model | `agentscope/model/` | LLM 模型封装 |
| message | `agentscope/message/` | 消息和内容块 |
| tool | `agentscope/tool/` | Toolkit 和内置工具 |
| skill | `agentscope/skill/` | Skill 加载系统 |
| middleware | `agentscope/middleware/` | 中间件 |
| event | `agentscope/event/` | 事件系统 |
| permission | `agentscope/permission/` | 权限控制 |
| workspace | `agentscope/workspace/` | 沙箱/工作区 |
| mcp | `agentscope/mcp/` | MCP 客户端 |
| credential | `agentscope/credential/` | API 认证 |
| formatter | `agentscope/formatter/` | 消息格式化 |