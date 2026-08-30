# -*- coding: utf-8 -*-

"""MCP 管理 API 路由

端点：
- GET    /mcp             — 列出已安装 MCP
- POST   /mcp             — 添加 MCP
- PATCH  /mcp/{mcp_id}    — 更新 MCP（启用/禁用、改名）
- DELETE /mcp/{mcp_id}    — 删除 MCP
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.storage_models import MCPRecord
from core.validators import coerce_id, is_auth_enabled, require_user_id

from loguru import logger

router = APIRouter(prefix="/mcp", tags=["mcp"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateMCPRequest(BaseModel):
    """添加 MCP 请求"""
    name: str = Field(description="MCP 名称（唯一）")
    transport: str = Field(default="stdio", description="传输方式: stdio / http")
    command: str | None = Field(default=None, description="stdio 命令")
    args: list[str] = Field(default_factory=list, description="stdio 参数")
    url: str | None = Field(default=None, description="HTTP MCP 地址")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP 请求头")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")


class UpdateMCPRequest(BaseModel):
    """更新 MCP 请求"""
    name: str | None = Field(default=None, description="新名称")
    enabled: bool | None = Field(default=None, description="启用/禁用")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str | None = Field(default=None, description="描述")


class MCPResponse(BaseModel):
    """MCP 响应"""
    id: str
    user_id: str
    name: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    headers: dict[str, str]
    display_name: str | None
    description: str
    enabled: bool
    created_at: str
    updated_at: str


class ListMCPsResponse(BaseModel):
    """MCP 列表响应"""
    mcps: list[MCPResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _mcp_to_response(record: MCPRecord) -> MCPResponse:
    return MCPResponse(
        id=record.id,
        user_id=record.user_id,
        name=record.name,
        transport=record.transport,
        command=record.command,
        args=record.args,
        url=record.url,
        headers=record.headers,
        display_name=record.display_name,
        description=record.description,
        enabled=record.enabled,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListMCPsResponse)
async def list_mcps(request: Request, user_id: str = "anonymous"):
    """列出已安装 MCP"""
    config = getattr(request.app.state, "config", None)
    user_id = require_user_id(user_id, is_auth_enabled(config))
    storage = request.app.state.storage
    mcps = await storage.list_mcps(user_id)
    return ListMCPsResponse(
        mcps=[_mcp_to_response(m) for m in mcps],
        total=len(mcps),
    )


@router.post(
    "",
    response_model=MCPResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp(
    request: Request,
    body: CreateMCPRequest,
    user_id: str = "anonymous",
):
    """添加 MCP"""
    config = getattr(request.app.state, "config", None)
    user_id = require_user_id(user_id, is_auth_enabled(config))
    storage = request.app.state.storage

    # 检查名称唯一性
    existing = await storage.get_mcp_by_name(user_id, body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP '{body.name}' 已存在",
        )

    record = MCPRecord(
        user_id=user_id,
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        url=body.url,
        headers=body.headers,
        display_name=body.display_name,
        description=body.description,
    )

    mcp_id = await storage.upsert_mcp(user_id, record)
    created = await storage.get_mcp(user_id, mcp_id)
    return _mcp_to_response(created)


@router.patch("/{mcp_id}", response_model=MCPResponse)
async def update_mcp(
    request: Request,
    mcp_id: str,
    body: UpdateMCPRequest,
    user_id: str = "anonymous",
):
    """更新 MCP"""
    config = getattr(request.app.state, "config", None)
    user_id = require_user_id(user_id, is_auth_enabled(config))
    storage = request.app.state.storage
    existing = await storage.get_mcp(user_id, mcp_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP '{mcp_id}' 不存在",
        )

    # 应用更新
    if body.name is not None:
        existing.name = body.name
    if body.enabled is not None:
        existing.enabled = body.enabled
    if body.display_name is not None:
        existing.display_name = body.display_name
    if body.description is not None:
        existing.description = body.description

    await storage.upsert_mcp(user_id, existing)
    updated = await storage.get_mcp(user_id, mcp_id)
    return _mcp_to_response(updated)


@router.delete("/{mcp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp(
    request: Request,
    mcp_id: str,
    user_id: str = "anonymous",
):
    """删除 MCP"""
    config = getattr(request.app.state, "config", None)
    user_id = require_user_id(user_id, is_auth_enabled(config))
    storage = request.app.state.storage
    deleted = await storage.delete_mcp(user_id, mcp_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP '{mcp_id}' 不存在",
        )
