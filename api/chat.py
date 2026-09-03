# -*- coding: utf-8 -*-

"""对话 API 路由

端点：
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
import contextlib
import json
import os
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

from loguru import logger

from core.chat_service import acquire_session_lock, session_lock_key
from core.session_status import SessionBusyError, SessionStatusTracker
from core.token_counter import count_tokens
from core.validators import coerce_id, is_auth_enabled, require_user_id


async def _stream_with_timeout(agen, timeout: int):
    """包装异步生成器，添加空闲超时保护。

    如果在 timeout 秒内没有从 LLM 收到任何事件，抛出 asyncio.TimeoutError。
    避免 LLM 提供商挂起时 SSE/Socket 连接无限期等待。
    """
    while True:
        try:
            event = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
            yield event
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise  # 向上传播，由调用方处理

router = APIRouter()


def _validate_user_id(request: Request, user_id: str) -> str:
    """认证启用时校验 user_id — 不允许 'anonymous'。

    认证关闭时保持原有 coerce_id 行为（静默回退到 anonymous）。
    """
    config = getattr(request.app.state, "config", None)
    try:
        return require_user_id(user_id, is_auth_enabled(config))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="认证启用时必须提供有效的 user_id（不允许 'anonymous'）",
        )

# 追踪后台持久化任务，避免被 GC 回收（见 shutdown_persist_tasks）。
_PERSIST_TASKS: set[asyncio.Task] = set()


async def shutdown_persist_tasks() -> None:
    """优雅等待所有未完成的后台持久化任务完成（服务关闭时调用，避免丢弃在途写入）。

    由 DRAIN 语义替代原先的 cancel()：原先直接 cancel 会在任务尚未落库时丢弃在途写入。
    """
    tasks = list(_PERSIST_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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
    """后台异步持久化对话历史（仅 PG 双写；Redis AgentState 已在锁内同步落库）。"""
    try:
        # Redis AgentState 已在 /chat/stream 的 async with 锁内同步落库，
        # 此处仅负责 PG 历史双写（append-only，风险较低）。
        # 写 PG
        if db and db.is_initialized:
            await db.insert_conversation(user_id, session_id, "user", user_message)
            metadata = {}
            if thinking:
                metadata["thinking"] = thinking
            if tool_calls:
                metadata["tool_calls"] = tool_calls
            # assistant 消息仅在确有文本产出时写入（tool-only turn 跳过）
            if assistant_reply:
                await db.insert_conversation(
                    user_id, session_id, "assistant", assistant_reply,
                    metadata=metadata or None,
                )
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
    device_id: str = Field(
        default="unknown",
        description="设备标识（用于多端并发状态广播）",
    )


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
    bus = getattr(request.app.state, "message_bus", None)
    status_tracker = getattr(request.app.state, "session_status_tracker", None)

    # 规范化用户 / 会话标识（防止路径穿越等非法输入）
    user_id = _validate_user_id(request, body.user_id)
    session_id = body.session_id or str(uuid.uuid4())
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    device_id = body.device_id or "unknown"

    # 多端并发：检查会话是否已被其他设备占用
    if status_tracker:
        status = await status_tracker.get_status(session_id)
        if status.get("state") == "generating":
            return JSONResponse(
                status_code=409,
                content={
                    "error": "session_busy",
                    "message": "该会话正在处理中",
                    "owner_device": status.get("owner", "unknown"),
                    "started_at": status.get("started_at"),
                    "suggestion": "等待完成或发送中断请求",
                },
            )

    async def event_generator() -> AsyncGenerator[str, None]:
        # 先发送 session_id，让前端知道当前会话
        yield _sse_event("session", {
            "user_id": user_id,
            "session_id": session_id,
        })

        # 用于累积工具调用参数和结果
        pending_tool_calls: dict[str, dict] = {}  # tool_call_id -> {name, args_buffer}
        pending_tool_results: dict[str, str] = {}  # tool_call_id -> result_buffer
        tool_call_records: dict[str, dict] = {}  # tool_call_id -> record（用于持久化）

        # 累积完整回复内容，用于 PG 双写
        full_reply = ""
        full_thinking = ""

        lock_ctx = (
            acquire_session_lock(bus, user_id, session_id)
            if bus is not None else contextlib.nullasynccontext()
        )
        try:
            async with lock_ctx:
                try:
                    # 标记会话为生成中（多端并发状态广播）
                    if status_tracker:
                        await status_tracker.set_generating(
                            session_id, device_id, body.message,
                        )

                    agent = await session_mgr.get_or_create(user_id, session_id)
                    # 注入 OTel 追踪上下文（供 TracingContextMiddleware 使用）
                    agent.__tracing_context__ = {
                        "agentscope.user.id": user_id,
                        "agentscope.device.id": device_id,
                    }
                    await session_mgr.refresh_state(user_id, session_id)
                    user_msg = UserMsg(name="user", content=body.message)

                    event_count = 0

                    # LLM 超时保护：config.llm.timeout（默认 120s）
                    llm_timeout = getattr(
                        getattr(request.app.state.config, "llm", None),
                        "timeout", 120,
                    )
                    async for event in _stream_with_timeout(
                        agent.reply_stream(user_msg), llm_timeout,
                    ):
                        # 每 5 个事件检查一次 cancel 信号（多端中断）
                        event_count += 1
                        if status_tracker and event_count % 5 == 0:
                            if await status_tracker.should_cancel(session_id):
                                yield _sse_event("interrupted", {
                                    "reason": "cancel_requested",
                                    "session_id": session_id,
                                })
                                break

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
                                full_thinking += event.delta
                                yield _sse_event("thinking_delta", {"delta": event.delta})

                            case EventType.TOOL_CALL_START:
                                tool_call_id = getattr(event, "tool_call_id", "")
                                tool_name = getattr(event, "tool_call_name", "")
                                logger.info("TOOL_CALL_START: id={} name={}", tool_call_id, tool_name)
                                pending_tool_calls[tool_call_id] = {
                                    "name": tool_name,
                                    "args_buffer": "",
                                }

                            case EventType.TOOL_CALL_DELTA:
                                tool_call_id = getattr(event, "tool_call_id", "")
                                delta = getattr(event, "delta", "")
                                logger.info("TOOL_CALL_DELTA: id={} delta={}", tool_call_id, delta[:100] if delta else "")
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
                                    logger.info("TOOL_CALL_END: id={} name={} args={}", tool_call_id, tool_info["name"], str(args)[:200] if args else None)
                                    yield _sse_event("tool_call", {
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
                                tool_name = getattr(event, "tool_call_name", "")
                                logger.info("TOOL_RESULT_START: id={} name={}", tool_call_id, tool_name)
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
                                logger.info("TOOL_RESULT_END: id={} result={}", tool_call_id, result_text[:200] if result_text else "")
                                yield _sse_event("tool_result", {
                                    "tool_call_id": tool_call_id,
                                    "state": state,
                                    "result": result_text,
                                })
                                if tool_call_id in tool_call_records:
                                    tool_call_records[tool_call_id]["result"] = result_text
                                    tool_call_records[tool_call_id]["state"] = state

                            case EventType.REPLY_END:
                                yield _sse_event("reply_end", {
                                    "finished_reason": str(getattr(event, "finished_reason", "")),
                                    "finished": True,
                                })

                            case _:
                                pass
                finally:
                    # 标记会话为空闲（多端并发状态广播）
                    if status_tracker:
                        await status_tracker.set_idle(session_id)

                    # Redis AgentState 必须在锁内同步落库，避免下一轮 refresh_state
                    # 读取到过期状态导致本轮对话丢失（state-loss race）。
                    await session_mgr.save(user_id, session_id)

        except GeneratorExit:
            # 客户端断开连接时生成器被关闭，OTel tracing middleware
            # 的上下文清理可能抛出 "token was created in a different Context"，
            # 这是已知的 OTel + async generator 问题，不影响功能。
            logger.debug("SSE 生成器关闭: user={} session={}", body.user_id, session_id)
        except asyncio.TimeoutError:
            logger.error("LLM 调用超时: user={} session={}", body.user_id, session_id)
            yield _sse_event("error", {"message": f"LLM 调用超时（{llm_timeout}s），请重试"})
        except Exception as e:
            logger.exception("流式回复异常")
            yield _sse_event("error", {"message": str(e)})
        finally:
            # PG 历史双写为 append-only 且风险较低，保留为 fire-and-forget；
            # Redis AgentState 已在上方锁内 awaited 落库（state-loss race 修复）。
            task = asyncio.create_task(_persist_conversation(
                session_mgr, db, user_id, session_id,
                body.message, full_reply,
                thinking=full_thinking or None,
                tool_calls=list(tool_call_records.values()) or None,
            ))
            _PERSIST_TASKS.add(task)
            task.add_done_callback(_PERSIST_TASKS.discard)

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
    """健康检查 — 验证 Redis / PostgreSQL 连通性"""
    session_mgr = request.app.state.session_manager
    config = getattr(request.app.state, "config", None)
    checks = {}
    overall_ok = True

    # Redis 检查
    try:
        import redis as redis_lib
        redis_url = getattr(getattr(config, "redis", None), "url", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url, socket_timeout=3)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        overall_ok = False

    # PostgreSQL 检查
    db = getattr(request.app.state, "database_manager", None)
    if db and db.is_initialized:
        try:
            result = await db.fetchval("SELECT 1")
            checks["postgres"] = "ok" if result == 1 else f"unexpected: {result}"
        except Exception as e:
            checks["postgres"] = f"error: {e}"
            overall_ok = False
    else:
        checks["postgres"] = "not_configured"

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if overall_ok else "degraded",
            "checks": checks,
            "active_sessions": session_mgr.active_count,
        },
    )


@router.get("/sessions")
async def list_sessions(request: Request, user_id: str | None = None):
    """列出活跃会话（仅内存中）"""
    # 规范化用户标识（防止非法输入）；未提供 user_id 时保持 None 以列出全部会话。
    if user_id is not None:
        user_id = coerce_id(user_id)
    session_mgr = request.app.state.session_manager
    sessions = await session_mgr.list_sessions(user_id)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{user_id}")
async def list_user_sessions(request: Request, user_id: str):
    """列出指定用户的所有历史会话（从 PG 查询）

    返回会话元数据列表，包含标题、时间、消息数等信息。
    用于前端侧边栏展示。
    """
    user_id = coerce_id(user_id)
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
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
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
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
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
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
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
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    session_mgr = request.app.state.session_manager
    logger.info("Fork 请求: user={} session={}", user_id, session_id)

    # 确保父会话在 Redis 中有 state（从内存或 PG 恢复并保存到 Redis）
    try:
        await session_mgr.get_or_create(user_id, session_id)
        logger.info("父会话已加载: user={} session={}", user_id, session_id)
    except Exception:
        logger.exception("加载父会话失败: user={} session={}", user_id, session_id)

    try:
        child_sid = await session_mgr.fork_session(user_id, session_id)
    except ValueError as e:
        logger.warning("Fork 失败 (ValueError): {}", e)
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.warning("Fork 失败 (RuntimeError): {}", e)
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
    device_id: str = Field(default="unknown", description="设备标识")


class ChatTriggerResponse(BaseModel):
    """Fire-and-Forget 触发响应"""
    status: str = Field(description="状态: started")
    session_id: str = Field(description="会话 ID")


@router.get("/sessions/{user_id}/{session_id}/context")
async def get_session_context(
    request: Request,
    user_id: str,
    session_id: str,
):
    """获取会话的上下文用量信息

    返回当前会话的 token 估算、消息数、压缩状态等。
    用于前端上下文用量指示器。
    """
    # 规范化用户 / 会话标识（防止路径穿越等非法输入）
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))

    session_mgr = request.app.state.session_manager
    config = request.app.state.config
    session_mgr_list = await session_mgr.list_sessions(user_id)

    # 检查会话是否存在
    session_exists = any(
        s["session_id"] == session_id for s in session_mgr_list
    )

    # 从 Redis 获取状态
    state = await session_mgr._load_state(user_id, session_id)
    if state is None:
        # 尝试从内存获取
        entry = session_mgr._sessions.get((user_id, session_id))
        if entry:
            state = entry.agent.state

    context_size = config.llm.context_size
    trigger_ratio = config.agent.context_trigger_ratio
    reserve_ratio = config.agent.context_reserve_ratio

    if state is None:
        return {
            "estimated_tokens": 0,
            "context_window": context_size,
            "usage_ratio": 0.0,
            "trigger_ratio": trigger_ratio,
            "reserve_ratio": reserve_ratio,
            "status": "healthy",
            "message_count": 0,
            "summary_exists": False,
        }

    # 估算 token 数（使用 tiktoken 精确计数）
    context = getattr(state, "context", [])
    total_text = ""
    msg_count = len(context)
    for msg in context:
        content = getattr(msg, "content", [])
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", "")
                if text:
                    total_text += text + "\n"
        elif isinstance(content, str):
            total_text += content + "\n"

    # 获取模型名称用于选择编码器
    model_name = getattr(getattr(config, "llm", None), "model", "default")
    estimated_tokens = count_tokens(total_text, model_name=model_name)
    usage_ratio = estimated_tokens / context_size if context_size > 0 else 0.0

    # 状态判断
    if usage_ratio < 0.35:
        status = "healthy"
    elif usage_ratio < 0.45:
        status = "warning"
    else:
        status = "critical"

    # 检查是否有摘要
    summary_exists = any(
        getattr(msg, "name", "") == "summary"
        for msg in context
    )

    # 检查卸载文件（卸载目录已改为按用户隔离：{sandbox_dir}/{user_id}/sessions/{session_id}）
    # 从配置派生沙箱根，避免硬编码 "workspaces" 绕过 workspace 单一真源。
    from pathlib import Path
    sandbox_base = getattr(config.agent, "sandbox_dir", "workspaces")
    offload_dir = Path(sandbox_base) / user_id / "sessions" / session_id
    offloaded_files = []
    if offload_dir.is_dir():
        offloaded_files = [
            str(f.relative_to(Path(sandbox_base) / user_id))
            for f in offload_dir.iterdir()
            if f.is_file()
        ]

    return {
        "estimated_tokens": estimated_tokens,
        "context_window": context_size,
        "usage_ratio": round(usage_ratio, 3),
        "trigger_ratio": trigger_ratio,
        "reserve_ratio": reserve_ratio,
        "status": status,
        "message_count": msg_count,
        "summary_exists": summary_exists,
        "offloaded_files": offloaded_files,
    }


@router.post("/sessions/{user_id}/{session_id}/compress")
async def compress_session(
    request: Request,
    user_id: str,
    session_id: str,
):
    """手动触发上下文压缩

    调用 Agent 的 compress_context 方法，将历史消息压缩为摘要。
    压缩前会先通过 Offloader 卸载被移除的内容。
    """
    user_id = coerce_id(user_id)
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    session_mgr = request.app.state.session_manager

    # 确保会话已加载
    try:
        agent = await session_mgr.get_or_create(user_id, session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载会话失败: {e}")

    try:
        result = await agent.compress_context()
        # 保存压缩后的状态
        await session_mgr.save(user_id, session_id)

        return {
            "status": "compressed",
            "user_id": user_id,
            "session_id": session_id,
            "messages_compressed": getattr(result, "messages_compressed", 0),
            "tokens_before": getattr(result, "tokens_before", 0),
            "tokens_after": getattr(result, "tokens_after", 0),
        }
    except Exception as e:
        logger.exception("手动压缩失败: user={} session={}", user_id, session_id)
        raise HTTPException(status_code=500, detail=f"压缩失败: {e}")


@router.post("/chat/", response_model=ChatTriggerResponse)
async def chat_trigger(request: Request, body: ChatTriggerRequest):
    """Fire-and-Forget 触发 — 立即返回，后台执行

    事件通过 GET /sessions/{session_id}/stream 订阅。
    """
    chat_service = request.app.state.chat_service
    message_bus = request.app.state.message_bus
    db = getattr(request.app.state, "database_manager", None)
    status_tracker = getattr(request.app.state, "session_status_tracker", None)

    # 规范化用户 / 会话标识（防止路径穿越等非法输入）
    user_id = _validate_user_id(request, body.user_id)
    session_id = body.session_id or str(uuid.uuid4())
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    device_id = body.device_id or "unknown"

    # 多端并发：检查会话是否已被其他设备占用
    if status_tracker:
        status = await status_tracker.get_status(session_id)
        if status.get("state") == "generating":
            return JSONResponse(
                status_code=409,
                content={
                    "error": "session_busy",
                    "message": "该会话正在处理中",
                    "owner_device": status.get("owner", "unknown"),
                    "started_at": status.get("started_at"),
                },
            )

    # 检查是否已有 run 在执行（与 run() 内部使用的锁 key 一致）
    lock_key = session_lock_key(user_id, session_id)
    if await message_bus.is_locked(lock_key):
        return JSONResponse(
            status_code=409,
            content={"detail": "该会话已有对话在执行中"},
        )

    # 后台触发 chat run（传入 db 以进行 PG 双写）
    task = asyncio.create_task(
        chat_service.run(user_id, session_id, body.message, db, device_id=device_id)
    )
    _PERSIST_TASKS.add(task)
    task.add_done_callback(_PERSIST_TASKS.discard)

    return ChatTriggerResponse(status="started", session_id=session_id)


@router.get("/sessions/{session_id}/stream")
async def session_event_stream(request: Request, session_id: str):
    """SSE 事件流订阅 — 实时接收 session 事件

    先回放已有事件，然后订阅实时事件。
    """
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
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


# ============================================================
# 多端并发 — 中断与状态端点
# ============================================================

class InterruptRequest(BaseModel):
    """中断请求"""
    device_id: str = Field(default="unknown", description="发起中断的设备标识")


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    request: Request,
    session_id: str,
    body: InterruptRequest,
):
    """请求中断当前生成（任意设备可调用）

    生成者会在下一个事件检查点停止，所有设备收到 interrupted 事件。
    """
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    status_tracker = getattr(request.app.state, "session_status_tracker", None)
    if not status_tracker:
        raise HTTPException(503, "状态跟踪器未配置")

    success = await status_tracker.request_cancel(session_id, body.device_id)
    if not success:
        return {"status": "no_active_generation"}

    return {"status": "cancel_requested"}


@router.get("/sessions/{session_id}/status")
async def get_session_status(request: Request, session_id: str):
    """查询会话当前状态（idle/generating/interrupting）"""
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))
    status_tracker = getattr(request.app.state, "session_status_tracker", None)
    if not status_tracker:
        return {"state": "idle"}

    return await status_tracker.get_status(session_id)
