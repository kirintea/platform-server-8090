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
import contextlib
import json
import os
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from agentscope.event import EventType
from agentscope.message import Msg, UserMsg

from loguru import logger

from core.chat_service import acquire_session_lock
from core.session_status import SessionBusyError, SessionState, SessionStatusTracker
from core.validators import coerce_id, coerce_id_strict, is_auth_enabled

router = APIRouter()


async def _stream_with_timeout(agen, timeout: int):
    """包装异步生成器，添加空闲超时保护。"""
    while True:
        try:
            event = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
            yield event
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise

# 追踪后台持久化任务，避免被 GC 回收（见 shutdown_persist_tasks）。
_PERSIST_TASKS: set[asyncio.Task] = set()

# 断连后继续运行的后台生成任务注册表：(user_id, session_id) → asyncio.Task
# 用于重连时查询同一 session 是否有任务在后台运行。
_BACKGROUND_TASKS: dict[tuple[str, str], asyncio.Task] = {}

# 后台生成保护：防止断连后任务无限运行。
REPLY_MAX_DURATION = 300    # 总时长上限：5 分钟
REPLY_MAX_CHARS = 50_000    # 回复字符上限


async def shutdown_persist_tasks() -> None:
    """优雅等待所有未完成的后台任务完成（服务关闭时调用，避免丢弃在途写入）。

    由 DRAIN 语义替代原先的 cancel()：原先直接 cancel 会在任务尚未落库时丢弃在途写入。
    等待范围：持久化任务 + 断连后仍在运行的后台生成任务。
    """
    tasks = list(_PERSIST_TASKS) + list(_BACKGROUND_TASKS.values())
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
    """后台异步持久化对话历史（仅 PG 双写；Redis AgentState 已在锁内同步落库）。

    Args:
        thinking: 思考过程文本（仅前端展示，不注入 agent 上下文）
        tool_calls: 工具调用记录列表（注入 agent 上下文用于自我排错）
    """
    try:
        # Redis AgentState 已在 _handle_chat 的 async with 锁内同步落库，
        # 此处仅负责 PG 历史双写（append-only，风险较低）。
        if db and db.is_initialized:
            await db.insert_conversation(user_id, session_id, "user", user_message)
            # 构建 assistant 消息的 metadata
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
            title = user_message[:30] if user_message else None
            if title:
                existing = await db.get_session_title(user_id, session_id)
                if not existing:
                    await db.upsert_session_title(user_id, session_id, title)
    except Exception:
        logger.exception("WebSocket 持久化失败")


async def _send_json(ws: WebSocket, msg_type: str, payload: dict | None = None) -> bool:
    """发送 JSON 消息到客户端，连接已断开时返回 False 而非抛异常。"""
    try:
        await ws.send_json({"type": msg_type, "payload": payload or {}})
        return True
    except Exception:
        return False


@router.websocket("/ws/chat")
async def websocket_chat(
    ws: WebSocket,
    user_id: str = "anonymous",
    session_id: str | None = None,
    device_id: str = "unknown",
):
    """WebSocket 流式对话端点（支持多端并发观察者模式）

    Args:
        ws: WebSocket 连接
        user_id: 用户标识（query param）
        session_id: 会话 ID（可选，不传则自动创建）
        device_id: 设备标识（query param，用于多端状态广播）
    """
    await ws.accept()
    # 规范化用户 / 会话标识（防止路径穿越等非法输入）
    # 认证启用时，严格校验 user_id（不允许 'anonymous'）
    config = getattr(ws.app.state, "config", None)
    auth_required = is_auth_enabled(config)
    if auth_required:
        try:
            user_id = coerce_id_strict(user_id, "user_id")
        except ValueError:
            await _send_json(ws, "error", {"message": "认证启用时必须提供有效的 user_id"})
            await ws.close(code=4400, reason="invalid user_id")
            return
    else:
        user_id = coerce_id(user_id)
    session_id = session_id or str(uuid.uuid4())
    session_id = coerce_id(session_id, default=str(uuid.uuid4()))

    # --- 浏览器兼容的 WebSocket 认证 ---
    # 浏览器无法在 WS 握手时设置自定义 header，故采用「首帧认证」：
    # 启用 AUTH_REQUIRED=true 时，客户端须先发送
    #   {"type": "auth", "payload": {"api_key": "<API_KEY>"}}
    # 否则服务端直接关闭连接（code=4401）。未启用时则忽略任何 auth 帧，
    # 首帧无论是 auth 还是 chat 均按原协议处理（不再从 URL 读取 api_key）。
    auth_required = os.environ.get("AUTH_REQUIRED") == "true"
    if auth_required:
        try:
            first = await ws.receive_json()
        except Exception:
            await ws.close(code=4401, reason="unauthorized")
            return
        payload = first.get("payload") if isinstance(first, dict) else None
        api_key = payload.get("api_key") if isinstance(payload, dict) else None
        if not (
            isinstance(first, dict)
            and first.get("type") == "auth"
            and api_key == os.environ.get("API_KEY")
        ):
            await ws.close(code=4401, reason="unauthorized")
            return

    # 获取服务实例
    session_mgr = ws.app.state.session_manager
    db = getattr(ws.app.state, "database_manager", None)
    message_bus = getattr(ws.app.state, "message_bus", None)
    status_tracker = getattr(ws.app.state, "session_status_tracker", None)

    # 发送连接确认
    await _send_json(ws, "connected", {
        "session_id": session_id,
        "user_id": user_id,
        "device_id": device_id,
    })

    logger.info("WebSocket 连接建立: user={} session={} device={}", user_id, session_id, device_id)

    # --- 重连协调：检查同一 session 是否有待取回复或后台任务 ---
    bg_key = (user_id, session_id)
    bg_task = _BACKGROUND_TASKS.get(bg_key)
    if bg_task and not bg_task.done():
        # 旧连接断开后任务仍在后台运行
        await _send_json(ws, "generation_in_progress", {
            "message": "上一轮回复仍在生成中，请等待",
        })
    else:
        # 检查是否有断连期间生成完成的回复
        try:
            meta = await session_mgr.load_session_meta(user_id, session_id)
            if meta and meta.get("reply_status") == "completed" and meta.get("last_reply"):
                await _send_json(ws, "pending_reply", {
                    "text": meta["last_reply"],
                    "reply_status": "completed",
                })
        except Exception:
            pass

    # 当前正在执行的生成任务（用于 cancel）
    current_task: asyncio.Task | None = None
    # 取消标志
    cancelled = asyncio.Event()

    # --- 观察者模式：订阅 session 事件流（后台任务） ---
    from agentscope.app.message_bus import MessageBusKeys
    events_key = MessageBusKeys.session_events(session_id)
    event_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _session_event_subscriber() -> None:
        """后台订阅 Redis Pub/Sub，将 session 事件转发到 WebSocket 事件队列"""
        if not message_bus:
            return
        try:
            async for evt in message_bus.subscribe(events_key):
                await event_queue.put(
                    {k: v for k, v in evt.items() if k != "_entry_id"},
                )
        except asyncio.CancelledError:
            pass
        finally:
            await event_queue.put(None)

    subscriber_task = asyncio.create_task(_session_event_subscriber())

    try:
        while True:
            # 同时等待用户消息和 session 事件（观察者模式核心）
            ws_task = asyncio.create_task(ws.receive_json())
            evt_task = asyncio.create_task(event_queue.get())

            done, pending = await asyncio.wait(
                [ws_task, evt_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # 取消未完成的等待
            for p in pending:
                p.cancel()

            for task in done:
                data = task.result()

                if data is None:
                    # event_queue 返回 None，连接关闭
                    continue

                # 判断是 session 事件还是 WebSocket 消息
                if task is evt_task:
                    # 从 Redis 收到的 session 事件（观察者模式）
                    evt = data
                    evt_type = evt.get("type", "")
                    # 如果是其他设备发起的 run_start，通知前端
                    if evt_type == "run_start" and evt.get("device_id") != device_id:
                        await _send_json(ws, "other_device_active", {
                            "device_id": evt.get("device_id"),
                            "message": "其他设备正在生成回复",
                        })
                    # 转发所有事件给前端
                    await _send_json(ws, evt_type, evt)
                    continue

                # WebSocket 消息
                msg_type = data.get("type", "")

                if msg_type == "chat":
                    # 防止并发生成
                    if current_task and not current_task.done():
                        await _send_json(ws, "error", {
                            "message": "上一轮对话尚未结束，请等待完成或发送 cancel",
                        })
                        continue

                    # 多端并发：检查会话是否已被其他设备占用
                    if status_tracker:
                        status = await status_tracker.get_status(session_id)
                        if status.get("state") == SessionState.GENERATING:
                            await _send_json(ws, "busy", {
                                "owner": status.get("owner", "unknown"),
                                "started_at": status.get("started_at"),
                                "message": "其他设备正在回复中，请等待或发送 interrupt",
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
                        _handle_chat(
                            ws, session_mgr, db, user_id, session_id, message,
                            cancelled, device_id=device_id,
                            status_tracker=status_tracker,
                        )
                    )

                elif msg_type == "cancel":
                    # 取消当前连接的任务或上一连接遗留的后台任务
                    target = current_task
                    if (not target or target.done()) and bg_key in _BACKGROUND_TASKS:
                        target = _BACKGROUND_TASKS[bg_key]
                    if target and not target.done():
                        cancelled.set()
                        target.cancel()
                        try:
                            await target
                        except (asyncio.CancelledError, Exception):
                            pass
                        await _send_json(ws, "reply_end", {
                            "finished_reason": "cancelled",
                            "finished": True,
                        })
                        logger.info("WebSocket 取消生成: user={} session={}", user_id, session_id)
                    current_task = None

                elif msg_type == "interrupt":
                    # 多端中断：任意设备可发送 interrupt 请求
                    if status_tracker:
                        success = await status_tracker.request_cancel(
                            session_id, device_id,
                        )
                        if success:
                            await _send_json(ws, "interrupt_requested", {
                                "status": "cancel_requested",
                            })
                        else:
                            await _send_json(ws, "interrupt_requested", {
                                "status": "no_active_generation",
                            })
                    else:
                        await _send_json(ws, "error", {
                            "message": "状态跟踪器未配置",
                        })

                elif msg_type == "ping":
                    await _send_json(ws, "pong")

                elif msg_type == "auth":
                    # 认证帧：未启用认证时到达此处说明是客户端冗余发送，忽略即可。
                    continue

                else:
                    await _send_json(ws, "error", {
                        "message": f"未知消息类型: {msg_type}",
                    })

    except WebSocketDisconnect:
        logger.info("WebSocket 断开: user={} session={}", user_id, session_id)
    except Exception:
        logger.exception("WebSocket 异常: user={} session={}", user_id, session_id)
    finally:
        # 断连时不清除 current_task，让它在 _BACKGROUND_TASKS 中继续运行。
        # 任务完成后会自动注销并保存回复，前端重连时可通过 pending_reply 拉取。
        # 仅在服务关闭时由 shutdown_persist_tasks 统一等待。
        subscriber_task.cancel()
        try:
            await subscriber_task
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
    device_id: str = "unknown",
    status_tracker: SessionStatusTracker | None = None,
) -> None:
    """处理单轮对话的流式回复（支持多端并发状态广播）

    断连保护：WebSocket 断开后不停止生成，继续累积回复直到 AI 完成或触发停止条件。
    前端重连同一 session_id 时可通过 pending_reply 获取断连期间生成的回复。
    """
    # 先初始化缓冲区，确保 finally 中始终可用（即使 turn 提前异常/取消）
    pending_tool_calls: dict[str, dict] = {}
    pending_tool_results: dict[str, str] = {}
    tool_call_records: dict[str, dict] = {}

    full_reply = ""
    full_thinking = ""
    ws_alive = True                          # 连接存活标志
    reply_status = "partial"                 # 默认状态，finally 中根据实际情况更新

    # 注册到后台任务表（断连后任务继续运行时，重连可通过此表查询）
    bg_key = (user_id, session_id)
    _BACKGROUND_TASKS[bg_key] = asyncio.current_task()

    bus = getattr(ws.app.state, "message_bus", None)
    lock_ctx = (
        acquire_session_lock(bus, user_id, session_id)
        if bus is not None else contextlib.nullasynccontext()
    )

    # 后台生成保护：总时长上限
    reply_deadline = asyncio.get_event_loop().time() + REPLY_MAX_DURATION

    try:
        async with lock_ctx:
            try:
                # 标记会话为生成中（多端并发状态广播）
                if status_tracker:
                    await status_tracker.set_generating(
                        session_id, device_id, message,
                    )

                agent = await session_mgr.get_or_create(user_id, session_id)
                # 注入 OTel 追踪上下文（供 TracingContextMiddleware 使用）
                agent.__tracing_context__ = {
                    "agentscope.user.id": user_id,
                    "agentscope.device.id": device_id,
                }
                # 多实例场景：强制刷新状态
                await session_mgr.refresh_state(user_id, session_id)

                user_msg = UserMsg(name="user", content=message)

                # LLM 超时保护（空闲超时：两事件之间最长等待）
                llm_timeout = getattr(
                    getattr(ws.app.state.config, "llm", None),
                    "timeout", 120,
                )
                event_count = 0

                async for event in _stream_with_timeout(
                    agent.reply_stream(user_msg), llm_timeout,
                ):
                    # === 停止条件检查（无论 ws_alive 都执行） ===
                    now = asyncio.get_event_loop().time()
                    if now > reply_deadline:
                        logger.warning(
                            "回复总时长超限({}s)，强制停止: user={} session={}",
                            REPLY_MAX_DURATION, user_id, session_id,
                        )
                        break
                    if len(full_reply) > REPLY_MAX_CHARS:
                        logger.warning(
                            "回复字符数超限({})，强制停止: user={} session={}",
                            REPLY_MAX_CHARS, user_id, session_id,
                        )
                        break

                    # 检查取消
                    if cancelled.is_set():
                        break

                    # 每 5 个事件检查一次 cancel 信号（多端中断）
                    event_count += 1
                    if status_tracker and event_count % 5 == 0:
                        if await status_tracker.should_cancel(session_id):
                            if ws_alive:
                                await _send_json(ws, "interrupted", {
                                    "reason": "cancel_requested",
                                    "session_id": session_id,
                                })
                            break

                    if not hasattr(event, "type"):
                        if isinstance(event, Msg):
                            if ws_alive:
                                if not await _send_json(ws, "reply_end", {
                                    "text": event.get_text_content(),
                                    "finished": True,
                                }):
                                    ws_alive = False
                        continue

                    # === 累积数据（无论 ws_alive 都执行） ===
                    match event.type:
                        case EventType.TEXT_BLOCK_DELTA:
                            full_reply += event.delta
                        case EventType.THINKING_BLOCK_DELTA:
                            full_thinking += event.delta
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
                            if tool_call_id in tool_call_records:
                                tool_call_records[tool_call_id]["result"] = result_text
                                tool_call_records[tool_call_id]["state"] = state
                        case EventType.REPLY_END:
                            pass  # 发送在下方
                        case _:
                            pass

                    # === 发送（仅连接存活时） ===
                    if ws_alive:
                        send_ok = True
                        match event.type:
                            case EventType.TEXT_BLOCK_DELTA:
                                send_ok = await _send_json(ws, "text_delta", {"delta": event.delta})
                            case EventType.THINKING_BLOCK_DELTA:
                                send_ok = await _send_json(ws, "thinking_delta", {"delta": event.delta})
                            case EventType.TOOL_CALL_END:
                                if tool_call_id in tool_call_records:
                                    rec = tool_call_records[tool_call_id]
                                    send_ok = await _send_json(ws, "tool_call", {
                                        "tool_name": rec["tool_name"],
                                        "tool_call_id": rec["tool_call_id"],
                                        "tool_args": rec.get("tool_args"),
                                    })
                            case EventType.TOOL_RESULT_END:
                                send_ok = await _send_json(ws, "tool_result", {
                                    "tool_call_id": tool_call_id,
                                    "state": state,
                                    "result": result_text,
                                })
                            case EventType.REPLY_END:
                                send_ok = await _send_json(ws, "reply_end", {
                                    "finished_reason": str(getattr(event, "finished_reason", "")),
                                    "finished": True,
                                })
                        if not send_ok:
                            ws_alive = False
                            logger.info("WebSocket 发送失败，切换为静默模式: user={} session={}", user_id, session_id)

                # 循环正常结束 = AI 生成完成
                reply_status = "completed"

            finally:
                # 标记会话为空闲（多端并发状态广播）
                if status_tracker:
                    await status_tracker.set_idle(session_id)

                # Redis AgentState 必须在锁内同步落库，避免下一轮 refresh_state
                # 读取到过期状态导致本轮对话丢失（state-loss race）。
                await session_mgr.save(user_id, session_id)

    except asyncio.TimeoutError:
        reply_status = "timeout"
        logger.error("LLM 调用超时: user={} session={}", user_id, session_id)
        if ws_alive:
            await _send_json(ws, "error", {
                "message": f"LLM 调用超时（{llm_timeout}s），请重试",
            })
    except asyncio.CancelledError:
        reply_status = "cancelled"
        logger.info("对话生成被取消: user={} session={}", user_id, session_id)
        raise
    except GeneratorExit:
        reply_status = "partial"
        logger.debug("WebSocket 生成器关闭: user={} session={}", user_id, session_id)
    except Exception as e:
        reply_status = "error"
        logger.exception("WebSocket 对话异常: user={} session={}", user_id, session_id)
        if ws_alive:
            await _send_json(ws, "error", {"message": str(e)})
    finally:
        # 从后台任务表注销
        _BACKGROUND_TASKS.pop(bg_key, None)

        # 断连但生成完成时，将回复写入 session 元数据（供重连拉取）
        if not ws_alive and full_reply and reply_status == "completed":
            try:
                await session_mgr.save_session_reply(
                    user_id, session_id, full_reply, reply_status,
                )
            except Exception:
                logger.debug("写入 pending_reply 元数据失败: user={} session={}", user_id, session_id)

        # PG 历史双写为 append-only 且风险较低，保留为 fire-and-forget；
        # Redis AgentState 已在上方锁内 awaited 落库（state-loss race 修复）。
        task = asyncio.create_task(_persist_conversation(
            session_mgr, db, user_id, session_id, message, full_reply,
            thinking=full_thinking or None,
            tool_calls=list(tool_call_records.values()) or None,
        ))
        _PERSIST_TASKS.add(task)
        task.add_done_callback(_PERSIST_TASKS.discard)
