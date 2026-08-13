# -*- coding: utf-8 -*-

"""Task 4 装配测试 — RedisMessageBus 接入 + ChatService.run() refresh_state

验证两件事：
1. RedisMessageBus 能正常初始化并工作（main.py 装配的基础）
2. ChatService.run() 在每次执行前调用 session_mgr.refresh_state()
   （多实例无状态的关键：避免用过期内存 state 推理）

refresh_state 调用顺序测试使用 InMemoryMessageBus + 假 SessionManager，
避免拉起真实 Redis/Agent/LLM 依赖；只验证 wiring 行为本身。
"""

import pytest

from agentscope.app.message_bus import MessageBus
from core.chat_service import ChatService
from core.config import ConfigManager
from core.message_bus import InMemoryMessageBus
from core.redis_message_bus import RedisMessageBus

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# 假对象 — 用于 ChatService.run() wiring 测试
# ============================================================


class _NoopAgent:
    """reply_stream 立即结束的假 Agent（不产生任何事件）"""

    def __init__(self) -> None:
        self.reply_stream_called = False
        self.last_msg = None

    async def reply_stream(self, msg):
        self.reply_stream_called = True
        self.last_msg = msg
        # 使函数成为 async generator（不 yield 任何事件）
        if False:  # pragma: no cover
            yield


class _RecordingSessionMgr:
    """记录方法调用顺序的假 SessionManager

    只实现 ChatService.run() 用到的三个方法：
    get_or_create / refresh_state / save
    """

    def __init__(self, agent) -> None:
        self._agent = agent
        self.calls: list[tuple] = []

    async def get_or_create(self, user_id: str, session_id: str):
        self.calls.append(("get_or_create", user_id, session_id))
        return self._agent

    async def refresh_state(self, user_id: str, session_id: str) -> None:
        self.calls.append(("refresh_state", user_id, session_id))

    async def save(self, user_id: str, session_id: str) -> None:
        self.calls.append(("save", user_id, session_id))


# ============================================================
# RedisMessageBus 装配 smoke 测试
# ============================================================


async def test_redis_message_bus_is_message_bus():
    """RedisMessageBus 是 MessageBus 的子类（类型注解放宽的前提）"""
    assert issubclass(RedisMessageBus, MessageBus)


async def test_redis_message_bus_initializes():
    """RedisMessageBus 能正常初始化并完成基本 log_append/log_read"""
    cfg = ConfigManager.get_instance().load()
    bus = RedisMessageBus(cfg.redis.url)
    await bus.initialize()
    key = "test:wiring:log"
    try:
        # 清理可能残留的旧数据
        await bus.log_trim(key)
        eid = await bus.log_append(key, {"v": "hello"}, max_len=10)
        assert eid is not None
        items = await bus.log_read(key, max_count=10)
        assert len(items) == 1
        assert items[0][1]["v"] == "hello"
    finally:
        try:
            await bus.log_trim(key)
        except Exception:
            pass
        await bus.aclose()


# ============================================================
# ChatService.run() refresh_state wiring 测试
# ============================================================


async def test_chatservice_run_calls_refresh_state_before_reply():
    """ChatService.run() 在 agent.reply_stream 之前调用 refresh_state

    多实例场景下，每次 run 必须刷新内存 state（其他实例可能已写入更新），
    避免用过期上下文推理。调用顺序应为：
    get_or_create → refresh_state → (reply_stream) → save
    """
    bus = InMemoryMessageBus()
    agent = _NoopAgent()
    mgr = _RecordingSessionMgr(agent)
    service = ChatService(mgr, bus)

    await service.run("u1", "s1", "hello")

    # refresh_state 必须被调用恰好一次
    refresh_calls = [c for c in mgr.calls if c[0] == "refresh_state"]
    assert len(refresh_calls) == 1
    assert refresh_calls[0] == ("refresh_state", "u1", "s1")

    # 调用顺序：get_or_create → refresh_state → save
    method_order = [c[0] for c in mgr.calls]
    assert method_order == ["get_or_create", "refresh_state", "save"]

    # agent.reply_stream 被实际调用（refresh 后才执行推理）
    assert agent.reply_stream_called is True

    await bus.aclose()


async def test_chatservice_accepts_redis_message_bus_instance():
    """ChatService 能接收 RedisMessageBus 实例（类型注解放宽为 MessageBus）

    Python 运行时不强制类型注解，但此处显式验证 RedisMessageBus 实例
    能传入 ChatService 构造函数而不报错，确保 wiring 路径畅通。
    """
    cfg = ConfigManager.get_instance().load()
    bus = RedisMessageBus(cfg.redis.url)
    await bus.initialize()
    try:
        mgr = _RecordingSessionMgr(_NoopAgent())
        # 关键断言：构造不报错（类型注解应为 MessageBus 基类）
        service = ChatService(mgr, bus)
        assert service is not None
        # 内部引用的就是传入的 RedisMessageBus
        assert service._bus is bus
    finally:
        await bus.aclose()
