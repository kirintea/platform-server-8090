# 对话持久化与历史会话设计

## 概述

本项目采用 **Redis + PostgreSQL** 双存储架构，实现对话状态的快速恢复和历史消息的长期保存。

| 存储层 | 职责 | 特点 |
|--------|------|------|
| **Redis** | 会话状态（AgentState）、会话元数据、消息总线 | 高速读写、自动过期、支持分布式 |
| **PostgreSQL** | 对话历史（conversations）、会话记录（sessions）、资源管理 | 持久化存储、复杂查询、事务支持 |

## 存储架构

```
┌─────────────────────────────────────────────────────────────┐
│                      SessionManager                         │
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │   内存缓存       │    │          Redis                   │ │
│  │ (user_id,        │    │  ┌───────────────────────────┐  │ │
│  │  session_id)     │    │  │ agentscope:session:       │  │ │
│  │   → SessionEntry │    │  │   {user_id}:{session_id}  │  │ │
│  │     ├─ agent     │◄──►│  │   → AgentState JSON       │  │ │
│  │     ├─ created_at│    │  │   (TTL: 1800s)            │  │ │
│  │     └─ last_active│   │  └───────────────────────────┘  │ │
│  └─────────────────┘    │  ┌───────────────────────────┐  │ │
│                         │  │ agentscope:session:       │  │ │
│                         │  │   {user_id}:{session_id}  │  │ │
│                         │  │   :meta                   │  │ │
│                         │  │   → 会话元数据 JSON        │  │ │
│                         │  │   (TTL: 1800s)            │  │ │
│                         │  └───────────────────────────┘  │ │
│                         └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ PG 回填（Redis 未命中时）
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PostgresStorage                           │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  conversations 表 — 对话历史                             ││
│  │  ┌──────┬────────┬────────┬─────────┬─────────────────┐ ││
│  │  │ id   │ user_id│session │  role   │    content       │ ││
│  │  │      │        │  _id   │         │                  │ ││
│  │  ├──────┼────────┼────────┼─────────┼─────────────────┤ ││
│  │  │ BIGSERIAL │ VARCHAR(64) │ VARCHAR(64) │ VARCHAR(16) │ TEXT ││
│  │  └──────┴────────┴────────┴─────────┴─────────────────┘ ││
│  │  + metadata (JSONB) — thinking, tool_calls 等            ││
│  │  + status (软删除: active/deleted)                       ││
│  └─────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────┐│
│  │  sessions 表 — 会话记录                                  ││
│  │  id, user_id, agent_id, config, state_json,             ││
│  │  parent_session_id, depth                                ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

## Redis 存储详情

### Key 结构

```
agentscope:session:{user_id}:{session_id}        → AgentState JSON (TTL 1800s)
agentscope:session:{user_id}:{session_id}:meta   → 会话元数据 JSON (TTL 1800s)
agentscope:session:{session_id}:status           → 会话实时状态 JSON (TTL 300s)  ← 多端同步
agentscope:session:{session_id}:control          → cancel 指令 JSON (TTL 60s)     ← 多端同步
```

### AgentState 内容

```json
{
  "session_id": "xxx",
  "context": [
    {
      "role": "user",
      "name": "user",
      "content": [{"type": "text", "text": "你好"}]
    },
    {
      "role": "assistant",
      "name": "assistant",
      "content": [{"type": "text", "text": "你好！有什么可以帮你的？"}]
    }
  ],
  "permission_context": {...}
}
```

### 会话元数据内容

```json
{
  "session_id": "xxx",
  "user_id": "user_001",
  "title": "你好",
  "created_at": 1691234567.89,
  "last_active": 1691234890.12,
  "message_count": 4,
  "parent_session_id": null,
  "forked_at": null
}
```

### 会话实时状态（多端同步）

用于多端并发场景，设备B发消息时可立即得知"有人在说话"（不 spin-wait）。

```json
// agentscope:session:{session_id}:status
{
  "state": "generating",      // idle | generating | interrupting
  "owner": "web-a1b2c3-phone", // 持有者设备标识
  "started_at": 1693123456.789,
  "user_msg_preview": "帮我写一个..."
}
```

```json
// agentscope:session:{session_id}:control
{
  "action": "cancel",         // 取消指令
  "from": "web-d4e5f6-desktop", // 发起取消的设备
  "at": 1693123460.123
}
```

## PostgreSQL 存储详情

### conversations 表

```sql
CREATE TABLE conversations (
    id          BIGSERIAL PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    session_id  VARCHAR(64) NOT NULL,
    role        VARCHAR(16) NOT NULL,        -- user/assistant/system/tool
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT NULL,           -- thinking, tool_calls 等
    status      VARCHAR(16) DEFAULT 'active', -- 软删除
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_conv_user_session ON conversations(user_id, session_id) WHERE status = 'active';
CREATE INDEX idx_conv_created ON conversations(created_at);
CREATE INDEX idx_conv_user_time ON conversations(user_id, created_at DESC) WHERE status = 'active';
```

### sessions 表

```sql
CREATE TABLE sessions (
    id                  VARCHAR(64) PRIMARY KEY,
    user_id             VARCHAR(64) NOT NULL,
    agent_id            VARCHAR(32) NOT NULL,
    source              VARCHAR(16) DEFAULT 'user',  -- user/fork
    config              JSONB DEFAULT NULL,           -- 包含 title
    state_json          TEXT DEFAULT '',
    parent_session_id   VARCHAR(64),                  -- Fork 血缘
    depth               INTEGER DEFAULT 0,            -- Fork 深度
    status              VARCHAR(16) DEFAULT 'active',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_sessions_user_agent ON sessions(user_id, agent_id) WHERE status = 'active';
CREATE INDEX idx_sessions_parent ON sessions(parent_session_id) WHERE parent_session_id IS NOT NULL;
```

## 会话生命周期

### 1. 创建会话

```
用户请求 (user_id, session_id)
    │
    ▼
SessionManager.get_or_create()
    │
    ├─ 内存缓存命中？ ──是──► 返回缓存的 Agent
    │
    └─ 未命中
        │
        ├─ Redis 有 AgentState？ ──是──► 恢复 AgentState，创建 Agent
        │
        └─ Redis 无
            │
            ├─ PG 有对话历史？ ──是──► _backfill_from_pg() 回填最近 N 条消息
            │                          创建 AgentState，写入 Redis
            │
            └─ PG 无 ───────────────► 创建全新 Agent（空状态）
```

### 2. 对话流程

```
用户发送消息（含 device_id）
    │
    ▼
POST /chat/stream 或 POST /chat/
    │
    ├─ 【多端同步】检查 SessionStatusTracker
    │   ├─ generating → 返回 409 Conflict（设备B告知"有人在说话"）
    │   └─ idle → 继续
    │
    ├─ 获取分布式锁（acquire_session_lock）
    │
    ├─ 【多端同步】标记 set_generating(session_id, device_id)
    │
    ├─ 获取/创建 Agent（get_or_create）
    │
    ├─ 多实例场景：refresh_state() 从 Redis 刷新最新状态
    │
    ├─ 执行 agent.reply_stream(user_msg)
    │   │
    │   ├─ 流式输出 text_delta / thinking_delta
    │   ├─ 工具调用 tool_call / tool_result
    │   ├─ 【多端同步】每 5 个事件检查 should_cancel() → 中断
    │   └─ 回复完成 reply_end
    │
    └─ 持久化（异步，不阻塞响应）
        │
        ├─ 【多端同步】标记 set_idle(session_id)
        │
        ├─ Redis: session_mgr.save()
        │   ├─ 序列化 AgentState → Redis
        │   └─ 保存会话元数据（title, message_count）
        │
        └─ PG: db.insert_conversation()
            ├─ 插入 user 消息
            ├─ 插入 assistant 消息（含 thinking/tool_calls metadata）
            └─ 更新 sessions 表标题
```

### 3. 历史会话恢复

```
用户切换到历史会话
    │
    ▼
前端调用 GET /sessions/{user_id}/{session_id}/messages
    │
    ▼
从 PG conversations 表查询（游标分页）
    │
    ▼
返回消息列表（user/assistant 角色，跳过 thinking/tool_call）
```

### 4. 继续历史对话

```
用户在历史会话中发送新消息
    │
    ▼
POST /chat/stream (session_id=历史会话ID)
    │
    ▼
SessionManager.get_or_create()
    │
    ├─ 内存中有 Agent？ ──是──► 直接使用（上下文已包含历史）
    │
    └─ 内存无
        │
        ├─ Redis 有 AgentState？ ──是──► 恢复（包含完整上下文）
        │
        └─ Redis 无
            │
            └─ _backfill_from_pg()
                │
                ├─ 从 PG 加载最近 20 条消息
                ├─ 转换为 UserMsg / AssistantMsg 对象
                ├─ 注入工具调用记录（用于 Agent 自我排错）
                └─ 构造 AgentState，写入 Redis
```

## 会话分支（Fork）

Fork 创建一个独立的子会话，复制父会话的完整上下文：

```
POST /sessions/{user_id}/{session_id}/fork
    │
    ▼
SessionManager.fork_session()
    │
    ├─ 1. 从 Redis 拷贝父会话 AgentState JSON → 子会话 key
    │
    ├─ 2. 拷贝父会话元数据，追加 parent_session_id / forked_at
    │
    └─ 3. PostgresStorage.fork_session()
        ├─ 读取父会话的 agent_id / config / state_json / depth
        ├─ 创建子会话：parent_session_id=父ID, depth=父depth+1
        └─ source = FORK
```

## 消息持久化流程

### 流式对话 (`POST /chat/stream`)

```python
# api/chat.py

async def event_generator():
    # ... 流式输出事件 ...

    finally:
        # 后台异步持久化（不阻塞流式响应）
        asyncio.create_task(_persist_conversation(
            session_mgr, db, user_id, session_id,
            user_message, full_reply,
            thinking=full_thinking,
            tool_calls=tool_call_records,
        ))
```

### Fire-and-Forget (`POST /chat/`)

```python
# core/chat_service.py

async def run(user_id, session_id, message):
    # 获取 session lock
    async with self._bus.acquire_lock(lock_key):
        # 执行流式回复
        async for event in agent.reply_stream(user_msg):
            # 发布事件到消息总线
            await self._publish_event(events_key, event)

        # 持久化会话状态
        await self._session_mgr.save(user_id, session_id)
```

## 查询接口

### 会话列表

```python
# 从 PG conversations 聚合 + sessions 获取自定义标题
GET /sessions/{user_id}
    → SELECT session_id, MIN(created_at), MAX(created_at), COUNT(*),
             sessions.config->>'title'
      FROM conversations
      LEFT JOIN sessions ON ...
      GROUP BY session_id
      ORDER BY last_active DESC
```

### 消息历史

```python
# 从 PG conversations 查询，支持游标分页
GET /sessions/{user_id}/{session_id}/messages?before_id=xxx&limit=50
    → SELECT id, role, content, metadata, created_at
      FROM conversations
      WHERE user_id = $1 AND session_id = $2 AND status = 'active'
      ORDER BY id DESC
      LIMIT $3
```

### 上下文用量

```python
GET /sessions/{user_id}/{session_id}/context
    → 从 Redis 加载 AgentState
    → 估算 token 数（中文约 1.5 token/字，英文约 0.25 token/word）
    → 返回 usage_ratio, status (healthy/warning/critical)
```

## 配置项

```yaml
# configs/dev.yaml
redis:
  url: "${REDIS_URL:-redis://localhost:6379/0}"
  key_prefix: "agentscope:session:"
  session_ttl: 1800    # 秒（30 分钟）

database:
  url: "${DATABASE_URL:-postgresql://user:password@localhost:5432/ragdb}"
  pool_size: 10

context:
  backfill_message_limit: 20  # PG 回填时加载的消息条数上限
```

## 数据流总结

```
┌──────────┐     ┌──────────┐     ┌──────────────┐
│  前端    │────►│  API     │────►│ SessionManager│
│ (多设备) │◄────│  Router  │◄────│              │
└──────────┘     └──────────┘     └──────┬───────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
              ┌──────────┐        ┌──────────┐        ┌──────────┐
              │  内存    │        │  Redis   │        │    PG    │
              │  缓存    │◄──────►│          │◄──────►│          │
              └──────────┘        └──────────┘        └──────────┘
              AgentState          AgentState           conversations
              SessionEntry        元数据               sessions
                                  消息总线             agents/mcps/skills
                                  :status (多端同步)
                                  :control (多端中断)
```

## 关键设计决策

1. **Redis 作为热存储**：会话状态保存在 Redis，支持快速恢复和分布式部署
2. **PG 作为冷存储**：对话历史持久化到 PostgreSQL，支持复杂查询和长期保存
3. **PG 回填机制**：Redis 未命中时从 PG 加载历史消息，确保会话可恢复
4. **异步持久化**：对话结束后异步写入存储，不阻塞用户响应
5. **软删除**：PG 使用 status 字段标记删除，实际删除由数据部门处理
6. **Fork 机制**：支持从任意会话创建分支，复制完整上下文
7. **多端同步**：SessionStatusTracker 通过 Redis 广播会话状态（对讲机模型），设备B发消息时 409 拒绝而非 spin-wait；任意设备可通过 /interrupt 中断当前生成
