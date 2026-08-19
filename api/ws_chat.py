# -*- coding: utf-8 -*-

"""WebSocket 对话端点

提供全双工流式对话通道，替代 SSE 的单向通信模式。

协议：
    ws://localhost:8090/ws/chat?user_id={user_id}&session_id={session_id}

客户端→服务端消息：
    {"type": "chat",   "payload": {"message": "用户输入"}}
    {"type": "cancel",  "payload": {}}
    {"type": "ping",    "payload": {}}

服务端→客户端消息：
    {"type": "connected",      "payload": {"session_id", "user_id"}}
    {"type": "text_delta",     "payload": {"delta": "增量文本"}}
    {"type": "thinking_delta", "payload": {"delta": "思考内容"}}
    {"type": "tool_call",      "payload": {"tool_name", "tool_call_id", "tool_args"}}
    {"type": "tool_result",    "payload": {"tool_call_id", "state", "result"}}
    {"type": "reply_end",      "payload": {"finished_reason", "finished": true}}
    {"type": "error",          "payload": {"message": "错误描述"}}
    {"type": "pong",           "payload": {}}
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

from loguru import logger

router = APIRouter()


async def _persist_conversation(
    session_mgr,
    db,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    *,
    thinking: str | None = None,
    tool_calls: list[dict] | None = None,
) -> None:
    """后台异步持久化对话（Redis + PG）

    Args:
        thinking: 思考过程文本（仅前端展示，不注入 agent 上下文）
        tool_calls: 工具调用记录列表（注入 agent 上下文用于自我排错）
    """
    try:
        await session_mgr.save(user_id, session_id)
        if db and db.is_initialized:
            await db.insert_conversation(user_id, session_id, "user", user_message)
            # 构建 assistant 消息的 metadata
            metadata = {}
            if thinking:
                metadata["thinking"] = thinking
            if tool_calls:
                metadata["tool_calls"] = tool_calls
            await db.insert_conversation(
                user_id, session_id, "assistant", assistant_reply,
                metadata=metadata or None,
            )
            title = user_message[:30] if user_message else None
            if title:
                existing = await db.get_session_title(user_id, session_id)
                if not existing:
                    await db.upsert_session_title(user_id, session_id, title)
    except Exception:
        logger.exception("WebSocket 持久化失败")


async def _send_json(ws: WebSocket, msg_type: str, payload: dict | None = None) -> None:
    """发送 JSON 消息到客户端"""
    await ws.send_json({"type": msg_type, "payload": payload or {}})


@router.websocket("/ws/chat")
async def websocket_chat(
    ws: WebSocket,
    user_id: str = "anonymous",
    session_id: str | None = None,
):
    """WebSocket 流式对话端点

    Args:
        ws: WebSocket 连接
        user_id: 用户标识（query param）
        session_id: 会话 ID（可选，不传则自动创建）
    """
    await ws.accept()
    session_id = session_id or str(uuid.uuid4())

    # 获取服务实例
    session_mgr = ws.app.state.session_manager
    db = getattr(ws.app.state, "database_manager", None)

    # 发送连接确认
    await _send_json(ws, "connected", {
        "session_id": session_id,
        "user_id": user_id,
    })

    logger.info("WebSocket 连接建立: user={} session={}", user_id, session_id)

    # 当前正在执行的生成任务（用于 cancel）
    current_task: asyncio.Task | None = None
    # 取消标志
    cancelled = asyncio.Event()

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat":
                # 防止并发生成
                if current_task and not current_task.done():
                    await _send_json(ws, "error", {
                        "message": "上一轮对话尚未结束，请等待完成或发送 cancel",
                    })
                    continue

                message = data.get("payload", {}).get("message", "")
                if not message:
                    await _send_json(ws, "error", {"message": "消息不能为空"})
                    continue

                # 重置取消标志
                cancelled.clear()

                # 启动生成任务
                current_task = asyncio.create_task(
                    _handle_chat(ws, session_mgr, db, user_id, session_id, message, cancelled)
                )

            elif msg_type == "cancel":
                if current_task and not current_task.done():
                    cancelled.set()
                    current_task.cancel()
                    try:
                        await current_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    await _send_json(ws, "reply_end", {
                        "finished_reason": "cancelled",
                        "finished": True,
                    })
                    logger.info("WebSocket 取消生成: user={} session={}", user_id, session_id)
                current_task = None

            elif msg_type == "ping":
                await _send_json(ws, "pong")

            else:
                await _send_json(ws, "error", {
                    "message": f"未知消息类型: {msg_type}",
                })

    except WebSocketDisconnect:
        logger.info("WebSocket 断开: user={} session={}", user_id, session_id)
    except Exception:
        logger.exception("WebSocket 异常: user={} session={}", user_id, session_id)
    finally:
        # 清理：取消正在执行的任务
        if current_task and not current_task.done():
            current_task.cancel()
            try:
                await current_task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("WebSocket 连接关闭: user={} session={}", user_id, session_id)


async def _handle_chat(
    ws: WebSocket,
    session_mgr,
    db,
    user_id: str,
    session_id: str,
    message: str,
    cancelled: asyncio.Event,
) -> None:
    """处理单轮对话的流式回复"""
    try:
        agent = await session_mgr.get_or_create(user_id, session_id)
        # 多实例场景：强制刷新状态
        await session_mgr.refresh_state(user_id, session_id)

        user_msg = UserMsg(name="user", content=message)
        full_reply = ""
        full_thinking = ""

        # 工具调用缓冲区（与 chat.py SSE 逻辑一致）
        pending_tool_calls: dict[str, dict] = {}
        pending_tool_results: dict[str, str] = {}
        # 已完成的工具调用记录（用于持久化到 metadata）
        tool_call_records: dict[str, dict] = {}  # tool_call_id -> record

        async for event in agent.reply_stream(user_msg):
            # 检查取消
            if cancelled.is_set():
                break

            if not hasattr(event, "type"):
                if isinstance(event, Msg):
                    await _send_json(ws, "reply_end", {
                        "text": event.get_text_content(),
                        "finished": True,
                    })
                continue

            match event.type:
                case EventType.TEXT_BLOCK_DELTA:
                    full_reply += event.delta
                    await _send_json(ws, "text_delta", {"delta": event.delta})

                case EventType.THINKING_BLOCK_DELTA:
                    full_thinking += event.delta
                    await _send_json(ws, "thinking_delta", {"delta": event.delta})

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
                        args = None
                        if tool_info["args_buffer"]:
                            try:
                                args = json.loads(tool_info["args_buffer"])
                            except (json.JSONDecodeError, TypeError):
                                args = tool_info["args_buffer"]
                        await _send_json(ws, "tool_call", {
                            "tool_name": tool_info["name"],
                            "tool_call_id": tool_call_id,
                            "tool_args": args,
                        })
                        # 记录工具调用（等待后续 result 填充）
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
                    await _send_json(ws, "tool_result", {
                        "tool_call_id": tool_call_id,
                        "state": state,
                        "result": result_text,
                    })
                    # 将 result 回填到工具调用记录
                    if tool_call_id in tool_call_records:
                        tool_call_records[tool_call_id]["result"] = result_text
                        tool_call_records[tool_call_id]["state"] = state

                case EventType.REPLY_END:
                    await _send_json(ws, "reply_end", {
                        "finished_reason": str(getattr(event, "finished_reason", "")),
                        "finished": True,
                    })

                case _:
                    pass

        # 后台持久化（含 thinking + tool_calls metadata）
        if full_reply:
            asyncio.create_task(_persist_conversation(
                session_mgr, db, user_id, session_id, message, full_reply,
                thinking=full_thinking or None,
                tool_calls=list(tool_call_records.values()) or None,
            ))

    except asyncio.CancelledError:
        logger.info("对话生成被取消: user={} session={}", user_id, session_id)
        raise
    except Exception as e:
        logger.exception("WebSocket 对话异常: user={} session={}", user_id, session_id)
        try:
            await _send_json(ws, "error", {"message": str(e)})
        except Exception:
            pass
