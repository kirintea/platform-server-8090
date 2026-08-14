# -*- coding: utf-8 -*-

"""会话管理器 — 用户区分 + 会话隔离 + Redis 持久化

设计：
- 每个 (user_id, session_id) 维护一个独立的 Agent 实例
- Agent 实例持有独立的 AgentState（对话上下文、摘要等）
- AgentState 通过 Redis 持久化，服务器重启后可恢复会话
- 会话有 TTL，超时后自动清理（内存 + Redis）

使用方式：
    manager = SessionManager(config)
    await manager.initialize()  # 初始化 Redis 连接
    agent = await manager.get_or_create("user_001", "session_abc")
    reply = await agent.reply(UserMsg("user", "你好"))
    await manager.save("user_001", "session_abc")  # 持久化状态
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from agentscope.agent import Agent
from agentscope.state import AgentState

from agentscope.message import AssistantMsg, UserMsg

from core.agent import AgentFactory
from core.config.schemas import AppConfig

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    """单个会话条目"""
    user_id: str
    session_id: str
    agent: Agent
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        """更新最后活跃时间"""
        self.last_active = time.time()


class SessionManager:
    """会话管理器 — 管理多用户多会话的 Agent 实例，支持 Redis 持久化

    Args:
        config: 应用配置
        session_ttl: 会话超时时间（秒），默认 30 分钟
        max_sessions: 最大同时活跃会话数，默认 100
        storage: 可选的 PostgresStorage，用于 fork_session 落库（默认 None）
    """

    def __init__(
        self,
        config: AppConfig,
        session_ttl: int = 1800,
        max_sessions: int = 100,
        storage=None,
        db=None,
    ) -> None:
        self._config = config
        self._session_ttl = session_ttl
        self._max_sessions = max_sessions
        self._db = db  # DatabaseManager，用于 PG 回填
        # key: (user_id, session_id) → SessionEntry
        self._sessions: dict[tuple[str, str], SessionEntry] = {}
        self._lock = asyncio.Lock()

        # Redis 配置
        redis_cfg = config.redis
        self._redis_url = redis_cfg.url
        self._redis_prefix = redis_cfg.key_prefix
        self._redis: aioredis.Redis | None = None

        # 存储层（可选，用于 fork_session 落库）
        self._storage = storage

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化 Redis 连接并测试连通性。"""
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("SessionManager: Redis 连接成功 (%s)", self._redis_url)

    async def shutdown(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            logger.info("SessionManager: Redis 连接已关闭")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def get_or_create(
        self,
        user_id: str,
        session_id: str,
    ) -> Agent:
        """获取或创建会话对应的 Agent 实例

        优先从内存缓存获取；若内存未命中，尝试从 Redis 恢复 AgentState；
        若 Redis 也未命中，创建全新 Agent。

        Args:
            user_id: 用户标识
            session_id: 会话标识

        Returns:
            该会话的独立 Agent 实例
        """
        key = (user_id, session_id)

        async with self._lock:
            entry = self._sessions.get(key)
            if entry is not None:
                entry.touch()
                return entry.agent

            # 超过上限时清理过期会话
            if len(self._sessions) >= self._max_sessions:
                await self._evict_expired()

            # 仍然超限则拒绝
            if len(self._sessions) >= self._max_sessions:
                raise RuntimeError(
                    f"会话数已达上限 ({self._max_sessions})，请稍后重试"
                )

            # 尝试从 Redis 恢复 AgentState
            saved_state = await self._load_state(user_id, session_id)

            # Redis 未命中时，尝试从 PG 回填历史消息
            if saved_state is None:
                saved_state = await self._backfill_from_pg(user_id, session_id)

            # 创建 Agent 实例（传入恢复的状态）
            agent = AgentFactory.create(self._config, state=saved_state)
            entry = SessionEntry(
                user_id=user_id,
                session_id=session_id,
                agent=agent,
            )
            self._sessions[key] = entry
            logger.info(
                "新建会话: user=%s session=%s (恢复=%s, 当前 %d 个会话)",
                user_id, session_id,
                "Redis" if saved_state else "无",
                len(self._sessions),
            )
            return agent

    async def refresh_state(self, user_id: str, session_id: str) -> None:
        """强制从 Redis 重新加载 AgentState 覆盖内存中的副本

        用于多实例场景：其他实例可能已写入更新的状态到 Redis，
        本方法拉取最新 state 替换当前内存中 agent 的 state。

        如果当前内存中没有该会话，则静默返回（下次 get_or_create 会
        自动从 Redis 恢复）；如果 Redis 中也无 state，则不修改内存。
        """
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return  # 内存中没有，下次 get_or_create 会自动从 Redis 恢复
            # 强制从 Redis 加载最新状态
            new_state = await self._load_state(user_id, session_id)
            if new_state is not None:
                entry.agent.state = new_state
                # 同步 PermissionEngine 的 context 引用：Agent.__init__ 中
                # self._engine = PermissionEngine(self.state.permission_context)
                # 持有旧 permission_context 引用，替换 state 后必须同步更新，
                # 否则权限检查会用到过期的 context
                engine = getattr(entry.agent, "_engine", None)
                if engine is not None:
                    engine.context = new_state.permission_context
                logger.debug(
                    "refresh_state 成功: user=%s session=%s",
                    user_id, session_id,
                )
            entry.touch()

    async def fork_session(
        self,
        user_id: str,
        parent_session_id: str,
        new_title: str | None = None,
    ) -> str:
        """基于父会话创建分支，返回新会话 ID

        流程：
        1. 从 Redis 拉取父会话 AgentState JSON，**值拷贝**到子会话 key
           （完全独立，互不影响）
        2. 复制父会话 meta，追加 parent_session_id / forked_at 标记
        3. 若 storage (PostgresStorage) 可用，则落库插入 FORK 记录

        Args:
            user_id: 用户标识
            parent_session_id: 父会话 ID
            new_title: 子会话标题，None 则沿用父标题并追加 " (分支)"

        Returns:
            新生成的子会话 ID

        Raises:
            ValueError: 父会话在 Redis 中无 state（无法 fork）
            RuntimeError: Redis 未初始化
        """
        if self._redis is None:
            raise RuntimeError(
                "Redis 未初始化，无法 fork 会话；请先调用 initialize()"
            )

        import asyncio
        import uuid

        child_session_id = str(uuid.uuid4())

        # 1. 拷贝 AgentState JSON（直接值拷贝，无需反序列化）
        state_key_p = self._redis_key(user_id, parent_session_id)
        state_key_c = self._redis_key(user_id, child_session_id)
        state_json = await self._redis.get(state_key_p)
        if state_json is None:
            raise ValueError(
                f"父会话没有可 fork 的状态: user={user_id} "
                f"session={parent_session_id}"
            )
        await self._redis.set(
            state_key_c, state_json, ex=self._session_ttl
        )

        # 2. 拷贝 meta，追加 parent / fork 标记
        meta_p = await self._load_session_meta(user_id, parent_session_id)
        if meta_p is None:
            meta_p = {}
        if new_title:
            title = new_title
        else:
            base_title = meta_p.get("title", "") or "新会话"
            title = f"{base_title} (分支)"
        now = time.time()
        meta_c = {
            "session_id": child_session_id,
            "user_id": user_id,
            "title": title,
            "parent_session_id": parent_session_id,  # 标记血缘
            "forked_at": now,
            "created_at": meta_p.get("created_at", now),
            "last_active": now,
            "message_count": meta_p.get("message_count", 0),
        }
        await self._redis.set(
            self._redis_meta_key(user_id, child_session_id),
            json.dumps(meta_c, ensure_ascii=False),
            ex=self._session_ttl,
        )

        # 3. 若有 PostgresStorage，则落库（best-effort，失败仅记录日志；
        #    但 CancelledError 必须 re-raise，避免干扰协程取消语义）
        if self._storage is not None:
            try:
                await self._storage.fork_session(
                    parent_session_id, user_id,
                    new_name=title,
                    new_session_id=child_session_id,
                )
            except asyncio.CancelledError:
                raise  # 不吞 CancelledError，保留协程取消语义
            except ValueError:
                # 父会话不在 PG 中（8090 流程只写 Redis 不写 PG sessions 表），
                # 先 upsert 父会话再重试 fork，确保 depth/parent 血缘落库
                try:
                    from core.storage_models import (
                        SessionConfig,
                        SessionSource,
                    )
                    parent_cfg = SessionConfig(
                        name=meta_p.get("title", "新会话"),
                    )
                    await self._storage.upsert_session(
                        user_id=user_id,
                        agent_id=self._config.agent.name,
                        config=parent_cfg,
                        state_json=state_json,
                        session_id=parent_session_id,
                        source=SessionSource.USER,
                        depth=0,
                    )
                    await self._storage.fork_session(
                        parent_session_id, user_id,
                        new_name=title,
                        new_session_id=child_session_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "fork_session 落库失败（含父会话补写）: user=%s parent=%s",
                        user_id, parent_session_id,
                    )
            except Exception:
                logger.exception(
                    "fork_session 落库失败: user=%s parent=%s",
                    user_id, parent_session_id,
                )

        logger.info(
            "fork 会话成功: user=%s parent=%s child=%s",
            user_id, parent_session_id, child_session_id,
        )
        return child_session_id

    async def save(self, user_id: str, session_id: str) -> None:
        """将指定会话的 AgentState 保存到 Redis。

        应在每次 reply/reply_stream 完成后调用。
        同时保存会话元数据（标题、消息数等）。
        """
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.get(key)

        if entry:
            await self._save_state(user_id, session_id, entry.agent)
            # 保存元数据（从 AgentState.context 提取）
            context = getattr(entry.agent.state, "context", [])
            msg_count = len(context)
            title = ""
            for msg in context:
                if getattr(msg, "role", "") == "user":
                    content = getattr(msg, "content", [])
                    if isinstance(content, list):
                        for block in content:
                            if getattr(block, "type", "") == "text":
                                title = block.text[:30]
                                break
                    elif isinstance(content, str):
                        title = content[:30]
                    break
            await self.save_session_meta(user_id, session_id, title, msg_count)

    async def remove(self, user_id: str, session_id: str) -> bool:
        """移除指定会话（同时清除 Redis 中的状态）"""
        key = (user_id, session_id)
        async with self._lock:
            entry = self._sessions.pop(key, None)

        if entry:
            # 先保存最终状态到 Redis（可选：也可直接删除）
            await self._save_state(user_id, session_id, entry.agent)
            logger.info("移除会话: user=%s session=%s", user_id, session_id)
            return True
        return False

    async def list_sessions(self, user_id: str | None = None) -> list[dict]:
        """列出活跃会话（仅内存中）

        Args:
            user_id: 可选，按用户过滤

        Returns:
            会话信息列表
        """
        async with self._lock:
            result = []
            for (uid, sid), entry in self._sessions.items():
                if user_id and uid != user_id:
                    continue
                result.append({
                    "user_id": uid,
                    "session_id": sid,
                    "created_at": entry.created_at,
                    "last_active": entry.last_active,
                })
            return result

    async def list_user_sessions(self, user_id: str) -> list[dict]:
        """列出指定用户的所有会话（从 Redis 扫描）

        通过 SCAN 遍历 Redis 中该用户的所有 :meta key，
        返回会话元数据列表，按 last_active 倒序排列。

        Args:
            user_id: 用户标识

        Returns:
            会话元数据列表
        """
        if not self._redis:
            return []

        meta_prefix = f"{self._redis_prefix}{user_id}:*:meta"
        sessions = []

        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor=cursor, match=meta_prefix, count=50
                )
                for key in keys:
                    try:
                        meta_json = await self._redis.get(key)
                        if meta_json:
                            meta = json.loads(meta_json)
                            sessions.append(meta)
                    except Exception:
                        logger.warning("解析会话元数据失败: %s", key)
                if cursor == 0:
                    break

            # 按 last_active 倒序
            sessions.sort(key=lambda s: s.get("last_active", 0), reverse=True)
        except Exception:
            logger.exception("扫描用户会话失败: user=%s", user_id)

        return sessions

    async def save_session_meta(
        self,
        user_id: str,
        session_id: str,
        title: str = "",
        message_count: int = 0,
    ) -> None:
        """保存会话元数据到 Redis

        元数据与 AgentState 使用相同的 TTL，独立 key 存储。
        """
        if not self._redis:
            return

        key = self._redis_meta_key(user_id, session_id)
        now = time.time()

        # 尝试更新已有元数据
        existing = await self._load_session_meta(user_id, session_id)
        if existing:
            meta = {
                **existing,
                "last_active": now,
                "message_count": message_count,
            }
            # 只在 title 为空时更新
            if title and not existing.get("title"):
                meta["title"] = title
        else:
            meta = {
                "session_id": session_id,
                "user_id": user_id,
                "title": title or "新会话",
                "created_at": now,
                "last_active": now,
                "message_count": message_count,
            }

        try:
            await self._redis.set(
                key, json.dumps(meta, ensure_ascii=False), ex=self._session_ttl
            )
        except Exception:
            logger.exception("保存会话元数据失败: user=%s session=%s", user_id, session_id)

    async def _load_session_meta(self, user_id: str, session_id: str) -> dict | None:
        """从 Redis 加载会话元数据"""
        if not self._redis:
            return None
        try:
            key = self._redis_meta_key(user_id, session_id)
            meta_json = await self._redis.get(key)
            if meta_json:
                return json.loads(meta_json)
        except Exception:
            logger.exception("加载会话元数据失败: user=%s session=%s", user_id, session_id)
        return None

    async def get_session_messages(
        self, user_id: str, session_id: str
    ) -> list[dict] | None:
        """获取会话的消息历史

        从 Redis 加载 AgentState，提取消息列表返回。
        如果会话不存在返回 None。

        Returns:
            消息列表 [{"role": "user"/"assistant", "content": "..."}, ...]
            或 None（会话不存在）
        """
        state = await self._load_state(user_id, session_id)
        if state is None:
            return None

        messages = []
        try:
            # AgentState.context 是 list[Msg]，每条有 role/name/content(list[ContentBlock])
            for msg in state.context:
                role = getattr(msg, "role", "unknown")

                # 确定前端显示角色
                if role == "user":
                    display_role = "user"
                elif role == "assistant":
                    display_role = "agent"
                else:
                    continue  # 跳过 system 等其他角色

                # 提取文本内容 — content 是 list[ContentBlock]
                content = getattr(msg, "content", [])
                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        block_type = getattr(block, "type", "")
                        if block_type == "text":
                            text_parts.append(block.text)
                        elif block_type == "thinking":
                            pass  # 跳过思考过程
                        elif block_type == "tool_call":
                            pass  # 跳过工具调用
                        elif block_type == "tool_result":
                            pass  # 跳过工具结果
                elif isinstance(content, str):
                    text_parts.append(content)

                text = "\n".join(text_parts).strip()
                if text:
                    messages.append({
                        "role": display_role,
                        "content": text,
                    })
        except Exception:
            logger.exception("提取消息历史失败: user=%s session=%s", user_id, session_id)

        return messages

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """彻底删除会话（内存 + Redis AgentState + Redis 元数据）

        Returns:
            是否成功删除
        """
        key = (user_id, session_id)
        async with self._lock:
            self._sessions.pop(key, None)

        deleted = False
        if self._redis:
            try:
                state_key = self._redis_key(user_id, session_id)
                meta_key = self._redis_meta_key(user_id, session_id)
                result = await self._redis.delete(state_key, meta_key)
                deleted = result > 0
            except Exception:
                logger.exception("删除会话 Redis 数据失败: user=%s session=%s", user_id, session_id)

        logger.info("删除会话: user=%s session=%s deleted=%s", user_id, session_id, deleted)
        return deleted

    async def cleanup(self) -> int:
        """手动触发过期会话清理

        Returns:
            清理的会话数量
        """
        async with self._lock:
            return await self._evict_expired()

    @property
    def active_count(self) -> int:
        """当前活跃会话数"""
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Redis 持久化
    # ------------------------------------------------------------------

    def _redis_key(self, user_id: str, session_id: str) -> str:
        """生成 Redis key: {prefix}{user_id}:{session_id}"""
        return f"{self._redis_prefix}{user_id}:{session_id}"

    def _redis_meta_key(self, user_id: str, session_id: str) -> str:
        """生成 Redis 元数据 key: {prefix}{user_id}:{session_id}:meta"""
        return f"{self._redis_prefix}{user_id}:{session_id}:meta"

    async def _save_state(
        self,
        user_id: str,
        session_id: str,
        agent: Agent,
    ) -> None:
        """序列化 AgentState 并写入 Redis。"""
        if not self._redis:
            return
        try:
            key = self._redis_key(user_id, session_id)
            state_json = agent.state.model_dump_json()
            await self._redis.set(key, state_json, ex=self._session_ttl)
            logger.debug("状态已保存: %s (%d bytes)", key, len(state_json))
        except Exception:
            logger.exception("保存状态失败: user=%s session=%s", user_id, session_id)

    async def _load_state(
        self,
        user_id: str,
        session_id: str,
    ) -> AgentState | None:
        """从 Redis 反序列化 AgentState，未命中返回 None。"""
        if not self._redis:
            return None
        try:
            key = self._redis_key(user_id, session_id)
            state_json = await self._redis.get(key)
            if state_json:
                state = AgentState.model_validate_json(state_json)
                logger.debug("从 Redis 恢复状态: %s", key)
                return state
        except Exception:
            logger.exception("加载状态失败: user=%s session=%s", user_id, session_id)
        return None

    async def _backfill_from_pg(
        self,
        user_id: str,
        session_id: str,
        limit: int = 50,
    ) -> AgentState | None:
        """从 PG 加载历史消息，构造 AgentState 用于恢复上下文

        当 Redis 未命中时调用，将 PG 中最近 N 条消息转为 Msg 列表
        注入 AgentState.context，使 Agent 能继续之前的对话。

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            limit: 加载消息条数上限

        Returns:
            AgentState 或 None（PG 无数据时）
        """
        if not self._db or not self._db.is_initialized:
            return None
        try:
            result = await self._db.get_conversation_history(
                user_id, session_id, limit=limit,
            )
            messages = result.get("messages", [])
            if not messages:
                return None

            # 转换为 AgentScope Msg 对象
            context = []
            for msg in messages:
                role = msg["role"]
                content = msg["content"]
                if role == "user":
                    context.append(UserMsg(name="user", content=content))
                elif role == "assistant":
                    context.append(AssistantMsg(name="assistant", content=content))

            if not context:
                return None

            state = AgentState(session_id=session_id)
            state.context = context
            logger.info(
                "PG 回填成功: user=%s session=%s, 加载 %d 条消息",
                user_id, session_id, len(context),
            )
            return state
        except Exception:
            logger.exception("PG 回填失败: user=%s session=%s", user_id, session_id)
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _evict_expired(self) -> int:
        """清理过期会话（需在持有 _lock 时调用）"""
        now = time.time()
        expired = [
            key for key, entry in self._sessions.items()
            if now - entry.last_active > self._session_ttl
        ]
        for key in expired:
            uid, sid = key
            # 过期前保存状态到 Redis（TTL 会续期）
            entry = self._sessions.pop(key)
            await self._save_state(uid, sid, entry.agent)
            logger.info("清理过期会话: user=%s session=%s", uid, sid)
        return len(expired)
