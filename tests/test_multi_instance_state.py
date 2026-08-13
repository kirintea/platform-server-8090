# -*- coding: utf-8 -*-

"""SessionManager 多实例状态一致性 + 会话分支测试

覆盖 Task 3 新增的两个方法：
- refresh_state(): 强制从 Redis 重新加载 AgentState 覆盖内存（多实例一致性）
- fork_session():  基于 Redis state 深拷贝创建会话分支

测试直接操作 Redis 写入 AgentState（绕过 AgentFactory），
避免 LLM/MCP/Skills 等重依赖；refresh_state 测试中通过 get_or_create
将 Agent 载入内存，再验证 refresh 能拉到其他实例写入的新状态。
"""

import json

import pytest
import redis.asyncio as aioredis
from agentscope.state import AgentState

from core.config import ConfigManager
from core.session import SessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def cfg():
    return ConfigManager.get_instance().load()


# ============================================================
# 辅助函数 — 直接操作 Redis（绕过 SessionManager / AgentFactory）
# ============================================================


async def _write_state_to_redis(cfg, user_id, session_id, state: AgentState):
    """直接写 AgentState JSON 到 Redis state key"""
    r = aioredis.from_url(cfg.redis.url, decode_responses=True)
    try:
        key = f"{cfg.redis.key_prefix}{user_id}:{session_id}"
        await r.set(key, state.model_dump_json(), ex=1800)
    finally:
        await r.aclose()


async def _read_state_from_redis(cfg, user_id, session_id) -> AgentState | None:
    """直接从 Redis 读 AgentState"""
    r = aioredis.from_url(cfg.redis.url, decode_responses=True)
    try:
        key = f"{cfg.redis.key_prefix}{user_id}:{session_id}"
        raw = await r.get(key)
    finally:
        await r.aclose()
    if raw:
        return AgentState.model_validate_json(raw)
    return None


async def _purge_user_keys(cfg, user_id):
    """清理某用户在 Redis 中的全部 session key（state + meta）"""
    r = aioredis.from_url(cfg.redis.url, decode_responses=True)
    try:
        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor,
                match=f"{cfg.redis.key_prefix}{user_id}:*",
                count=50,
            )
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break
    finally:
        await r.aclose()


# ============================================================
# refresh_state 测试
# ============================================================


async def test_refresh_state_picks_up_other_instance_write(cfg):
    """模拟实例 A 写 Redis，实例 B 内存中已加载旧状态，
    refresh_state 后 B 的 agent.state 被替换为 A 写入的新状态。
    """
    mgrA = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    mgrB = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgrA.initialize()
    await mgrB.initialize()

    uid, sid = "u_multi_01", "s_multi_01"
    # 清理残留
    await mgrA.delete_session(uid, sid)
    await mgrB.delete_session(uid, sid)
    await _purge_user_keys(cfg, uid)

    try:
        # 1) 实例 B 先 get_or_create：内存缓存 agent，state.summary 为默认空串
        agent_b = await mgrB.get_or_create(uid, sid)
        summary_before = agent_b.state.summary
        assert summary_before == "", f"默认 summary 应为空, 实际: {summary_before!r}"

        # 2) 实例 A 直接写一个新 AgentState 到 Redis（summary=summary_from_A）
        state_a = AgentState()
        state_a.summary = "summary_from_A"
        await _write_state_to_redis(cfg, uid, sid, state_a)

        # 3) 此时 B 内存中的 agent.state 仍然是旧的（未 refresh）
        #    —— 这正是多实例不一致的场景
        assert agent_b.state.summary == "", "refresh 前 B 内存状态不应被 Redis 写入影响"

        # 4) B 调用 refresh_state 强制从 Redis 加载
        await mgrB.refresh_state(uid, sid)

        # 5) 验证 B 的 agent.state 已被替换为 Redis 中的新状态
        #    注意：refresh_state 修改的是同一个 entry.agent.state 引用
        assert agent_b.state.summary == "summary_from_A", (
            "refresh_state 后 B 应看到 A 写入的 summary"
        )

        # 6) 再次 get_or_create 应返回同一个 entry（已 refresh）
        agent_b2 = await mgrB.get_or_create(uid, sid)
        assert agent_b2 is agent_b, "refresh 后 get_or_create 应返回同一 agent 实例"
        assert agent_b2.state.summary == "summary_from_A"
    finally:
        await mgrA.delete_session(uid, sid)
        await mgrB.delete_session(uid, sid)
        await _purge_user_keys(cfg, uid)
        await mgrA.shutdown()
        await mgrB.shutdown()


async def test_refresh_state_noop_when_session_not_in_memory(cfg):
    """内存中没有该会话时，refresh_state 应静默返回（不报错），
    下次 get_or_create 会自动从 Redis 恢复。
    """
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgr.initialize()

    uid, sid = "u_multi_02", "s_multi_02"
    await mgr.delete_session(uid, sid)
    await _purge_user_keys(cfg, uid)

    try:
        # Redis 中写一个 state
        state = AgentState()
        state.summary = "orphan_state"
        await _write_state_to_redis(cfg, uid, sid, state)

        # 内存中无此会话 → refresh_state 应不报错、不影响 Redis
        await mgr.refresh_state(uid, sid)  # 不应抛异常

        # Redis 中 state 仍然存在
        state_still = await _read_state_from_redis(cfg, uid, sid)
        assert state_still is not None
        assert state_still.summary == "orphan_state"
    finally:
        await mgr.delete_session(uid, sid)
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()


# ============================================================
# fork_session 测试
# ============================================================


async def test_fork_session_deep_copies_state(cfg):
    """fork 后子会话 state 是父会话 state 的值拷贝，
    修改子会话 state 不影响父会话。
    """
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgr.initialize()

    uid = "u_fork_01"
    parent_sid = "s_fork_parent_01"
    await mgr.delete_session(uid, parent_sid)
    await _purge_user_keys(cfg, uid)

    try:
        # 1) 直接写父会话 state 到 Redis
        parent_state = AgentState()
        parent_state.summary = "parent_summary_v1"
        await _write_state_to_redis(cfg, uid, parent_sid, parent_state)

        # 2) 写父会话 meta 到 Redis（fork_session 会读取）
        parent_meta = {
            "session_id": parent_sid,
            "user_id": uid,
            "title": "parent_title",
            "created_at": 1000000.0,
            "last_active": 1000001.0,
            "message_count": 5,
        }
        r = aioredis.from_url(cfg.redis.url, decode_responses=True)
        try:
            meta_key = f"{cfg.redis.key_prefix}{uid}:{parent_sid}:meta"
            await r.set(meta_key, json.dumps(parent_meta), ex=1800)
        finally:
            await r.aclose()

        # 3) fork
        child_sid = await mgr.fork_session(uid, parent_sid, "fork-child-1")

        # 4) 验证子会话 state 存在且 summary 等于父
        child_state = await _read_state_from_redis(cfg, uid, child_sid)
        assert child_state is not None, "子会话 state 应已写入 Redis"
        assert child_state.summary == "parent_summary_v1"

        # 5) 修改子会话 state（写回 Redis）
        child_state.summary = "child_modified"
        await _write_state_to_redis(cfg, uid, child_sid, child_state)

        # 6) 父会话 state 不变（值拷贝，完全独立）
        parent_state_after = await _read_state_from_redis(cfg, uid, parent_sid)
        assert parent_state_after.summary == "parent_summary_v1", (
            "修改子会话不应影响父会话 state"
        )

        # 7) 子会话 meta 有 parent_session_id 标记 + forked_at
        child_meta = await mgr._load_session_meta(uid, child_sid)
        assert child_meta is not None, "子会话 meta 应已写入"
        assert child_meta.get("parent_session_id") == parent_sid
        assert "forked_at" in child_meta
        assert child_meta.get("title") == "fork-child-1"
        assert child_meta.get("message_count") == 5
    finally:
        await mgr.delete_session(uid, parent_sid)
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()


async def test_fork_session_missing_parent_state_raises(cfg):
    """父会话在 Redis 中无 state 时，fork_session 应抛 ValueError"""
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgr.initialize()

    uid = "u_fork_02"
    missing_sid = "s_fork_missing_01"
    await _purge_user_keys(cfg, uid)

    try:
        with pytest.raises(ValueError, match="父会话没有可 fork 的状态"):
            await mgr.fork_session(uid, missing_sid, "orphan-fork")
    finally:
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()


async def test_fork_session_generates_new_session_id(cfg):
    """fork_session 返回的 child_session_id 必须不同于 parent_session_id"""
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgr.initialize()

    uid = "u_fork_03"
    parent_sid = "s_fork_parent_03"
    await _purge_user_keys(cfg, uid)

    try:
        parent_state = AgentState()
        parent_state.summary = "p"
        await _write_state_to_redis(cfg, uid, parent_sid, parent_state)

        child_sid = await mgr.fork_session(uid, parent_sid)
        assert child_sid != parent_sid, "子会话 ID 必须不同于父会话"
        assert len(child_sid) > 0
    finally:
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()


# ============================================================
# C1 修复测试 — refresh_state 必须同步 PermissionEngine 的 context 引用
# ============================================================


async def test_refresh_state_syncs_permission_engine_context(cfg):
    """refresh_state 替换 agent.state 后，agent._engine.context 必须指向
    新 state 的 permission_context，否则权限检查会用到过期的 context。

    复现：Agent.__init__ 中 self._engine = PermissionEngine(self.state.
    permission_context) 持有引用。若 refresh_state 只替换 state，
    _engine.context 仍指向旧对象。
    """
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    await mgr.initialize()

    uid, sid = "u_engine_01", "s_engine_01"
    await mgr.delete_session(uid, sid)
    await _purge_user_keys(cfg, uid)

    try:
        # 1) get_or_create 让 Agent 载入内存（state.summary 为空）
        agent = await mgr.get_or_create(uid, sid)
        old_perm_ctx = agent.state.permission_context
        assert agent._engine.context is old_perm_ctx, (
            "初始时 _engine.context 应指向 state.permission_context"
        )

        # 2) 写一个新 AgentState 到 Redis（permission_context 是新对象）
        new_state = AgentState()
        new_state.summary = "refreshed_for_engine_sync"
        await _write_state_to_redis(cfg, uid, sid, new_state)

        # 3) refresh_state 应同步替换 state 和 _engine.context
        await mgr.refresh_state(uid, sid)

        # 4) 验证 _engine.context 已指向新 state 的 permission_context
        #    注意：refresh_state 内部 _load_state 会反序列化出新的
        #    AgentState 对象（含新 permission_context），所以这里验证
        #    engine.context 与当前 agent.state.permission_context 是同一对象，
        #    且不等于旧的 old_perm_ctx
        assert agent.state.summary == "refreshed_for_engine_sync", (
            "agent.state 应已被替换为新 state"
        )
        assert agent._engine.context is agent.state.permission_context, (
            "_engine.context 必须指向当前 state.permission_context，"
            "否则权限检查用过期数据"
        )
        assert agent._engine.context is not old_perm_ctx, (
            "_engine.context 不应仍指向旧 state 的 permission_context"
        )
    finally:
        await mgr.delete_session(uid, sid)
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()


# ============================================================
# C2 修复测试 — fork_session 在 Redis 未初始化时应报 RuntimeError
# ============================================================


async def test_fork_session_raises_when_redis_not_initialized(cfg):
    """Redis 未初始化时 fork_session 不应静默返回 UUID，
    应抛 RuntimeError 避免调用方误以为 fork 成功。
    """
    # 不调用 initialize()，self._redis 为 None
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=50)
    # 不初始化 Redis
    uid = "u_no_redis_01"
    parent_sid = "s_no_redis_parent_01"

    with pytest.raises(RuntimeError, match="Redis 未初始化"):
        await mgr.fork_session(uid, parent_sid)


# ============================================================
# I2 修复测试 — fork_session 落库时不应吞 CancelledError
# ============================================================


class _CancelRaisingStorage:
    """伪 storage，fork_session 抛 CancelledError，验证不被吞"""

    async def fork_session(self, src_session_id, user_id, new_name=None,
                           new_session_id=None):
        import asyncio
        raise asyncio.CancelledError("simulated cancel")


async def test_fork_session_reraises_cancelled_error(cfg):
    """storage.fork_session 抛 CancelledError 时，SessionManager.fork_session
    必须 re-raise，不能被 except Exception 吞掉。
    """
    mgr = SessionManager(
        cfg, session_ttl=1800, max_sessions=50,
        storage=_CancelRaisingStorage(),
    )
    await mgr.initialize()

    uid = "u_cancel_01"
    parent_sid = "s_cancel_parent_01"
    await _purge_user_keys(cfg, uid)

    try:
        # 准备父会话 state（Redis 操作正常）
        parent_state = AgentState()
        parent_state.summary = "parent_for_cancel_test"
        await _write_state_to_redis(cfg, uid, parent_sid, parent_state)

        import asyncio
        with pytest.raises(asyncio.CancelledError):
            await mgr.fork_session(uid, parent_sid, "cancel-test")
    finally:
        await _purge_user_keys(cfg, uid)
        await mgr.shutdown()

