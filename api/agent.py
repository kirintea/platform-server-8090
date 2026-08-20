# -*- coding: utf-8 -*-
# 没想好，不使用

"""Agent CRUD API 路由（暂时未使用，路由未注册）

端点：
- GET    /agent             — 列出所有 Agent
- POST   /agent             — 创建 Agent
- GET    /agent/{agent_id}  — 获取单个 Agent
- PATCH  /agent/{agent_id}  — 更新 Agent
- DELETE /agent/{agent_id}  — 删除 Agent
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.storage_models import AgentData, AgentRecord
from core.validators import coerce_id

from loguru import logger

router = APIRouter(prefix="/agent", tags=["agent"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateAgentRequest(BaseModel):
    """创建 Agent 请求"""
    name: str = Field(description="Agent 名称")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="系统提示词",
    )
    context_config: dict = Field(
        default_factory=lambda: {"trigger_ratio": 0.8, "reserve_ratio": 0.1},
        description="上下文压缩配置",
    )
    react_config: dict = Field(
        default_factory=lambda: {"max_iters": 50, "stop_on_reject": False},
        description="ReAct 配置",
    )


class UpdateAgentRequest(BaseModel):
    """更新 Agent 请求（PATCH 语义，只更新提供的字段）"""
    name: str | None = Field(default=None, description="Agent 名称")
    system_prompt: str | None = Field(default=None, description="系统提示词")
    context_config: dict | None = Field(default=None, description="上下文压缩配置")
    react_config: dict | None = Field(default=None, description="ReAct 配置")


class AgentResponse(BaseModel):
    """Agent 响应"""
    id: str
    user_id: str
    source: str
    name: str
    system_prompt: str
    context_config: dict
    react_config: dict
    created_at: str
    updated_at: str


class ListAgentsResponse(BaseModel):
    """Agent 列表响应"""
    agents: list[AgentResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _agent_to_response(record: AgentRecord) -> AgentResponse:
    """将 AgentRecord 转换为响应格式"""
    return AgentResponse(
        id=record.id,
        user_id=record.user_id,
        source=record.source,
        name=record.data.name,
        system_prompt=record.data.system_prompt,
        context_config=record.data.context_config,
        react_config=record.data.react_config,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListAgentsResponse)
async def list_agents(request: Request, user_id: str = "anonymous"):
    """列出用户的所有 Agent"""
    user_id = coerce_id(user_id)
    storage = request.app.state.storage
    agents = await storage.list_agents(user_id)
    return ListAgentsResponse(
        agents=[_agent_to_response(a) for a in agents],
        total=len(agents),
    )


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    request: Request,
    body: CreateAgentRequest,
    user_id: str = "anonymous",
):
    """创建新 Agent"""
    user_id = coerce_id(user_id)
    storage = request.app.state.storage

    record = AgentRecord(
        user_id=user_id,
        data=AgentData(
            name=body.name,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
        ),
    )

    agent_id = await storage.upsert_agent(user_id, record)
    created = await storage.get_agent(user_id, agent_id)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent 创建失败",
        )
    return _agent_to_response(created)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    request: Request,
    agent_id: str,
    user_id: str = "anonymous",
):
    """获取单个 Agent"""
    user_id = coerce_id(user_id)
    storage = request.app.state.storage
    record = await storage.get_agent(user_id, agent_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' 不存在",
        )
    return _agent_to_response(record)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    request: Request,
    agent_id: str,
    body: UpdateAgentRequest,
    user_id: str = "anonymous",
):
    """更新 Agent（PATCH 语义）"""
    user_id = coerce_id(user_id)
    storage = request.app.state.storage
    existing = await storage.get_agent(user_id, agent_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' 不存在",
        )

    # 合并更新
    data = existing.data
    if body.name is not None:
        data = data.model_copy(update={"name": body.name})
    if body.system_prompt is not None:
        data = data.model_copy(update={"system_prompt": body.system_prompt})
    if body.context_config is not None:
        data = data.model_copy(update={"context_config": body.context_config})
    if body.react_config is not None:
        data = data.model_copy(update={"react_config": body.react_config})

    updated = existing.model_copy(update={"data": data})
    await storage.upsert_agent(user_id, updated)

    result = await storage.get_agent(user_id, agent_id)
    return _agent_to_response(result)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    request: Request,
    agent_id: str,
    user_id: str = "anonymous",
):
    """删除 Agent"""
    user_id = coerce_id(user_id)
    storage = request.app.state.storage
    deleted = await storage.delete_agent(user_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' 不存在",
        )
