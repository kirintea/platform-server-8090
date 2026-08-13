# -*- coding: utf-8 -*-

"""Session Fork 血缘测试 — parent_session_id / depth / fork_session

测试使用真实 PostgreSQL（ragdb），每个用例后按 user_id 清理 sessions 记录，
避免污染其他测试。
"""

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.storage import PostgresStorage
from core.storage_models import SessionConfig, SessionSource

pytestmark = pytest.mark.asyncio(loop_scope="session")

DB_URL = "postgresql://user:password@localhost:5432/ragdb"


async def _storage():
    """构造独立的 DatabaseManager + PostgresStorage，返回 (storage, db) 供调用方清理"""
    cfg = DatabaseConfig(url=DB_URL, pool_size=2)
    db = DatabaseManager(cfg)
    await db.initialize()
    return PostgresStorage(db), db


async def test_fork_session_carries_parent_and_depth():
    """fork_session 后子会话 parent_session_id / depth 正确，state_json 深拷贝"""
    storage, db = await _storage()
    try:
        uid = "test_fork_user_01"
        cfg = SessionConfig(name="root")
        root = await storage.upsert_session(uid, "ag_01", cfg)
        # 根会话 depth=0, parent=None
        assert root.depth == 0
        assert root.parent_session_id is None

        # fork 一级
        child1 = await storage.fork_session(root.id, uid, "fork-1")
        assert child1.parent_session_id == root.id
        assert child1.depth == 1

        # fork 二级
        child2 = await storage.fork_session(child1.id, uid, "fork-2")
        assert child2.parent_session_id == child1.id
        assert child2.depth == 2

        # 验证 state_json 被深拷贝（此处为空串相等）
        assert child1.state_json == root.state_json
        assert child2.state_json == child1.state_json
    finally:
        # 清理测试数据
        await db.execute("DELETE FROM sessions WHERE user_id = $1", uid)
        await db.shutdown()


async def test_fork_preserves_config_and_agent():
    """fork 保留 agent_id / state_json，新名字覆盖 config.name，source=FORK"""
    storage, db = await _storage()
    try:
        uid = "test_fork_user_02"
        cfg = SessionConfig(name="original", cwd="/tmp")
        root = await storage.upsert_session(
            uid, "ag_99", cfg, state_json='{"k":"v"}'
        )

        child = await storage.fork_session(root.id, uid, "forked")
        assert child.agent_id == "ag_99"
        assert child.config.name == "forked"  # new_name 覆盖
        assert child.config.cwd == "/tmp"  # 原 config 其余字段保留
        assert child.state_json == '{"k":"v"}'
        assert child.source == SessionSource.FORK
    finally:
        await db.execute("DELETE FROM sessions WHERE user_id = $1", uid)
        await db.shutdown()


async def test_fork_nonexistent_parent_raises():
    """fork 不存在的源会话应抛 ValueError"""
    storage, db = await _storage()
    try:
        with pytest.raises(ValueError, match="源会话不存在"):
            await storage.fork_session("nonexistent_sid", "nobody", "x")
    finally:
        await db.shutdown()


async def test_fork_session_uses_provided_new_session_id():
    """fork_session 传入 new_session_id 时，子会话 ID 必须等于该值。

    修复 C3：原实现 upsert_session(session_id=None) 会生成独立 ID，
    导致 Redis 与 PG 的子会话 ID 不一致。新增 new_session_id 参数
    让调用方（SessionManager.fork_session）传入 Redis 中已写入的 ID。
    """
    storage, db = await _storage()
    try:
        uid = "test_fork_user_03"
        cfg = SessionConfig(name="root")
        root = await storage.upsert_session(uid, "ag_01", cfg)

        # 传入指定的 new_session_id
        specified_id = "my-fixed-child-id-12345"
        child = await storage.fork_session(
            root.id, uid, "fork-fixed", new_session_id=specified_id
        )

        # 子会话 ID 必须等于传入值，而非 upsert_session 内部生成
        assert child.id == specified_id, (
            f"子会话 ID 应为 {specified_id}, 实际 {child.id}"
        )
        assert child.parent_session_id == root.id
        assert child.depth == 1
        assert child.source == SessionSource.FORK
    finally:
        await db.execute("DELETE FROM sessions WHERE user_id = $1", "test_fork_user_03")
        await db.shutdown()


async def test_fork_session_without_new_session_id_still_works():
    """new_session_id=None（默认）时保持原行为：内部生成新 ID。

    确保新增参数不破坏现有调用方（向后兼容）。
    """
    storage, db = await _storage()
    try:
        uid = "test_fork_user_04"
        cfg = SessionConfig(name="root")
        root = await storage.upsert_session(uid, "ag_01", cfg)

        # 不传 new_session_id，应保持原行为
        child = await storage.fork_session(root.id, uid, "fork-auto")
        assert child.id != root.id
        assert len(child.id) > 0
        assert child.parent_session_id == root.id
    finally:
        await db.execute("DELETE FROM sessions WHERE user_id = $1", "test_fork_user_04")
        await db.shutdown()
