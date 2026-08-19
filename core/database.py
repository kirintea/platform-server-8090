# -*- coding: utf-8 -*-

"""PostgreSQL 数据库管理器 — 连接池 + DDL 自动迁移

设计：
- 使用 asyncpg 连接池管理数据库连接
- 启动时自动建表（幂等操作）
- 提供便捷的查询方法供 SessionManager 使用

使用方式：
    db = DatabaseManager(config)
    await db.initialize()  # 初始化连接池 + 建表
    await db.execute("INSERT INTO ...")
    rows = await db.fetch("SELECT * FROM ...")
    await db.shutdown()  # 关闭连接池
"""

from __future__ import annotations

import asyncpg

from core.config.schemas import DatabaseConfig

from loguru import logger

# ============================================================
# DDL 建表语句（幂等操作，可重复执行）
# ============================================================

DDL_STATEMENTS = [
    # 对话历史表（核心）
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        session_id  VARCHAR(64) NOT NULL,
        role        VARCHAR(16) NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB DEFAULT NULL,
        status      VARCHAR(16) NOT NULL DEFAULT 'active',
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 状态字段（软删除：active / deleted）
    """
    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'
    """,

    # JSONB 字段默认值改为 NULL（已有表）
    """
    ALTER TABLE conversations ALTER COLUMN metadata SET DEFAULT NULL
    """,
    """
    ALTER TABLE sessions ALTER COLUMN config SET DEFAULT NULL
    """,
    """
    ALTER TABLE mcps ALTER COLUMN config SET DEFAULT NULL
    """,
    """
    ALTER TABLE skills ALTER COLUMN data SET DEFAULT NULL
    """,

    # 索引：用户+会话查询（过滤状态）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_session
    ON conversations(user_id, session_id) WHERE status = 'active'
    """,

    # 索引：时间范围查询
    """
    CREATE INDEX IF NOT EXISTS idx_conv_created
    ON conversations(created_at)
    """,

    # 索引：用户最近对话（过滤状态）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_time
    ON conversations(user_id, created_at DESC) WHERE status = 'active'
    """,

    # 索引：按状态查询（供数据部门清理 deleted 记录）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_status
    ON conversations(status) WHERE status != 'active'
    """,

    # ============================================================
    # 服务层新表（Phase 1+）
    # ============================================================

    # Session 会话表
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        agent_id    VARCHAR(32) NOT NULL,
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        team_id     VARCHAR(32),
        config      JSONB DEFAULT NULL,
        state_json  TEXT NOT NULL DEFAULT '',
        status      VARCHAR(16) NOT NULL DEFAULT 'active',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 状态字段（软删除：active / deleted）
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_sessions_user_agent
    ON sessions(user_id, agent_id) WHERE status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_team
    ON sessions(team_id) WHERE team_id IS NOT NULL AND status = 'active'
    """,

    # 索引：按状态查询（供数据清理 deleted 记录）
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_status
    ON sessions(status) WHERE status != 'active'
    """,

    # MCP 已安装表
    """
    CREATE TABLE IF NOT EXISTS mcps (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        transport   VARCHAR(16) NOT NULL DEFAULT 'stdio',
        config      JSONB DEFAULT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mcps_user
    ON mcps(user_id)
    """,

    # Skill 已安装表
    """
    CREATE TABLE IF NOT EXISTS skills (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        data        JSONB DEFAULT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_skills_user
    ON skills(user_id)
    """,

    # 定时任务表
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        agent_id    VARCHAR(32) NOT NULL,
        session_id  VARCHAR(32),
        name        VARCHAR(256) NOT NULL,
        cron_expr   VARCHAR(64) NOT NULL,
        prompt      TEXT NOT NULL DEFAULT '',
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        last_run_at TIMESTAMPTZ,
        next_run_at TIMESTAMPTZ,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_schedules_user
    ON schedules(user_id)
    """,

    # ============================================================
    # 会话分支血缘（Fork 特性）
    # ============================================================

    # parent_session_id：父会话 ID（Fork 血缘），根会话为 NULL
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id VARCHAR(32)
    """,
    # depth：Fork 深度，根会话=0，每 fork 一次 +1
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS depth INTEGER NOT NULL DEFAULT 0
    """,
    # 索引：按父会话查询子分支（仅对非根会话生效）
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_parent
    ON sessions(parent_session_id) WHERE parent_session_id IS NOT NULL
    """,

    # ============================================================
    # 列宽迁移 — sessions.id / parent_session_id 扩容至 VARCHAR(64)
    # 原始 DDL 用 VARCHAR(32)，但 8090 API 层用 str(uuid.uuid4())（36 字符）
    # 生成 session_id，超出 32 字符上限。conversations.session_id 已是 VARCHAR(64)，
    # 此处对齐。ALTER ... TYPE 是幂等的，列宽不变时 PostgreSQL 不报错。
    # ============================================================
    """
    ALTER TABLE sessions ALTER COLUMN id TYPE VARCHAR(64)
    """,
    """
    ALTER TABLE sessions ALTER COLUMN parent_session_id TYPE VARCHAR(64)
    """,
]


class DatabaseManager:
    """PostgreSQL 数据库管理器 — 连接池 + 自动建表

    Args:
        config: 数据库配置
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化连接池并执行 DDL 建表"""
        if not self._config.url:
            logger.warning("DatabaseManager: 数据库 URL 未配置，跳过初始化")
            return

        # 创建连接池
        self._pool = await asyncpg.create_pool(
            self._config.url,
            min_size=2,
            max_size=self._config.pool_size,
            command_timeout=30,
        )
        logger.info(
            "DatabaseManager: 连接池已创建 (pool_size={})",
            self._config.pool_size,
        )

        # 执行 DDL 建表
        await self._run_ddl()

    async def shutdown(self) -> None:
        """关闭连接池"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("DatabaseManager: 连接池已关闭")

    # ------------------------------------------------------------------
    # DDL 执行
    # ------------------------------------------------------------------

    async def _run_ddl(self) -> None:
        """执行 DDL 建表语句（幂等操作）"""
        if not self._pool:
            return

        async with self._pool.acquire() as conn:
            for ddl in DDL_STATEMENTS:
                try:
                    await conn.execute(ddl)
                except Exception as e:
                    # 索引已存在等错误可忽略
                    logger.warning("DDL 执行警告: {}", e)

        logger.info("DatabaseManager: DDL 建表完成")

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    async def execute(self, sql: str, *args) -> str:
        """执行 SQL 语句（INSERT/UPDATE/DELETE）

        Args:
            sql: SQL 语句
            *args: 参数

        Returns:
            状态字符串，如 "INSERT 0 1"
        """
        if not self._pool:
            raise RuntimeError("数据库未初始化")

        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args) -> list[asyncpg.Record]:
        """查询多行数据

        Args:
            sql: SQL 语句
            *args: 参数

        Returns:
            记录列表
        """
        if not self._pool:
            raise RuntimeError("数据库未初始化")

        async with self._pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args) -> asyncpg.Record | None:
        """查询单行数据

        Args:
            sql: SQL 语句
            *args: 参数

        Returns:
            单条记录或 None
        """
        if not self._pool:
            raise RuntimeError("数据库未初始化")

        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args):
        """查询单个值

        Args:
            sql: SQL 语句
            *args: 参数

        Returns:
            单个值
        """
        if not self._pool:
            raise RuntimeError("数据库未初始化")

        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    async def insert_conversation(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        """插入对话记录

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            role: 角色 (user/assistant/system/tool)
            content: 消息内容
            metadata: 元数据（工具调用、token 用量等）

        Returns:
            插入记录的 ID
        """
        import json

        sql = """
            INSERT INTO conversations (user_id, session_id, role, content, metadata)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        """
        return await self.fetchval(
            sql,
            user_id,
            session_id,
            role,
            content,
            json.dumps(metadata) if metadata is not None else None,
        )

    async def get_conversation_history(
        self,
        user_id: str,
        session_id: str,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        """获取对话历史（游标分页，正序）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            before_id: 游标，获取此 ID 之前的消息（用于加载更多）
            limit: 返回记录数上限

        Returns:
            {
                "messages": [...],
                "has_more": bool,
                "oldest_id": int | None
            }
        """
        if before_id:
            sql = """
                SELECT id, role, content, metadata, created_at
                FROM conversations
                WHERE user_id = $1 AND session_id = $2 AND status = 'active' AND id < $3
                ORDER BY id DESC
                LIMIT $4
            """
            rows = await self.fetch(sql, user_id, session_id, before_id, limit)
        else:
            sql = """
                SELECT id, role, content, metadata, created_at
                FROM conversations
                WHERE user_id = $1 AND session_id = $2 AND status = 'active'
                ORDER BY id DESC
                LIMIT $3
            """
            rows = await self.fetch(sql, user_id, session_id, limit)

        import json as _json

        # 转为正序
        messages = list(reversed(rows))

        # 判断是否还有更多
        has_more = len(rows) == limit
        oldest_id = messages[0]["id"] if messages else None

        return {
            "messages": [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "metadata": _json.loads(row["metadata"]) if row["metadata"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in messages
            ],
            "has_more": has_more,
            "oldest_id": oldest_id,
        }

    async def get_user_sessions(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """获取用户的会话列表（从 conversations 聚合 + sessions 获取自定义标题）

        Args:
            user_id: 用户 ID
            limit: 返回会话数上限

        Returns:
            会话列表（按最后活跃时间倒序）
        """
        # 从 conversations 聚合会话信息，同时 left join sessions 获取自定义标题
        sql = """
            SELECT
                c.session_id,
                MIN(c.created_at) AS created_at,
                MAX(c.created_at) AS last_active,
                COUNT(*) AS message_count,
                s.config->>'title' AS custom_title,
                (
                    SELECT LEFT(x.content, 30)
                    FROM conversations x
                    WHERE x.user_id = $1
                      AND x.session_id = c.session_id
                      AND x.role = 'user'
                      AND x.status = 'active'
                    ORDER BY x.id ASC
                    LIMIT 1
                ) AS first_message
            FROM conversations c
            LEFT JOIN sessions s ON s.id = c.session_id AND s.user_id = c.user_id AND s.status = 'active'
            WHERE c.user_id = $1 AND c.status = 'active'
            GROUP BY c.session_id, s.config
            ORDER BY last_active DESC
            LIMIT $2
        """
        rows = await self.fetch(sql, user_id, limit)

        # 转换为字典列表，优先使用自定义标题
        sessions = []
        for row in rows:
            title = row["custom_title"] or row["first_message"] or "新会话"
            sessions.append({
                "session_id": row["session_id"],
                "title": title,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "last_active": row["last_active"].isoformat() if row["last_active"] else None,
                "message_count": row["message_count"],
            })

        return sessions

    async def soft_delete_session(
        self,
        user_id: str,
        session_id: str,
    ) -> int:
        """软删除会话（将所有消息标记为 deleted）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            受影响的行数
        """
        sql = """
            UPDATE conversations
            SET status = 'deleted'
            WHERE user_id = $1 AND session_id = $2 AND status = 'active'
        """
        result = await self.execute(sql, user_id, session_id)
        # 解析 "UPDATE N" 获取受影响行数
        return int(result.split()[-1]) if result else 0

    async def soft_delete_conversation(
        self,
        conversation_id: int,
    ) -> bool:
        """软删除单条对话记录

        Args:
            conversation_id: 对话记录 ID

        Returns:
            是否成功
        """
        sql = """
            UPDATE conversations
            SET status = 'deleted'
            WHERE id = $1 AND status = 'active'
        """
        result = await self.execute(sql, conversation_id)
        return "UPDATE 1" in result

    async def upsert_session_title(
        self,
        user_id: str,
        session_id: str,
        title: str,
    ) -> None:
        """更新或插入会话标题（存储在 sessions 表 config 字段）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            title: 会话标题
        """
        import json

        sql = """
            INSERT INTO sessions (id, user_id, agent_id, config, status)
            VALUES ($1, $2, 'default', $3, 'active')
            ON CONFLICT (id) DO UPDATE SET
                config = COALESCE(sessions.config, '{}'::jsonb) || EXCLUDED.config,
                updated_at = NOW()
        """
        await self.execute(sql, session_id, user_id, json.dumps({"title": title}))

    async def get_session_title(
        self,
        user_id: str,
        session_id: str,
    ) -> str | None:
        """获取会话标题

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            会话标题或 None
        """
        import json

        sql = """
            SELECT config->>'title' AS title
            FROM sessions
            WHERE id = $1 AND user_id = $2 AND status = 'active'
        """
        row = await self.fetchrow(sql, session_id, user_id)
        return row["title"] if row else None

    @property
    def is_initialized(self) -> bool:
        """连接池是否已初始化"""
        return self._pool is not None
