# -*- coding: utf-8 -*-

"""对话 API 路由

端点：
- POST /chat          — 非流式对话（等待完整回复）
- POST /chat/stream   — 流式对话（SSE 事件流）
- POST /chat/         — Fire-and-Forget 触发（新版）
- GET  /sessions/{session_id}/stream — SSE 事件流订阅（新版）
- GET  /health        — 健康检查
- GET  /sessions      — 列出活跃会话
- GET  /sessions/{user_id} — 列出用户历史会话
- GET  /sessions/{user_id}/{session_id}/messages — 获取消息历史
- DELETE /sessions/{user_id}/{session_id} — 关闭指定会话
- POST /sessions/{user_id}/{session_id}/fork — 基于父会话创建分支
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

logger = logging.getLogger(__name__)

router = APIRouter()


async def _persist_conversation(
    session_mgr,
    db,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
) -> None:
    """后台异步持久化对话（Redis + PG）"""
    try:
        # 写 Redis
        await session_mgr.save(user_id, session_id)
        # 写 PG
        if db and db.is_initialized:
            await db.insert_conversation(user_id, session_id, "user", user_message)
            await db.insert_conversation(user_id, session_id, "assistant", assistant_reply)
            # 自动创建/更新 sessions 记录（标题取首条用户消息前30字）
            title = user_message[:30] if user_message else None
            if title:
                existing = await db.get_session_title(user_id, session_id)
                if not existing:
                    await db.upsert_session_title(user_id, session_id, title)
    except Exception:
        logger.exception("后台持久化失败")


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class ChatRequest(BaseModel):
    """对话请求"""
    message: str = Field(description="用户消息内容")
    user_id: str = Field(
        default="anonymous",
        description="用户标识（区分不同用户）",
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID（不传则自动创建新会话）",
    )


class ChatResponse(BaseModel):
    """对话响应"""
    reply: str = Field(description="Agent 回复文本")
    user_id: str = Field(description="用户标识")
    session_id: str = Field(description="会话 ID")
    reply_id: str = Field(default="", description="回复 ID")


# ============================================================
# SSE 事件格式化
# ============================================================

def _sse_event(event_type: str, data: dict | str) -> str:
    """格式化为 SSE 事件"""
    payload = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return f"event: {event_type}\ndata: {payload}\n"


# ============================================================
# 端点
# ============================================================

@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    """非流式对话 — 等待完整回复后返回"""
    session_mgr = request.app.state.session_manager
    db = getattr(request.app.state, "database_manager", None)

    # 确保 session_id
    session_id = body.session_id or str(uuid.uuid4())

    # 获取或创建该会话的 Agent
    agent = await session_mgr.get_or_create(body.user_id, session_id)

    user_msg = UserMsg(name="user", content=body.message)
    reply_msg = await agent.reply(user_msg)

    # 先返回响应
    response = ChatResponse(
        reply=reply_msg.get_text_content(),
        user_id=body.user_id,
        session_id=session_id,
        reply_id=getattr(reply_msg, "id", ""),
    )

    # 后台异步持久化（不阻塞响应）
    asyncio.create_task(_persist_conversation(
        session_mgr, db, body.user_id, session_id,
        body.message, reply_msg.get_text_content(),
    ))

    return response


@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatRequest):
    """流式对话 — SSE 事件流

    事件类型：
    - text_delta     : 文本增量
    - thinking_delta : 思考增量
    - tool_call      : 工具调用开始
    - tool_result    : 工具执行结果
    - reply_end      : 回复结束
    - error          : 错误
    """
    session_mgr = request.app.state.session_manager
    db = getattr(request.app.state, "database_manager", None)
    session_id = body.session_id or str(uuid.uuid4())

    agent = await session_mgr.get_or_create(body.user_id, session_id)

    user_msg = UserMsg(name="user", content=body.message)

    async def event_generator() -> AsyncGenerator[str, None]:
        # 先发送 session_id，让前端知道当前会话
        yield _sse_event("session", {
            "user_id": body.user_id,
            "session_id": session_id,
        })

        # 用于累积工具调用参数和结果
        pending_tool_calls: dict[str, dict] = {}  # tool_call_id -> {name, args_buffer}
        pending_tool_results: dict[str, str] = {}  # tool_call_id -> result_buffer

        # 累积完整回复内容，用于 PG 双写
        full_reply = ""

        try:
            async for event in agent.reply_stream(user_msg):
                if not hasattr(event, "type"):
                    if isinstance(event, Msg):
                        yield _sse_event("reply_end", {
                            "text": event.get_text_content(),
                            "finished": True,
                        })
                    continue

                match event.type:
                    case EventType.TEXT_BLOCK_DELTA:
                        full_reply += event.delta
                        yield _sse_event("text_delta", {"delta": event.delta})

                    case EventType.THINKING_BLOCK_DELTA:
                        yield _sse_event("thinking_delta", {"delta": event.delta})

                    case EventType.TOOL_CALL_START:
                        tool_call_id = getattr(event, "tool_call_id", "")
                        tool_name = getattr(event, "tool_call_name", "")
                        logger.info("TOOL_CALL_START: id=%s name=%s", tool_call_id, tool_name)
                        pending_tool_calls[tool_call_id] = {
                            "name": tool_name,
                            "args_buffer": "",
                        }

                    case EventType.TOOL_CALL_DELTA:
                        tool_call_id = getattr(event, "tool_call_id", "")
                        delta = getattr(event, "delta", "")
                        logger.info("TOOL_CALL_DELTA: id=%s delta=%s", tool_call_id, delta[:100] if delta else "")
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
                                    import json
                                    args = json.loads(tool_info["args_buffer"])
                                except:
                                    args = tool_info["args_buffer"]
                            logger.info("TOOL_CALL_END: id=%s name=%s args=%s", tool_call_id, tool_info["name"], str(args)[:200] if args else None)
                            yield _sse_event("tool_call", {
                                "tool_name": tool_info["name"],
                                "tool_call_id": tool_call_id,
                                "tool_args": args,
                            })

                    case EventType.TOOL_RESULT_START:
                        tool_call_id = getattr(event, "tool_call_id", "")
                        tool_name = getattr(event, "tool_call_name", "")
                        logger.info("TOOL_RESULT_START: id=%s name=%s", tool_call_id, tool_name)
                        pending_tool_results[tool_call_id] = ""

                    case EventType.TOOL_RESULT_TEXT_DELTA:
                        tool_call_id = getattr(event, "tool_call_id", "")
                        delta = getattr(event, "delta", "")
                        if tool_call_id in pending_tool_results:
                            pending_tool_results[tool_call_id] += delta

                    case EventType.TOOL_RESULT_END:
                        tool_call_id = getattr(event, "tool_call_id", "")
                        result_text = pending_tool_results.pop(tool_call_id, "")
                        logger.info("TOOL_RESULT_END: id=%s result=%s", tool_call_id, result_text[:200] if result_text else "")
                        yield _sse_event("tool_result", {
                            "tool_call_id": tool_call_id,
                            "state": str(getattr(event, "state", "")),
                            "result": result_text,
                        })

                    case EventType.REPLY_END:
                        yield _sse_event("reply_end", {
                            "finished_reason": str(getattr(event, "finished_reason", "")),
                            "finished": True,
                        })

                    case _:
                        pass

        except Exception as e:
            logger.exception("流式回复异常")
            yield _sse_event("error", {"message": str(e)})
        finally:
            # 后台异步持久化（不阻塞流式响应）
            if full_reply:
                asyncio.create_task(_persist_conversation(
                    session_mgr, db, body.user_id, session_id,
                    body.message, full_reply,
                ))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
async def health(request: Request):
    """健康检查"""
    session_mgr = request.app.state.session_manager
    return {
        "status": "ok",
        "active_sessions": session_mgr.active_count,
    }


@router.get("/sessions")
async def list_sessions(request: Request, user_id: str | None = None):
    """列出活跃会话（仅内存中）"""
    session_mgr = request.app.state.session_manager
    sessions = await session_mgr.list_sessions(user_id)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{user_id}")
async def list_user_sessions(request: Request, user_id: str):
    """列出指定用户的所有历史会话（从 PG 查询）

    返回会话元数据列表，包含标题、时间、消息数等信息。
    用于前端侧边栏展示。
    """
    db = getattr(request.app.state, "database_manager", None)
    if not db or not db.is_initialized:
        raise HTTPException(503, "数据库未配置")

    sessions = await db.get_user_sessions(user_id)

    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{user_id}/{session_id}/messages")
async def get_session_messages(
    request: Request,
    user_id: str,
    session_id: str,
    before_id: int | None = None,
    limit: int = 50,
):
    """获取会话的消息历史（从 PG 查询，支持游标分页）

    Args:
        before_id: 游标，获取此 ID 之前的消息（用于加载更多）
        limit: 每页消息数，默认 50

    用于前端切换会话时加载聊天记录，支持滚动加载更多。
    """
    db = getattr(request.app.state, "database_manager", None)
    if not db or not db.is_initialized:
        raise HTTPException(503, "数据库未配置")

    result = await db.get_conversation_history(
        user_id, session_id,
        before_id=before_id,
        limit=limit,
    )

    return result


@router.post("/sessions/{user_id}/{session_id}/delete")
async def soft_delete_session(request: Request, user_id: str, session_id: str):
    """软删除会话（标记为 deleted，实际删除由数据部门处理）

    - PG: 将 conversations 表中该会话所有消息标记为 deleted
    - Redis: 清除会话状态
    """
    db = getattr(request.app.state, "database_manager", None)
    session_mgr = request.app.state.session_manager

    # PG 软删除
    affected = 0
    if db and db.is_initialized:
        affected = await db.soft_delete_session(user_id, session_id)

    # Redis 清除（可选，也可以保留让其自然过期）
    await session_mgr.delete_session(user_id, session_id)

    return {
        "status": "deleted",
        "user_id": user_id,
        "session_id": session_id,
        "affected_messages": affected,
    }


class RenameSessionRequest(BaseModel):
    """重命名会话请求"""
    title: str = Field(description="新会话标题", max_length=100)


@router.post("/sessions/{user_id}/{session_id}/rename")
async def rename_session(
    request: Request,
    user_id: str,
    session_id: str,
    body: RenameSessionRequest,
):
    """重命名会话

    标题存储在 sessions 表的 config 字段中。
    """
    db = getattr(request.app.state, "database_manager", None)
    if not db or not db.is_initialized:
        raise HTTPException(503, "数据库未配置")

    await db.upsert_session_title(user_id, session_id, body.title)

    return {
        "status": "ok",
        "user_id": user_id,
        "session_id": session_id,
        "title": body.title,
    }


class ForkSessionResponse(BaseModel):
    """会话分支响应"""
    session_id: str = Field(description="新创建的子会话 ID")
    parent_session_id: str = Field(description="父会话 ID")
    title: str = Field(description="子会话标题")


@router.post("/sessions/{user_id}/{session_id}/fork", response_model=ForkSessionResponse)
async def fork_session(request: Request, user_id: str, session_id: str):
    """基于父会话创建分支，返回新会话信息"""
    session_mgr = request.app.state.session_manager
    try:
        child_sid = await session_mgr.fork_session(user_id, session_id)
    except ValueError as e:
        # 父会话在 Redis 中无 state（不存在或已过期）
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        # Redis 未初始化
        raise HTTPException(status_code=503, detail=str(e))

    # 读取子会话元数据给前端（_load_session_meta 内部已吞异常返回 None）
    meta = await session_mgr._load_session_meta(user_id, child_sid)
    title = meta.get("title", "新会话(分支)") if meta else "新会话(分支)"

    return ForkSessionResponse(
        session_id=child_sid,
        parent_session_id=session_id,
        title=title,
    )


# ============================================================
# 新版 Fire-and-Forget 端点
# ============================================================

class ChatTriggerRequest(BaseModel):
    """Fire-and-Forget 触发请求"""
    message: str = Field(description="用户消息内容")
    user_id: str = Field(default="anonymous", description="用户标识")
    session_id: str | None = Field(default=None, description="会话 ID")


class ChatTriggerResponse(BaseModel):
    """Fire-and-Forget 触发响应"""
    status: str = Field(description="状态: started")
    session_id: str = Field(description="会话 ID")


@router.post("/chat/", response_model=ChatTriggerResponse)
async def chat_trigger(request: Request, body: ChatTriggerRequest):
    """Fire-and-Forget 触发 — 立即返回，后台执行

    事件通过 GET /sessions/{session_id}/stream 订阅。
    """
    chat_service = request.app.state.chat_service
    message_bus = request.app.state.message_bus
    session_id = body.session_id or str(uuid.uuid4())

    # 检查是否已有 run 在执行
    from agentscope.app.message_bus import MessageBusKeys
    lock_key = MessageBusKeys.session_lock(session_id)
    if await message_bus.is_locked(lock_key):
        return JSONResponse(
            status_code=409,
            content={"detail": "该会话已有对话在执行中"},
        )

    # 后台触发 chat run
    asyncio.create_task(
        chat_service.run(body.user_id, session_id, body.message)
    )

    return ChatTriggerResponse(status="started", session_id=session_id)


@router.get("/sessions/{session_id}/stream")
async def session_event_stream(request: Request, session_id: str):
    """SSE 事件流订阅 — 实时接收 session 事件

    先回放已有事件，然后订阅实时事件。
    """
    from agentscope.app.message_bus import MessageBusKeys

    message_bus = request.app.state.message_bus
    events_key = MessageBusKeys.session_events(session_id)

    async def _sse_generator() -> AsyncGenerator[str, None]:
        # 1. 回放已有事件
        for _entry_id, event in await message_bus.log_read(
            events_key,
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n"

        # 2. 订阅实时事件
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _feeder() -> None:
            try:
                async for evt in message_bus.subscribe(events_key):
                    await queue.put(
                        {k: v for k, v in evt.items() if k != "_entry_id"},
                    )
            except asyncio.CancelledError:
                pass
            finally:
                await queue.put(None)

        feeder_task = asyncio.create_task(_feeder())

        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30)
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n"
                except asyncio.TimeoutError:
                    # 心跳
                    yield ":\n"
        finally:
            feeder_task.cancel()
            try:
                await feeder_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
