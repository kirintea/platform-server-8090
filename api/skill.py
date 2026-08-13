# -*- coding: utf-8 -*-

"""Skill 管理 API 路由

端点：
- GET    /skill               — 列出已安装 Skill
- POST   /skill               — 添加 Skill
- GET    /skill/{skill_id}    — 获取单个 Skill
- DELETE /skill/{skill_id}    — 删除 Skill
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.storage_models import SkillRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skill", tags=["skill"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateSkillRequest(BaseModel):
    """添加 Skill 请求"""
    name: str = Field(description="Skill 名称（唯一）")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")
    markdown: str = Field(default="", description="SKILL.md 内容")
    tags: list[str] = Field(default_factory=list, description="标签")
    author: str | None = Field(default=None, description="作者")


class SkillResponse(BaseModel):
    """Skill 响应"""
    id: str
    user_id: str
    name: str
    display_name: str | None
    description: str
    markdown: str
    tags: list[str]
    author: str | None
    enabled: bool
    created_at: str
    updated_at: str


class ListSkillsResponse(BaseModel):
    """Skill 列表响应"""
    skills: list[SkillResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _skill_to_response(record: SkillRecord) -> SkillResponse:
    return SkillResponse(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        display_name=record.display_name,
        description=record.description,
        markdown=record.markdown,
        tags=record.tags,
        author=record.author,
        enabled=record.enabled,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListSkillsResponse)
async def list_skills(request: Request, user_id: str = "anonymous"):
    """列出已安装 Skill"""
    storage = request.app.state.storage
    skills = await storage.list_skills(user_id)
    return ListSkillsResponse(
        skills=[_skill_to_response(s) for s in skills],
        total=len(skills),
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    request: Request,
    body: CreateSkillRequest,
    user_id: str = "anonymous",
):
    """添加 Skill"""
    storage = request.app.state.storage

    # 检查名称唯一性
    existing = await storage.get_skill_by_name(user_id, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{body.name}' 已存在",
        )

    record = SkillRecord(
        user_id=user_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        markdown=body.markdown,
        tags=body.tags,
        author=body.author,
    )

    skill_id = await storage.upsert_skill(user_id, record)
    created = await storage.get_skill(user_id, skill_id)
    return _skill_to_response(created)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    request: Request,
    skill_id: str,
    user_id: str = "anonymous",
):
    """获取单个 Skill"""
    storage = request.app.state.storage
    record = await storage.get_skill(user_id, skill_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )
    return _skill_to_response(record)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    request: Request,
    skill_id: str,
    user_id: str = "anonymous",
):
    """删除 Skill"""
    storage = request.app.state.storage
    deleted = await storage.delete_skill(user_id, skill_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )
