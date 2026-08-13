# -*- coding: utf-8 -*-

"""PostgreSQL 存储层 — 平台资源的持久化 CRUD

提供 Agent、Session、MCP、Skill、Message、Schedule 的数据库操作。
底层复用 DatabaseManager 的 asyncpg 连接池。

使用方式：
    storage = PostgresStorage(db_manager)
    agent = await storage.upsert_agent(user_id, agent_record)
    agents = await storage.list_agents(user_id)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from core.database import DatabaseManager
from core.storage_models import (
    AgentData,
    AgentRecord,
    MCPRecord,
    MessageRecord,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    SkillRecord,
    _generate_id,
)

logger = logging.getLogger(__name__)


class PostgresStorage:
    """PostgreSQL 存储层 — 实现平台资源的 CRUD 操作

    Args:
        db: DatabaseManager 实例（需已初始化）
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        user_id: str,
        record: AgentRecord,
    ) -> str:
        """创建或更新 Agent 记录

        Args:
            user_id: 用户 ID
            record: Agent 记录

        Returns:
            Agent ID
        """
        now = datetime.now()
        record.updated_at = now
        if not record.created_at:
            record.created_at = now

        sql = """
            INSERT INTO agents (id, user_id, source, data, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                data = EXCLUDED.data,
                source = EXCLUDED.source,
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """
        return await self._db.fetchval(
            sql,
            record.id,
            user_id,
            record.source,
            record.data.model_dump_json(),
            record.created_at,
            record.updated_at,
        )

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """列出用户的所有 Agent"""
        sql = """
            SELECT id, user_id, source, data, created_at, updated_at
            FROM agents WHERE user_id = $1
            ORDER BY created_at DESC
        """
        rows = await self._db.fetch(sql, user_id)
        return [self._row_to_agent(r) for r in rows]

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """获取单个 Agent 记录"""
        sql = """
            SELECT id, user_id, source, data, created_at, updated_at
            FROM agents WHERE user_id = $1 AND id = $2
        """
        row = await self._db.fetchrow(sql, user_id, agent_id)
        return self._row_to_agent(row) if row else None

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """删除 Agent 记录"""
        sql = "DELETE FROM agents WHERE user_id = $1 AND id = $2"
        result = await self._db.execute(sql, user_id, agent_id)
        return "DELETE 1" in result

    def _row_to_agent(self, row) -> AgentRecord:
        """将数据库行转换为 AgentRecord"""
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        return AgentRecord(
            id=row["id"],
            user_id=row["user_id"],
            source=row["source"],
            data=AgentData(**data),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state_json: str = "",
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        parent_session_id: str | None = None,
        depth: int | None = None,
    ) -> SessionRecord:
        """创建或更新 Session 记录

        Args:
            user_id: 用户 ID
            agent_id: Agent ID
            config: 会话配置
            state_json: AgentState 序列化 JSON
            session_id: 更新已有会话时传入，None 则创建新会话
            source: 会话来源
            parent_session_id: 父会话 ID（Fork 时传入），更新已有会话时为 None 表示保留原值
            depth: Fork 深度（None 则默认 0）

        Returns:
            SessionRecord
        """
        now = datetime.now()
        sid = session_id or _generate_id()
        final_depth = 0 if depth is None else depth

        sql = """
            INSERT INTO sessions (id, user_id, agent_id, source, config, state_json,
                                  parent_session_id, depth, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                config = EXCLUDED.config,
                state_json = EXCLUDED.state_json,
                source = EXCLUDED.source,
                parent_session_id = COALESCE(EXCLUDED.parent_session_id, sessions.parent_session_id),
                depth = COALESCE(NULLIF(EXCLUDED.depth, 0), sessions.depth),
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """
        await self._db.fetchval(
            sql,
            sid,
            user_id,
            agent_id,
            source.value,
            config.model_dump_json(),
            state_json,
            parent_session_id,
            final_depth,
            now,
            now,
        )

        return SessionRecord(
            id=sid,
            user_id=user_id,
            agent_id=agent_id,
            source=source,
            config=config,
            state_json=state_json,
            parent_session_id=parent_session_id,
            depth=final_depth,
            created_at=now,
            updated_at=now,
        )

    async def fork_session(
        self,
        src_session_id: str,
        user_id: str,
        new_name: str | None = None,
        new_session_id: str | None = None,
    ) -> SessionRecord:
        """基于已有会话创建分支（Fork）

        1. 读取源会话的 agent_id / config / state_json / depth
        2. 新会话 parent_session_id = src_session_id, depth = src.depth + 1
        3. source = FORK
        4. new_name 非空时覆盖 config.name，其余 config 字段保留

        Args:
            src_session_id: 源会话 ID
            user_id: 用户 ID（必须与源会话一致）
            new_name: 新会话名称，None 则沿用源会话名称
            new_session_id: 指定新会话 ID（与 Redis 中的 child_session_id 对齐），
                None 则由 upsert_session 内部生成

        Returns:
            新创建的 SessionRecord

        Raises:
            ValueError: 源会话不存在
        """
        row = await self._db.fetchrow(
            """SELECT id, user_id, agent_id, config, state_json,
                      parent_session_id, depth
               FROM sessions WHERE id = $1 AND user_id = $2""",
            src_session_id,
            user_id,
        )
        if row is None:
            raise ValueError(f"源会话不存在: {src_session_id}")

        src_cfg_json = row["config"]
        cfg_data = (
            json.loads(src_cfg_json)
            if isinstance(src_cfg_json, str)
            else src_cfg_json
        )
        cfg = SessionConfig(**cfg_data)
        if new_name:
            cfg.name = new_name

        src_depth = int(row["depth"] or 0)

        return await self.upsert_session(
            user_id=user_id,
            agent_id=row["agent_id"],
            config=cfg,
            state_json=row["state_json"] or "",
            session_id=new_session_id,
            source=SessionSource.FORK,
            parent_session_id=row["id"],
            depth=src_depth + 1,
        )

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str | None = None,
    ) -> list[SessionRecord]:
        """列出用户的会话"""
        if agent_id:
            sql = """
                SELECT * FROM sessions
                WHERE user_id = $1 AND agent_id = $2
                ORDER BY updated_at DESC
            """
            rows = await self._db.fetch(sql, user_id, agent_id)
        else:
            sql = """
                SELECT * FROM sessions
                WHERE user_id = $1
                ORDER BY updated_at DESC
            """
            rows = await self._db.fetch(sql, user_id)
        return [self._row_to_session(r) for r in rows]

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """获取单个 Session"""
        sql = """
            SELECT * FROM sessions
            WHERE user_id = $1 AND id = $2
        """
        row = await self._db.fetchrow(sql, user_id, session_id)
        return self._row_to_session(row) if row else None

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state_json: str,
    ) -> None:
        """更新 Session 的 AgentState（热路径）"""
        sql = """
            UPDATE sessions SET state_json = $1, updated_at = NOW()
            WHERE user_id = $2 AND id = $3
        """
        await self._db.execute(sql, state_json, user_id, session_id)

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """删除 Session"""
        sql = "DELETE FROM sessions WHERE user_id = $1 AND id = $2"
        result = await self._db.execute(sql, user_id, session_id)
        return "DELETE 1" in result

    def _row_to_session(self, row) -> SessionRecord:
        """将数据库行转换为 SessionRecord"""
        config_data = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            source=SessionSource(row["source"]),
            team_id=row.get("team_id"),
            config=SessionConfig(**config_data),
            state_json=row.get("state_json", ""),
            parent_session_id=row.get("parent_session_id"),
            depth=int(row.get("depth") or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # MCP CRUD
    # ------------------------------------------------------------------

    async def upsert_mcp(
        self,
        user_id: str,
        record: MCPRecord,
    ) -> str:
        """创建或更新 MCP 记录"""
        now = datetime.now()
        record.updated_at = now

        # 将 MCP 配置序列化为 JSON
        config = {
            "transport": record.transport,
            "command": record.command,
            "args": record.args,
            "url": record.url,
            "headers": record.headers,
            "display_name": record.display_name,
            "description": record.description,
            "author": record.author,
            "icon_url": record.icon_url,
            "tags": record.tags,
            "hub_id": record.hub_id,
            "card_id": record.card_id,
            "version": record.version,
        }

        sql = """
            INSERT INTO mcps (id, user_id, name, transport, config, enabled, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (user_id, name) DO UPDATE SET
                transport = EXCLUDED.transport,
                config = EXCLUDED.config,
                enabled = EXCLUDED.enabled,
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """
        return await self._db.fetchval(
            sql,
            record.id,
            user_id,
            record.name,
            record.transport,
            json.dumps(config, ensure_ascii=False),
            record.enabled,
            record.created_at or now,
            now,
        )

    async def list_mcps(self, user_id: str) -> list[MCPRecord]:
        """列出用户的所有 MCP"""
        sql = """
            SELECT * FROM mcps WHERE user_id = $1
            ORDER BY created_at DESC
        """
        rows = await self._db.fetch(sql, user_id)
        return [self._row_to_mcp(r) for r in rows]

    async def get_mcp(
        self,
        user_id: str,
        mcp_id: str,
    ) -> MCPRecord | None:
        """获取单个 MCP"""
        sql = "SELECT * FROM mcps WHERE user_id = $1 AND id = $2"
        row = await self._db.fetchrow(sql, user_id, mcp_id)
        return self._row_to_mcp(row) if row else None

    async def get_mcp_by_name(
        self,
        user_id: str,
        name: str,
    ) -> MCPRecord | None:
        """按名称获取 MCP"""
        sql = "SELECT * FROM mcps WHERE user_id = $1 AND name = $2"
        row = await self._db.fetchrow(sql, user_id, name)
        return self._row_to_mcp(row) if row else None

    async def delete_mcp(self, user_id: str, mcp_id: str) -> bool:
        """删除 MCP"""
        sql = "DELETE FROM mcps WHERE user_id = $1 AND id = $2"
        result = await self._db.execute(sql, user_id, mcp_id)
        return "DELETE 1" in result

    def _row_to_mcp(self, row) -> MCPRecord:
        """将数据库行转换为 MCPRecord"""
        config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        return MCPRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            transport=row["transport"],
            command=config.get("command"),
            args=config.get("args", []),
            url=config.get("url"),
            headers=config.get("headers", {}),
            display_name=config.get("display_name"),
            description=config.get("description", ""),
            author=config.get("author"),
            icon_url=config.get("icon_url"),
            tags=config.get("tags", []),
            hub_id=config.get("hub_id"),
            card_id=config.get("card_id"),
            version=config.get("version"),
            enabled=row["enabled"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def upsert_skill(
        self,
        user_id: str,
        record: SkillRecord,
    ) -> str:
        """创建或更新 Skill 记录"""
        now = datetime.now()
        record.updated_at = now

        data = {
            "display_name": record.display_name,
            "description": record.description,
            "markdown": record.markdown,
            "tags": record.tags,
            "author": record.author,
            "icon_url": record.icon_url,
            "hub_id": record.hub_id,
            "card_id": record.card_id,
            "version": record.version,
        }

        sql = """
            INSERT INTO skills (id, user_id, name, data, enabled, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, name) DO UPDATE SET
                data = EXCLUDED.data,
                enabled = EXCLUDED.enabled,
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """
        return await self._db.fetchval(
            sql,
            record.id,
            user_id,
            record.name,
            json.dumps(data, ensure_ascii=False),
            record.enabled,
            record.created_at or now,
            now,
        )

    async def list_skills(self, user_id: str) -> list[SkillRecord]:
        """列出用户的所有 Skill"""
        sql = "SELECT * FROM skills WHERE user_id = $1 ORDER BY created_at DESC"
        rows = await self._db.fetch(sql, user_id)
        return [self._row_to_skill(r) for r in rows]

    async def get_skill(
        self,
        user_id: str,
        skill_id: str,
    ) -> SkillRecord | None:
        """获取单个 Skill"""
        sql = "SELECT * FROM skills WHERE user_id = $1 AND id = $2"
        row = await self._db.fetchrow(sql, user_id, skill_id)
        return self._row_to_skill(row) if row else None

    async def get_skill_by_name(
        self,
        user_id: str,
        name: str,
    ) -> SkillRecord | None:
        """按名称获取 Skill"""
        sql = "SELECT * FROM skills WHERE user_id = $1 AND name = $2"
        row = await self._db.fetchrow(sql, user_id, name)
        return self._row_to_skill(row) if row else None

    async def delete_skill(self, user_id: str, skill_id: str) -> bool:
        """删除 Skill"""
        sql = "DELETE FROM skills WHERE user_id = $1 AND id = $2"
        result = await self._db.execute(sql, user_id, skill_id)
        return "DELETE 1" in result

    def _row_to_skill(self, row) -> SkillRecord:
        """将数据库行转换为 SkillRecord"""
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        return SkillRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            display_name=data.get("display_name"),
            description=data.get("description", ""),
            markdown=data.get("markdown", ""),
            tags=data.get("tags", []),
            author=data.get("author"),
            icon_url=data.get("icon_url"),
            hub_id=data.get("hub_id"),
            card_id=data.get("card_id"),
            version=data.get("version"),
            enabled=row["enabled"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Message CRUD
    # ------------------------------------------------------------------

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        """持久化一条消息

        如果 session 中最后一条消息的 msg_id 相同则更新，否则追加。
        """
        # 检查最后一条消息
        last_sql = """
            SELECT id, msg_id FROM messages
            WHERE user_id = $1 AND session_id = $2
            ORDER BY id DESC LIMIT 1
        """
        last = await self._db.fetchrow(last_sql, user_id, session_id)

        if last and last["msg_id"] == msg_id:
            # 更新已有消息
            update_sql = """
                UPDATE messages SET content = $1, metadata = $2
                WHERE id = $3
            """
            await self._db.execute(
                update_sql,
                content,
                json.dumps(metadata or {}),
                last["id"],
            )
            return last["id"]

        # 插入新消息
        insert_sql = """
            INSERT INTO messages (user_id, session_id, msg_id, role, content, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """
        return await self._db.fetchval(
            insert_sql,
            user_id,
            session_id,
            msg_id,
            role,
            content,
            json.dumps(metadata or {}),
        )

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> tuple[list[MessageRecord], bool]:
        """获取会话的消息历史（游标分页）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            limit: 最大返回数
            before: 游标 — 返回此 ID 之前的消息

        Returns:
            (消息列表, 是否有更多)
        """
        if before:
            sql = """
                SELECT * FROM messages
                WHERE user_id = $1 AND session_id = $2 AND msg_id < $3
                ORDER BY id DESC
                LIMIT $4
            """
            rows = await self._db.fetch(sql, user_id, session_id, before, limit + 1)
        else:
            sql = """
                SELECT * FROM messages
                WHERE user_id = $1 AND session_id = $2
                ORDER BY id DESC
                LIMIT $3
            """
            rows = await self._db.fetch(sql, user_id, session_id, limit + 1)

        has_more = len(rows) > limit
        rows = rows[:limit]
        records = [self._row_to_message(r) for r in rows]
        records.reverse()  # 恢复时间正序
        return records, has_more

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> MessageRecord | None:
        """获取单条消息"""
        sql = """
            SELECT * FROM messages
            WHERE user_id = $1 AND session_id = $2 AND msg_id = $3
        """
        row = await self._db.fetchrow(sql, user_id, session_id, message_id)
        return self._row_to_message(row) if row else None

    def _row_to_message(self, row) -> MessageRecord:
        """将数据库行转换为 MessageRecord"""
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        return MessageRecord(
            id=str(row["id"]),
            user_id=row["user_id"],
            session_id=row["session_id"],
            msg_id=row["msg_id"],
            role=row["role"],
            content=row["content"],
            metadata=meta or {},
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Schedule CRUD
    # ------------------------------------------------------------------

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """创建或更新定时任务"""
        now = datetime.now()
        record.updated_at = now

        sql = """
            INSERT INTO schedules (id, user_id, agent_id, session_id, name,
                                   cron_expr, prompt, source, enabled,
                                   last_run_at, next_run_at, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                cron_expr = EXCLUDED.cron_expr,
                prompt = EXCLUDED.prompt,
                enabled = EXCLUDED.enabled,
                last_run_at = EXCLUDED.last_run_at,
                next_run_at = EXCLUDED.next_run_at,
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """
        return await self._db.fetchval(
            sql,
            record.id,
            user_id,
            record.agent_id,
            record.session_id,
            record.name,
            record.cron_expr,
            record.prompt,
            record.source.value,
            record.enabled,
            record.last_run_at,
            record.next_run_at,
            record.created_at or now,
            now,
        )

    async def list_schedules(self, user_id: str) -> list[ScheduleRecord]:
        """列出用户的所有定时任务"""
        sql = "SELECT * FROM schedules WHERE user_id = $1 ORDER BY created_at DESC"
        rows = await self._db.fetch(sql, user_id)
        return [self._row_to_schedule(r) for r in rows]

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """获取单个定时任务"""
        sql = "SELECT * FROM schedules WHERE user_id = $1 AND id = $2"
        row = await self._db.fetchrow(sql, user_id, schedule_id)
        return self._row_to_schedule(row) if row else None

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        """删除定时任务"""
        sql = "DELETE FROM schedules WHERE user_id = $1 AND id = $2"
        result = await self._db.execute(sql, user_id, schedule_id)
        return "DELETE 1" in result

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """列出所有定时任务（启动恢复用）"""
        sql = "SELECT * FROM schedules WHERE enabled = TRUE ORDER BY created_at"
        rows = await self._db.fetch(sql)
        return [self._row_to_schedule(r) for r in rows]

    def _row_to_schedule(self, row) -> ScheduleRecord:
        """将数据库行转换为 ScheduleRecord"""
        return ScheduleRecord(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            session_id=row.get("session_id"),
            name=row["name"],
            cron_expr=row["cron_expr"],
            prompt=row["prompt"],
            source=ScheduleSource(row["source"]),
            enabled=row["enabled"],
            last_run_at=row.get("last_run_at"),
            next_run_at=row.get("next_run_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
