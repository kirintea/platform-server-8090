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

from agentscope.app.message_bus import MessageBus
from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

from core.session import SessionManager

from loguru import logger


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
    ) -> None:
        self._session_mgr = session_mgr
        self._bus = message_bus

    async def run(
        self,
        user_id: str,
        session_id: str,
        message: str,
    ) -> None:
        """触发一次 chat run（后台执行）

        获取或创建会话 Agent，执行 reply_stream，
        将每个事件发布到 message_bus 的 session events channel。

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            message: 用户消息
        """
        from agentscope.app.message_bus import MessageBusKeys

        events_key = MessageBusKeys.session_events(session_id)
        lock_key = MessageBusKeys.session_lock(session_id)

        # 获取 session lock（确保同一 session 不会并发执行）
        async with self._bus.acquire_lock(
            lock_key,
            ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
        ):
            try:
                # 获取或创建 Agent
                agent = await self._session_mgr.get_or_create(user_id, session_id)

                # 多实例无状态：每次 run 前强制从 Redis 刷新内存 AgentState，
                # 确保拿到其他实例最近一次 save 写入的最新状态，避免用过期
                # 上下文推理。get_or_create 返回的 agent 与内存缓存中是同一
                # 对象，refresh_state 直接替换其 state 字段，无需重新获取引用。
                await self._session_mgr.refresh_state(user_id, session_id)

                # 发布 run_start 事件
                await self._publish_event(events_key, {
                    "type": "run_start",
                    "session_id": session_id,
                })

                # 执行流式回复
                user_msg = UserMsg(name="user", content=message)

                async for event in agent.reply_stream(user_msg):
                    if not hasattr(event, "type"):
                        if isinstance(event, Msg):
                            # 最终回复消息
                            await self._publish_event(events_key, {
                                "type": "reply_end",
                                "text": event.get_text_content(),
                                "msg_id": getattr(event, "id", ""),
                            })
                        continue

                    match event.type:
                        case EventType.TEXT_BLOCK_DELTA:
                            await self._publish_event(events_key, {
                                "type": "text_delta",
                                "delta": event.delta,
                            })

                        case EventType.THINKING_BLOCK_DELTA:
                            await self._publish_event(events_key, {
                                "type": "thinking_delta",
                                "delta": event.delta,
                            })

                        case EventType.TOOL_CALL_START:
                            await self._publish_event(events_key, {
                                "type": "tool_call",
                                "tool_name": getattr(event, "tool_call_name", ""),
                                "tool_call_id": getattr(event, "tool_call_id", ""),
                            })

                        case EventType.TOOL_RESULT_END:
                            await self._publish_event(events_key, {
                                "type": "tool_result",
                                "tool_call_id": getattr(event, "tool_call_id", ""),
                                "state": str(getattr(event, "state", "")),
                            })

                        case EventType.REPLY_END:
                            await self._publish_event(events_key, {
                                "type": "reply_end",
                                "finished_reason": str(
                                    getattr(event, "finished_reason", "")
                                ),
                            })

                        case _:
                            pass

                # 持久化会话状态
                await self._session_mgr.save(user_id, session_id)

            except Exception as e:
                logger.exception("Chat run 异常: user={} session={}", user_id, session_id)
                await self._publish_event(events_key, {
                    "type": "error",
                    "message": str(e),
                })
            finally:
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
