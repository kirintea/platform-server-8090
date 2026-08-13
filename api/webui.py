# -*- coding: utf-8 -*-

"""WebUI 兼容层 — 适配 AgentScope Web UI 前端的 API 格式

WebUI 前端期望的 API 格式与我们现有后端有差异：
- 响应格式不同（Agent 需要 data 嵌套，MCP/Skill 返回数组等）
- 需要额外端点（health、session create/update/interrupt 等）
- user_id 通过 X-User-ID 请求头传递

本模块作为适配层，桥接 WebUI 前端和现有后端存储/服务。
所有端点挂载在 /webui 前缀下，WebUI 前端的 server_url 设置为
'http://localhost:8090/webui' 即可。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.storage_models import (
    AgentData,
    AgentRecord,
    MCPRecord,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    SkillRecord,
    _generate_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webui", tags=["webui-compat"])


# ============================================================
# 辅助函数
# ============================================================

def _get_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """从 X-User-ID 请求头获取用户 ID"""
    return x_user_id or "anonymous"


def _agent_to_view(record: AgentRecord) -> dict:
    """将 AgentRecord 转换为 WebUI 期望的 AgentView 格式"""
    return {
        "id": record.id,
        "user_id": record.user_id,
        "source": record.source,
        "data": record.data.model_dump(),
        "editable": True,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _mcp_to_view(record: MCPRecord) -> dict:
    """将 MCPRecord 转换为 WebUI 期望的 MCPView 格式"""
    return {
        "id": record.id,
        "name": record.name,
        "is_stateful": record.transport == "stdio",
        "enabled": record.enabled,
        "display_name": record.display_name,
        "description": record.description,
        "tags": record.tags,
        "author": record.author,
        "icon_url": record.icon_url,
        "url": record.url,
        "hub_id": record.hub_id,
        "card_id": record.card_id,
        "version": record.version,
    }


def _skill_to_view(record: SkillRecord) -> dict:
    """将 SkillRecord 转换为 WebUI 期望的 SkillView 格式"""
    return {
        "id": record.id,
        "name": record.name,
        "enabled": record.enabled,
        "display_name": record.display_name,
        "description": record.description,
        "tags": record.tags,
        "author": record.author,
        "icon_url": record.icon_url,
        "url": None,
        "hub_id": record.hub_id,
        "card_id": record.card_id,
        "version": record.version,
    }


def _skill_to_record_view(record: SkillRecord) -> dict:
    """将 SkillRecord 转换为 WebUI 期望的 SkillRecord 格式（含 markdown）"""
    view = _skill_to_view(record)
    view["markdown"] = record.markdown
    return view


def _session_to_record(session: SessionRecord) -> dict:
    """将 SessionRecord 转换为 WebUI 期望的 SessionRecord 格式"""
    return {
        "id": session.id,
        "user_id": session.user_id,
        "agent_id": session.agent_id,
        "source": session.source.value,
        "source_schedule_id": None,
        "source_channel_id": None,
        "team_id": session.team_id,
        "config": {
            "name": session.config.name,
            "chat_model_config": session.config.chat_model_config,
            "fallback_chat_model_config": None,
            "tts_model_config": None,
            "knowledge_config": None,
            "workspace_id": session.config.workspace_id,
            "cwd": session.config.cwd,
        },
        "state": {},
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


def _session_to_view(session: SessionRecord, is_running: bool = False) -> dict:
    """将 SessionRecord 转换为 WebUI 期望的 SessionView 格式"""
    status_val = "running" if is_running else "idle"
    return {
        "session": _session_to_record(session),
        "is_running": is_running,
        "status": status_val,
        "team": None,
    }


# ============================================================
# Health
# ============================================================

@router.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "components": {},
    }


# ============================================================
# Agent 端点
# ============================================================

@router.get("/agent/schema/v2")
async def agent_schema_v2():
    """Agent JSON Schema — WebUI 用它来渲染创建表单"""
    return {
        "schema": {
            "title": "AgentData",
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent 名称",
                },
                "system_prompt": {
                    "type": "string",
                    "format": "textarea",
                    "description": "系统提示词",
                    "default": "You're a helpful assistant.",
                },
                "context_config": {
                    "type": "object",
                    "title": "ContextConfig",
                    "description": "上下文压缩配置",
                    "properties": {
                        "trigger_ratio": {"type": "number", "default": 0.8, "minimum": 0, "maximum": 1},
                        "reserve_ratio": {"type": "number", "default": 0.1, "minimum": 0, "maximum": 1},
                        "tool_result_limit": {"type": "integer", "default": 50000},
                    },
                },
                "react_config": {
                    "type": "object",
                    "title": "ReActConfig",
                    "description": "ReAct 推理配置",
                    "properties": {
                        "max_iters": {"type": "integer", "default": 50, "minimum": 1},
                        "stop_on_reject": {"type": "boolean", "default": False},
                    },
                },
            },
            "required": ["name"],
        }
    }


@router.get("/agent/")
async def list_agents(
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """列出用户的所有 Agent"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    agents = await storage.list_agents(user_id)
    return {
        "agents": [_agent_to_view(a) for a in agents],
        "total": len(agents),
    }


@router.post("/agent/", status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """创建 Agent"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage

    data = AgentData(
        name=body.get("name", "New Agent"),
        system_prompt=body.get("system_prompt", "You're a helpful assistant."),
        context_config=body.get("context_config", {"trigger_ratio": 0.8, "reserve_ratio": 0.1}),
        react_config=body.get("react_config", {"max_iters": 50, "stop_on_reject": False}),
    )

    record = AgentRecord(user_id=user_id, data=data)
    agent_id = await storage.upsert_agent(user_id, record)
    created = await storage.get_agent(user_id, agent_id)
    return {"agent_id": created.id}


@router.patch("/agent/{agent_id}")
async def update_agent(
    agent_id: str,
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """更新 Agent"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    existing = await storage.get_agent(user_id, agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    data = existing.data
    if "name" in body:
        data = data.model_copy(update={"name": body["name"]})
    if "system_prompt" in body:
        data = data.model_copy(update={"system_prompt": body["system_prompt"]})
    if "context_config" in body:
        data = data.model_copy(update={"context_config": body["context_config"]})
    if "react_config" in body:
        data = data.model_copy(update={"react_config": body["react_config"]})

    updated = existing.model_copy(update={"data": data})
    await storage.upsert_agent(user_id, updated)
    result = await storage.get_agent(user_id, agent_id)
    return _agent_to_view(result)


@router.delete("/agent/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """删除 Agent"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    deleted = await storage.delete_agent(user_id, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")


# ============================================================
# MCP 端点
# ============================================================

@router.get("/mcp")
async def list_mcps(
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """列出已安装 MCP — 返回数组"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    mcps = await storage.list_mcps(user_id)
    return [_mcp_to_view(m) for m in mcps]


@router.patch("/mcp/{mcp_id}")
async def update_mcp(
    mcp_id: str,
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """更新 MCP"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    existing = await storage.get_mcp(user_id, mcp_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"MCP '{mcp_id}' not found")

    if "name" in body:
        existing.name = body["name"]
    if "enabled" in body:
        existing.enabled = body["enabled"]
    if "display_name" in body:
        existing.display_name = body["display_name"]
    if "description" in body:
        existing.description = body["description"]

    await storage.upsert_mcp(user_id, existing)
    updated = await storage.get_mcp(user_id, mcp_id)
    return _mcp_to_view(updated)


@router.delete("/mcp/{mcp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp(
    mcp_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """删除 MCP"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    deleted = await storage.delete_mcp(user_id, mcp_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"MCP '{mcp_id}' not found")


# ============================================================
# Skill 端点
# ============================================================

@router.get("/skill")
async def list_skills(
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """列出已安装 Skill — 返回数组"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    skills = await storage.list_skills(user_id)
    return [_skill_to_view(s) for s in skills]


@router.get("/skill/{skill_id}")
async def get_skill(
    skill_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """获取单个 Skill（含 markdown）"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    record = await storage.get_skill(user_id, skill_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return _skill_to_record_view(record)


@router.delete("/skill/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """删除 Skill"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    deleted = await storage.delete_skill(user_id, skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")


# ============================================================
# Session 端点
# ============================================================

@router.get("/sessions/")
async def list_sessions(
    request: Request,
    agent_id: str = Query(default=""),
    x_user_id: str | None = Header(default=None),
):
    """列出会话"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    sessions = await storage.list_sessions(user_id, agent_id or None)
    return {
        "sessions": [_session_to_view(s) for s in sessions],
        "total": len(sessions),
    }


@router.post("/sessions/", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """创建会话"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage

    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    # 验证 agent 存在
    agent = await storage.get_agent(user_id, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    config = SessionConfig(
        name=body.get("name", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        workspace_id=body.get("workspace_id", _generate_id()),
        chat_model_config=body.get("chat_model_config"),
    )

    record = await storage.upsert_session(
        user_id=user_id,
        agent_id=agent_id,
        config=config,
        source=SessionSource.USER,
    )

    return {"session_id": record.id}


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """更新会话配置"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage

    session = await storage.get_session(user_id, "", session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # 更新 config 字段
    config = session.config
    if "name" in body:
        config = config.model_copy(update={"name": body["name"]})
    if "chat_model_config" in body:
        config = config.model_copy(update={"chat_model_config": body["chat_model_config"]})
    if "cwd" in body:
        config = config.model_copy(update={"cwd": body["cwd"]})

    await storage.upsert_session(
        user_id=user_id,
        agent_id=session.agent_id,
        config=config,
        state_json=session.state_json,
        session_id=session_id,
        source=session.source,
    )

    updated = await storage.get_session(user_id, "", session_id)
    return _session_to_record(updated)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """删除会话"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    deleted = await storage.delete_session(user_id, "", session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """中断会话"""
    user_id = x_user_id or "anonymous"
    # 通过消息总线发布取消信号
    message_bus = request.app.state.message_bus
    cancel_key = f"agentscope:session:{session_id}:cancel"
    await message_bus.publish(cancel_key, {"cancel": True})
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
    agent_id: str = Query(default=""),
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    x_user_id: str | None = Header(default=None),
):
    """获取会话消息历史"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage

    messages, has_more = await storage.list_messages(
        user_id, session_id, limit=limit, before=before,
    )

    # 转换为 WebUI 期望的 Msg 格式
    msg_list = []
    for m in messages:
        msg_list.append({
            "id": m.msg_id,
            "role": m.role,
            "content": [{"type": "text", "text": m.content}],
            "name": m.role,
            "metadata": m.metadata,
        })

    return {
        "messages": msg_list,
        "is_running": False,
        "has_more": has_more,
    }


@router.get("/sessions/{session_id}/stream")
async def stream_session_events(
    session_id: str,
    request: Request,
    agent_id: str = Query(default=""),
):
    """SSE 事件流 — 代理到现有 SSE 端点"""
    from api.chat import session_event_stream
    return await session_event_stream(request, session_id)


# ============================================================
# Chat 端点
# ============================================================

@router.post("/chat/")
async def trigger_chat(
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """触发聊天 — 代理到现有 fire-and-forget 端点"""
    user_id = x_user_id or "anonymous"
    chat_service = request.app.state.chat_service
    session_mgr = request.app.state.session_manager
    storage = request.app.state.storage

    session_id = body.get("session_id")
    agent_id = body.get("agent_id", "")
    input_data = body.get("input")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    # 提取消息文本
    message = ""
    if input_data:
        if isinstance(input_data, dict):
            content = input_data.get("content", "")
            if isinstance(content, str):
                message = content
            elif isinstance(content, list):
                # 提取文本块
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                message = "\n".join(parts)
        elif isinstance(input_data, str):
            message = input_data

    if not message:
        return {"status": "ok", "session_id": session_id}

    # 获取 session 信息以得到 agent_id
    session = await storage.get_session(user_id, "", session_id)
    if session:
        agent_id = session.agent_id

    # 获取 agent 配置
    agent_config = {}
    if agent_id:
        agent = await storage.get_agent(user_id, agent_id)
        if agent:
            agent_config = {
                "system_prompt": agent.data.system_prompt,
                "context_config": agent.data.context_config,
                "react_config": agent.data.react_config,
            }

    # 获取或创建会话
    entry = await session_mgr.get_or_create(session_id, user_id)

    # 异步触发聊天
    import asyncio
    asyncio.create_task(
        chat_service.run(
            session_id=session_id,
            user_id=user_id,
            message=message,
            agent_config=agent_config,
        )
    )

    return {"status": "ok", "session_id": session_id}


# ============================================================
# Workspace 端点
# ============================================================

@router.get("/workspace/directories")
async def list_workspace_directories(
    request: Request,
    agent_id: str = Query(default=""),
    session_id: str = Query(default=""),
    path: str = Query(default=""),
    x_user_id: str | None = Header(default=None),
):
    """列出工作区目录"""
    user_id = x_user_id or "anonymous"
    workspace_mgr = request.app.state.workspace_manager
    workspace = await workspace_mgr.get_workspace(user_id, session_id)

    files = workspace.list_files(path or ".")
    entries = []
    for f in files:
        entries.append({
            "name": f["name"],
            "is_dir": f["is_dir"],
            "size_bytes": f["size"] if not f["is_dir"] else None,
            "updated_at": f["modified"],
        })

    return {
        "path": workspace.workdir,
        "entries": entries,
    }


@router.get("/workspace/status")
async def workspace_status(
    request: Request,
    agent_id: str = Query(default=""),
    session_id: str = Query(default=""),
    x_user_id: str | None = Header(default=None),
):
    """获取工作区状态"""
    user_id = x_user_id or "anonymous"
    workspace_mgr = request.app.state.workspace_manager
    workspace = await workspace_mgr.get_workspace(user_id, session_id)

    return {
        "workdir": workspace.workdir,
        "cwd": workspace.workdir,
        "git": None,
    }


# ============================================================
# Schedule 端点
# ============================================================

@router.get("/schedule/")
async def list_schedules(
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """列出定时任务"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    schedules = await storage.list_schedules(user_id)

    result = []
    for s in schedules:
        result.append({
            "id": s.id,
            "user_id": s.user_id,
            "agent_id": s.agent_id,
            "data": {
                "name": s.name,
                "description": "",
                "enabled": s.enabled,
                "timezone": "UTC",
                "cron_expression": s.cron_expr,
                "started_at": s.created_at.isoformat(),
                "ended_at": None,
                "chat_model_config": {},
                "stateful": False,
                "permission_mode": "default",
                "source": s.source.value.upper(),
                "source_session_id": s.session_id or "",
            },
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        })

    return {"schedules": result, "total": len(result)}


@router.post("/schedule/", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """创建定时任务"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage

    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    agent = await storage.get_agent(user_id, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    name = body.get("name", "")
    cron_expr = body.get("cron_expression", "")
    prompt = body.get("description", "")

    record = ScheduleRecord(
        user_id=user_id,
        agent_id=agent_id,
        name=name,
        cron_expr=cron_expr,
        prompt=prompt,
        enabled=body.get("enabled", True),
    )

    schedule_id = await storage.upsert_schedule(user_id, record)
    return {"schedule_id": schedule_id}


@router.patch("/schedule/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    request: Request,
    body: dict[str, Any],
    x_user_id: str | None = Header(default=None),
):
    """更新定时任务"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    existing = await storage.get_schedule(user_id, schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    if "name" in body:
        existing.name = body["name"]
    if "cron_expression" in body:
        existing.cron_expr = body["cron_expression"]
    if "description" in body:
        existing.prompt = body["description"]
    if "enabled" in body:
        existing.enabled = body["enabled"]

    await storage.upsert_schedule(user_id, existing)
    updated = await storage.get_schedule(user_id, schedule_id)

    return {
        "id": updated.id,
        "user_id": updated.user_id,
        "agent_id": updated.agent_id,
        "data": {
            "name": updated.name,
            "description": "",
            "enabled": updated.enabled,
            "timezone": "UTC",
            "cron_expression": updated.cron_expr,
            "started_at": updated.created_at.isoformat(),
            "ended_at": None,
            "chat_model_config": {},
            "stateful": False,
            "permission_mode": "default",
            "source": updated.source.value.upper(),
            "source_session_id": updated.session_id or "",
        },
        "created_at": updated.created_at.isoformat(),
        "updated_at": updated.updated_at.isoformat(),
    }


@router.delete("/schedule/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """删除定时任务"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    deleted = await storage.delete_schedule(user_id, schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")


@router.get("/schedule/{schedule_id}/sessions")
async def list_schedule_sessions(
    schedule_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
):
    """列出定时任务关联的会话"""
    user_id = x_user_id or "anonymous"
    storage = request.app.state.storage
    schedule = await storage.get_schedule(user_id, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    sessions = []
    if schedule.session_id:
        session = await storage.get_session(user_id, "", schedule.session_id)
        if session:
            sessions.append(_session_to_record(session))

    return {"sessions": sessions, "total": len(sessions)}
