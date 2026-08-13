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

import logging

import asyncpg

from core.config.schemas import DatabaseConfig

logger = logging.getLogger(__name__)

# ============================================================
# DDL 建表语句（幂等操作，可重复执行）
# ============================================================

DDL_STATEMENTS = [
    # 用户表
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id     VARCHAR(64) PRIMARY KEY,
        name        VARCHAR(128),
        preferences JSONB DEFAULT '{}',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 对话历史表（原有）
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        session_id  VARCHAR(64) NOT NULL,
        role        VARCHAR(16) NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB DEFAULT '{}',
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 索引：用户+会话查询
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_session
    ON conversations(user_id, session_id)
    """,

    # 索引：时间范围查询
    """
    CREATE INDEX IF NOT EXISTS idx_conv_created
    ON conversations(created_at)
    """,

    # 索引：用户最近对话
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_time
    ON conversations(user_id, created_at DESC)
    """,

    # ============================================================
    # 服务层新表（Phase 1+）
    # ============================================================

    # Agent 配置表
    """
    CREATE TABLE IF NOT EXISTS agents (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        data        JSONB NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agents_user
    ON agents(user_id)
    """,

    # Session 会话表
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        agent_id    VARCHAR(32) NOT NULL,
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        team_id     VARCHAR(32),
        config      JSONB NOT NULL DEFAULT '{}',
        state_json  TEXT NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_user_agent
    ON sessions(user_id, agent_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_team
    ON sessions(team_id) WHERE team_id IS NOT NULL
    """,

    # MCP 已安装表
    """
    CREATE TABLE IF NOT EXISTS mcps (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        transport   VARCHAR(16) NOT NULL DEFAULT 'stdio',
        config      JSONB NOT NULL DEFAULT '{}',
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
        data        JSONB NOT NULL DEFAULT '{}',
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

    # 消息持久化表
    """
    CREATE TABLE IF NOT EXISTS messages (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        session_id  VARCHAR(32) NOT NULL,
        msg_id      VARCHAR(64) NOT NULL,
        role        VARCHAR(16) NOT NULL,
        content     TEXT NOT NULL DEFAULT '',
        metadata    JSONB DEFAULT '{}',
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(user_id, session_id, id)
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
            "DatabaseManager: 连接池已创建 (pool_size=%d)",
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
                    logger.warning("DDL 执行警告: %s", e)

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
            json.dumps(metadata or {}),
        )

    async def get_conversation_history(
        self,
        user_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[asyncpg.Record]:
        """获取对话历史

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            limit: 返回记录数上限

        Returns:
            对话记录列表（按时间正序）
        """
        sql = """
            SELECT id, role, content, metadata, created_at
            FROM conversations
            WHERE user_id = $1 AND session_id = $2
            ORDER BY created_at DESC
            LIMIT $3
        """
        rows = await self.fetch(sql, user_id, session_id, limit)
        return list(reversed(rows))

    async def get_user_info(self, user_id: str) -> asyncpg.Record | None:
        """获取用户信息

        Args:
            user_id: 用户 ID

        Returns:
            用户记录或 None
        """
        sql = "SELECT * FROM users WHERE user_id = $1"
        return await self.fetchrow(sql, user_id)

    async def upsert_user(
        self,
        user_id: str,
        name: str | None = None,
        preferences: dict | None = None,
    ) -> None:
        """创建或更新用户

        Args:
            user_id: 用户 ID
            name: 用户名称
            preferences: 偏好设置
        """
        import json

        sql = """
            INSERT INTO users (user_id, name, preferences, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, users.name),
                preferences = COALESCE(EXCLUDED.preferences, users.preferences),
                updated_at = NOW()
        """
        await self.execute(sql, user_id, name, json.dumps(preferences or {}))

    @property
    def is_initialized(self) -> bool:
        """连接池是否已初始化"""
        return self._pool is not None
