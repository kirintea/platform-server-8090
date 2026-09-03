# -*- coding: utf-8 -*-

"""Chat 服务层 — Fire-and-Forget 模式

将 chat 从同步请求-响应升级为事件驱动：
1. POST /chat/ 触发后台 task，立即返回
2. 后台 task 执行 agent.reply_stream，将事件发布到 message_bus
3. 前端通过 GET /sessions/{id}/stream 订阅 SSE 事件流

使用方式：
    service = ChatService(config, session_mgr, message_bus)
    await service.run(user_id, session_id, message)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentscope.app.message_bus import MessageBus, MessageBusKeys
from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

from core.session import SessionManager
from core.session_status import SessionBusyError, SessionState, SessionStatusTracker

from loguru import logger


# ------------------------------------------------------------------
# 共享会话级运行锁（跨传输 / 跨实例互斥）
# ------------------------------------------------------------------
# 与 /chat/stream、/ws/chat 共用同一把 per-(user, session) 锁，确保同一
# 会话的并发 turn 被串行化，避免三个传输各自为政导致的状态竞态。
SESSION_RUN_TTL_SECS = getattr(MessageBusKeys, "SESSION_RUN_TTL_SECS", 600)


def session_lock_key(user_id: str, session_id: str) -> str:
    """生成统一的 per-(user, session) 运行锁 key。

    三个传输（/chat/stream、/ws/chat、/chat/）都通过此函数取同一把锁，
    避免“同一会话、不同传输”并发执行导致 AgentState 互相覆盖。
    """
    return f"session_lock:{user_id}:{session_id}"


def acquire_session_lock(bus, user_id: str, session_id: str):
    """返回一个 async context manager，获取该会话的运行锁。

    bus 既可为 InMemoryMessageBus（进程内 asyncio.Lock）也可为
    RedisMessageBus（分布式锁），让锁在多实例部署下同样生效。
    """
    return bus.acquire_lock(
        session_lock_key(user_id, session_id),
        ttl_secs=SESSION_RUN_TTL_SECS,
    )


class ChatService:
    """Chat 服务 — 管理 fire-and-forget 模式的对话触发

    Args:
        session_mgr: 会话管理器
        message_bus: 消息总线（MessageBus 抽象基类，支持 InMemory / Redis 实现）
    """

    def __init__(
        self,
        session_mgr: SessionManager,
        message_bus: MessageBus,
        status_tracker: SessionStatusTracker | None = None,
    ) -> None:
        self._session_mgr = session_mgr
        self._bus = message_bus
        self._status_tracker = status_tracker

    async def run(
        self,
        user_id: str,
        session_id: str,
        message: str,
        db=None,
        device_id: str = "unknown",
    ) -> None:
        """触发一次 chat run（后台执行）

        获取或创建会话 Agent，执行 reply_stream，
        将每个事件发布到 message_bus 的 session events channel。

        与 /chat/stream、/ws/chat 共用同一把 per-(user, session) 运行锁，
        确保同一会话的并发 turn 被串行化（跨传输、跨实例）。

        多端并发：通过 SessionStatusTracker 广播会话状态，
        设备B发消息时可立即得知"有人在说话"，不再 spin-wait。

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            message: 用户消息
            db: 可选 DatabaseManager，用于 PG 双写；不传则仅写 Redis
            device_id: 发起请求的设备标识（用于多端状态广播）
        """
        events_key = MessageBusKeys.session_events(session_id)

        # 检查会话是否已被其他设备占用（多端并发）
        if self._status_tracker:
            status = await self._status_tracker.get_status(session_id)
            if status.get("state") == SessionState.GENERATING:
                await self._publish_event(events_key, {
                    "type": "busy",
                    "session_id": session_id,
                    "owner": status.get("owner", "unknown"),
                    "started_at": status.get("started_at"),
                })
                raise SessionBusyError(
                    owner=status.get("owner", "unknown"),
                    started_at=status.get("started_at"),
                )

        # 工具调用缓冲区（与 chat.py / ws_chat.py 保持一致的事件形状）
        pending_tool_calls: dict[str, dict] = {}
        pending_tool_results: dict[str, str] = {}
        tool_call_records: dict[str, dict] = {}

        # 累积完整回复内容，用于 PG 双写
        full_reply = ""
        full_thinking = ""

        # 获取 session lock（确保同一会话不会并发执行，且跨传输共享）
        async with acquire_session_lock(self._bus, user_id, session_id):
            try:
                # 标记会话为生成中（多端并发状态广播）
                if self._status_tracker:
                    await self._status_tracker.set_generating(
                        session_id, device_id, message,
                    )

                # 获取或创建 Agent
                agent = await self._session_mgr.get_or_create(user_id, session_id)

                # 注入 OTel 追踪上下文（供 TracingContextMiddleware 使用）
                agent.__tracing_context__ = {
                    "agentscope.user.id": user_id,
                    "agentscope.device.id": device_id,
                }

                # 多实例无状态：每次 run 前强制从 Redis 刷新内存 AgentState，
                # 确保拿到其他实例最近一次 save 写入的最新状态，避免用过期
                # 上下文推理。get_or_create 返回的 agent 与内存缓存中是同一
                # 对象，refresh_state 直接替换其 state 字段，无需重新获取引用。
                await self._session_mgr.refresh_state(user_id, session_id)

                # 发布 run_start 事件（携带 device_id，供多端观察）
                await self._publish_event(events_key, {
                    "type": "run_start",
                    "session_id": session_id,
                    "device_id": device_id,
                })

                # 执行流式回复
                user_msg = UserMsg(name="user", content=message)
                event_count = 0

                async for event in agent.reply_stream(user_msg):
                    # 每 5 个事件检查一次 cancel 信号（多端中断）
                    event_count += 1
                    if self._status_tracker and event_count % 5 == 0:
                        if await self._status_tracker.should_cancel(session_id):
                            await self._publish_event(events_key, {
                                "type": "interrupted",
                                "reason": "cancel_requested",
                                "session_id": session_id,
                            })
                            break
                    if not hasattr(event, "type"):
                        if isinstance(event, Msg):
                            # 最终回复消息
                            await self._publish_event(events_key, {
                                "type": "reply_end",
                                "text": event.get_text_content(),
                                "finished": True,
                            })
                        continue

                    match event.type:
                        case EventType.TEXT_BLOCK_DELTA:
                            full_reply += event.delta
                            await self._publish_event(events_key, {
                                "type": "text_delta",
                                "delta": event.delta,
                            })

                        case EventType.THINKING_BLOCK_DELTA:
                            full_thinking += event.delta
                            await self._publish_event(events_key, {
                                "type": "thinking_delta",
                                "delta": event.delta,
                            })

                        case EventType.TOOL_CALL_START:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            tool_name = getattr(event, "tool_call_name", "")
                            pending_tool_calls[tool_call_id] = {
                                "name": tool_name,
                                "args_buffer": "",
                            }

                        case EventType.TOOL_CALL_DELTA:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            delta = getattr(event, "delta", "")
                            if tool_call_id in pending_tool_calls:
                                pending_tool_calls[tool_call_id]["args_buffer"] += delta

                        case EventType.TOOL_CALL_END:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            if tool_call_id in pending_tool_calls:
                                tool_info = pending_tool_calls.pop(tool_call_id)
                                # 尝试解析参数 JSON
                                args = None
                                if tool_info["args_buffer"]:
                                    try:
                                        args = json.loads(tool_info["args_buffer"])
                                    except (json.JSONDecodeError, TypeError):
                                        args = tool_info["args_buffer"]
                                await self._publish_event(events_key, {
                                    "type": "tool_call",
                                    "tool_name": tool_info["name"],
                                    "tool_call_id": tool_call_id,
                                    "tool_args": args,
                                })
                                tool_call_records[tool_call_id] = {
                                    "tool_name": tool_info["name"],
                                    "tool_call_id": tool_call_id,
                                    "tool_args": args,
                                }

                        case EventType.TOOL_RESULT_START:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            pending_tool_results[tool_call_id] = ""

                        case EventType.TOOL_RESULT_TEXT_DELTA:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            delta = getattr(event, "delta", "")
                            if tool_call_id in pending_tool_results:
                                pending_tool_results[tool_call_id] += delta

                        case EventType.TOOL_RESULT_END:
                            tool_call_id = getattr(event, "tool_call_id", "")
                            result_text = pending_tool_results.pop(tool_call_id, "")
                            state = str(getattr(event, "state", ""))
                            await self._publish_event(events_key, {
                                "type": "tool_result",
                                "tool_call_id": tool_call_id,
                                "state": state,
                                "result": result_text,
                            })
                            # 将 result 回填到工具调用记录
                            if tool_call_id in tool_call_records:
                                tool_call_records[tool_call_id]["result"] = result_text
                                tool_call_records[tool_call_id]["state"] = state

                        case EventType.REPLY_END:
                            await self._publish_event(events_key, {
                                "type": "reply_end",
                                "finished_reason": str(
                                    getattr(event, "finished_reason", "")
                                ),
                                "finished": True,
                            })

                        case _:
                            pass

            except asyncio.CancelledError:
                # 取消时也要持久化已产生的部分（在 finally 中处理），
                # 因此这里仅 re-raise，不要吞掉取消信号。
                raise
            except Exception as e:
                logger.exception("Chat run 异常: user={} session={}", user_id, session_id)
                await self._publish_event(events_key, {
                    "type": "error",
                    "message": str(e),
                })
            finally:
                # 标记会话为空闲（多端并发状态广播）
                if self._status_tracker:
                    await self._status_tracker.set_idle(session_id)

                # 无论成功 / 异常 / 取消，都持久化 Redis 状态
                # （含 tool-only / 部分回复，agent state 已被回复过程改变）
                await self._session_mgr.save(user_id, session_id)

                # PG 双写（镜像 _persist_conversation）：user 消息始终写入，
                # assistant 消息仅在 full_reply 非空时写入；标题按需 upsert。
                if db and getattr(db, "is_initialized", False):
                    try:
                        await db.insert_conversation(user_id, session_id, "user", message)
                        metadata = {}
                        if full_thinking:
                            metadata["thinking"] = full_thinking
                        if tool_call_records:
                            metadata["tool_calls"] = list(tool_call_records.values())
                        if full_reply:
                            await db.insert_conversation(
                                user_id, session_id, "assistant", full_reply,
                                metadata=metadata or None,
                            )
                        title = message[:30] if message else None
                        if title:
                            existing = await db.get_session_title(user_id, session_id)
                            if not existing:
                                await db.upsert_session_title(user_id, session_id, title)
                    except Exception:
                        logger.exception(
                            "Chat run PG 持久化失败: user={} session={}",
                            user_id, session_id,
                        )

                # 发布 run_end 事件
                await self._publish_event(events_key, {
                    "type": "run_end",
                    "session_id": session_id,
                })

    async def _publish_event(self, key: str, event: dict[str, Any]) -> None:
        """发布事件到消息总线（log + broadcast）"""
        entry_id = await self._bus.log_append(
            key,
            event,
            max_len=1000,
        )
        await self._bus.publish(key, {**event, "_entry_id": entry_id})
