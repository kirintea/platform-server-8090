# -*- coding: utf-8 -*-

"""Workspace 管理 API 路由

端点：
- GET /workspace/{user_id}/{session_id}/files        — 列出工作区文件
- GET /workspace/{user_id}/{session_id}/files/{path}  — 获取文件内容
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from loguru import logger

router = APIRouter(prefix="/workspace", tags=["workspace"])


# ============================================================
# 响应 Schema
# ============================================================

class FileInfo(BaseModel):
    """文件信息"""
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float


class ListFilesResponse(BaseModel):
    """文件列表响应"""
    files: list[FileInfo]
    total: int
    workdir: str


# ============================================================
# 端点
# ============================================================

@router.get("/{user_id}/{session_id}/files", response_model=ListFilesResponse)
async def list_workspace_files(
    request: Request,
    user_id: str,
    session_id: str,
    sub_path: str = ".",
):
    """列出工作区中的文件"""
    workspace_mgr = request.app.state.workspace_manager
    workspace = await workspace_mgr.get_workspace(user_id, session_id)

    files = workspace.list_files(sub_path)
    return ListFilesResponse(
        files=[FileInfo(**f) for f in files],
        total=len(files),
        workdir=workspace.workdir,
    )


@router.get("/{user_id}/{session_id}/files/{file_path:path}")
async def get_workspace_file(
    request: Request,
    user_id: str,
    session_id: str,
    file_path: str,
):
    """下载工作区中的文件"""
    workspace_mgr = request.app.state.workspace_manager
    workspace = await workspace_mgr.get_workspace(user_id, session_id)

    abs_path = workspace.resolve_path(file_path)
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件 '{file_path}' 不存在",
        )

    return FileResponse(abs_path)
