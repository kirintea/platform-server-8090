# -*- coding: utf-8 -*-

"""Schedule 定时任务 API 路由（暂时未使用，路由未注册）

端点：
- GET    /schedule                — 列出定时任务
- POST   /schedule                — 创建定时任务
- PATCH  /schedule/{schedule_id}  — 更新定时任务
- DELETE /schedule/{schedule_id}  — 删除定时任务
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.storage_models import ScheduleRecord

from loguru import logger

router = APIRouter(prefix="/schedule", tags=["schedule"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateScheduleRequest(BaseModel):
    """创建定时任务请求"""
    agent_id: str = Field(description="关联 Agent ID")
    name: str = Field(description="任务名称")
    cron_expr: str = Field(description="Cron 表达式 (5 位: 分 时 日 月 周)")
    prompt: str = Field(description="触发时发送的提示词")
    session_id: str | None = Field(default=None, description="关联会话 ID（不传则自动创建）")


class UpdateScheduleRequest(BaseModel):
    """更新定时任务请求"""
    name: str | None = Field(default=None, description="任务名称")
    cron_expr: str | None = Field(default=None, description="Cron 表达式")
    prompt: str | None = Field(default=None, description="提示词")
    enabled: bool | None = Field(default=None, description="启用/禁用")


class ScheduleResponse(BaseModel):
    """定时任务响应"""
    id: str
    user_id: str
    agent_id: str
    session_id: str | None
    name: str
    cron_expr: str
    prompt: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    created_at: str
    updated_at: str


class ListSchedulesResponse(BaseModel):
    """定时任务列表响应"""
    schedules: list[ScheduleResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _schedule_to_response(record: ScheduleRecord) -> ScheduleResponse:
    return ScheduleResponse(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
        name=record.name,
        cron_expr=record.cron_expr,
        prompt=record.prompt,
        enabled=record.enabled,
        last_run_at=record.last_run_at.isoformat() if record.last_run_at else None,
        next_run_at=record.next_run_at.isoformat() if record.next_run_at else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListSchedulesResponse)
async def list_schedules(request: Request, user_id: str = "anonymous"):
    """列出定时任务"""
    storage = request.app.state.storage
    schedules = await storage.list_schedules(user_id)
    return ListSchedulesResponse(
        schedules=[_schedule_to_response(s) for s in schedules],
        total=len(schedules),
    )


@router.post(
    "",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    request: Request,
    body: CreateScheduleRequest,
    user_id: str = "anonymous",
):
    """创建定时任务"""
    storage = request.app.state.storage

    # 验证 Agent 存在
    agent = await storage.get_agent(user_id, body.agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_id}' 不存在",
        )

    record = ScheduleRecord(
        user_id=user_id,
        agent_id=body.agent_id,
        session_id=body.session_id,
        name=body.name,
        cron_expr=body.cron_expr,
        prompt=body.prompt,
    )

    schedule_id = await storage.upsert_schedule(user_id, record)
    created = await storage.get_schedule(user_id, schedule_id)
    return _schedule_to_response(created)


@router.patch("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    request: Request,
    schedule_id: str,
    body: UpdateScheduleRequest,
    user_id: str = "anonymous",
):
    """更新定时任务"""
    storage = request.app.state.storage
    existing = await storage.get_schedule(user_id, schedule_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' 不存在",
        )

    if body.name is not None:
        existing.name = body.name
    if body.cron_expr is not None:
        existing.cron_expr = body.cron_expr
    if body.prompt is not None:
        existing.prompt = body.prompt
    if body.enabled is not None:
        existing.enabled = body.enabled

    await storage.upsert_schedule(user_id, existing)
    updated = await storage.get_schedule(user_id, schedule_id)
    return _schedule_to_response(updated)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    request: Request,
    schedule_id: str,
    user_id: str = "anonymous",
):
    """删除定时任务"""
    storage = request.app.state.storage
    deleted = await storage.delete_schedule(user_id, schedule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' 不存在",
        )
