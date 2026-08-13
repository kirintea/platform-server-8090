# -*- coding: utf-8 -*-

"""存储层数据模型 — 定义所有持久化资源的 Pydantic 模型

参考 AgentScope 的 storage._model，简化为平台所需的子集。
所有记录共用 _RecordBase（id + created_at + updated_at）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 基础模型
# ============================================================

def _generate_id() -> str:
    """生成短唯一 ID"""
    return uuid.uuid4().hex[:16]


class _RecordBase(BaseModel):
    """所有记录的基类"""

    id: str = Field(
        default_factory=_generate_id,
        description="唯一标识",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="更新时间",
    )


# ============================================================
# Agent 记录
# ============================================================

class AgentData(BaseModel):
    """Agent 配置数据"""

    name: str = Field(description="Agent 名称")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="系统提示词",
    )
    context_config: dict = Field(
        default_factory=lambda: {
            "trigger_ratio": 0.8,
            "reserve_ratio": 0.1,
        },
        description="上下文压缩配置",
    )
    react_config: dict = Field(
        default_factory=lambda: {
            "max_iters": 50,
            "stop_on_reject": False,
        },
        description="ReAct 配置",
    )


class AgentRecord(_RecordBase):
    """Agent 持久化记录"""

    user_id: str = Field(description="所属用户 ID")
    source: str = Field(default="user", description="来源: user / team")
    data: AgentData = Field(description="Agent 配置数据")


# ============================================================
# Session 记录
# ============================================================

class SessionSource(str, Enum):
    """会话来源"""
    USER = "user"
    SCHEDULE = "schedule"
    CHANNEL = "channel"
    FORK = "fork"


class SessionConfig(BaseModel):
    """会话配置"""

    workspace_id: str = Field(
        default_factory=_generate_id,
        description="工作区 ID",
    )
    name: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="会话显示名称",
    )
    cwd: str | None = Field(default=None, description="当前工作目录")
    chat_model_config: dict | None = Field(
        default=None,
        description="聊天模型配置",
    )


class SessionRecord(_RecordBase):
    """会话持久化记录"""

    user_id: str = Field(description="所属用户 ID")
    agent_id: str = Field(description="所属 Agent ID")
    source: SessionSource = Field(
        default=SessionSource.USER,
        description="会话来源",
    )
    team_id: str | None = Field(default=None, description="所属团队 ID")
    config: SessionConfig = Field(description="会话配置")
    state_json: str = Field(
        default="",
        description="AgentState 序列化 JSON（由 chat_service 管理）",
    )
    parent_session_id: str | None = Field(
        default=None,
        description="父会话 ID（Fork 血缘），根会话为 None",
    )
    depth: int = Field(
        default=0,
        description="Fork 深度，根会话=0，每 fork 一次 +1",
    )


# ============================================================
# MCP 记录
# ============================================================

class MCPRecord(_RecordBase):
    """已安装 MCP 记录"""

    user_id: str = Field(description="所属用户 ID")
    name: str = Field(description="MCP 名称（唯一）")
    transport: str = Field(default="stdio", description="传输方式: stdio / http")
    command: str | None = Field(default=None, description="stdio 命令")
    args: list[str] = Field(default_factory=list, description="stdio 参数")
    url: str | None = Field(default=None, description="HTTP MCP 地址")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP 请求头")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")
    author: str | None = Field(default=None, description="作者")
    icon_url: str | None = Field(default=None, description="图标 URL")
    tags: list[str] = Field(default_factory=list, description="标签")
    hub_id: str | None = Field(default=None, description="来源 Hub ID")
    card_id: str | None = Field(default=None, description="Hub 卡片 ID")
    version: str | None = Field(default=None, description="版本")
    enabled: bool = Field(default=True, description="是否启用")


# ============================================================
# Skill 记录
# ============================================================

class SkillRecord(_RecordBase):
    """已安装 Skill 记录"""

    user_id: str = Field(description="所属用户 ID")
    name: str = Field(description="Skill 名称（唯一）")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")
    markdown: str = Field(default="", description="SKILL.md 内容")
    tags: list[str] = Field(default_factory=list, description="标签")
    author: str | None = Field(default=None, description="作者")
    icon_url: str | None = Field(default=None, description="图标 URL")
    hub_id: str | None = Field(default=None, description="来源 Hub ID")
    card_id: str | None = Field(default=None, description="Hub 卡片 ID")
    version: str | None = Field(default=None, description="版本")
    enabled: bool = Field(default=True, description="是否启用")


# ============================================================
# Message 记录
# ============================================================

class MessageRecord(_RecordBase):
    """消息持久化记录"""

    user_id: str = Field(description="所属用户 ID")
    session_id: str = Field(description="所属会话 ID")
    msg_id: str = Field(description="消息 ID（AgentScope Msg.id）")
    role: str = Field(description="角色: user / assistant / system / tool")
    content: str = Field(description="消息文本内容")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="元数据（工具调用、token 用量等）",
    )


# ============================================================
# Schedule 记录
# ============================================================

class ScheduleSource(str, Enum):
    """调度来源"""
    USER = "user"
    SYSTEM = "system"


class ScheduleRecord(_RecordBase):
    """定时任务记录"""

    user_id: str = Field(description="所属用户 ID")
    agent_id: str = Field(description="关联 Agent ID")
    session_id: str | None = Field(default=None, description="关联会话 ID")
    name: str = Field(description="任务名称")
    cron_expr: str = Field(description="Cron 表达式")
    prompt: str = Field(description="触发时发送的提示词")
    source: ScheduleSource = Field(
        default=ScheduleSource.USER,
        description="来源",
    )
    enabled: bool = Field(default=True, description="是否启用")
    last_run_at: datetime | None = Field(default=None, description="上次执行时间")
    next_run_at: datetime | None = Field(default=None, description="下次执行时间")
