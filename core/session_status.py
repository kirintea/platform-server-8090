# -*- coding: utf-8 -*-

"""会话状态跟踪器 — 多端并发状态同步

管理会话级别的实时状态（生成中 / 空闲 / 中断中），存 Redis 跨实例共享。

核心机制：
- 设备A生成时，设备B发消息可立即得知"有人在说话"（不 spin-wait）
- 任意设备可通过 /interrupt 端点请求取消当前生成
- 所有设备通过 SSE/WebSocket 订阅同一事件流，实时看到生成过程

Redis key 设计：
- agentscope:session:{id}:status → JSON 状态（TTL 300s）
- agentscope:session:{id}:control → cancel 指令（TTL 60s）
"""

from __future__ import annotations

import json
import time
from enum import Enum

import redis.asyncio as aioredis

from loguru import logger


class SessionState(str, Enum):
    """会话状态枚举"""
    IDLE = "idle"
    GENERATING = "generating"
    INTERRUPTING = "interrupting"


class SessionBusyError(Exception):
    """会话正忙异常 — 设备B发消息时设备A正在生成"""

    def __init__(self, owner: str = "unknown", started_at: float = 0.0):
        self.owner = owner
        self.started_at = started_at
        super().__init__(f"会话正忙: 设备 {owner} 正在生成")


class SessionStatusTracker:
    """跟踪会话的实时状态（生成中/空闲/中断中），存 Redis 跨实例共享

    Args:
        redis: Redis 连接实例
        ttl: status key 过期时间（秒），默认 300s
    """

    PREFIX = "agentscope:session:"
    STATUS_SUFFIX = ":status"
    CONTROL_SUFFIX = ":control"

    def __init__(self, redis: aioredis.Redis, ttl: int = 300):
        self._redis = redis
        self._ttl = ttl

    def _status_key(self, session_id: str) -> str:
        return f"{self.PREFIX}{session_id}{self.STATUS_SUFFIX}"

    def _control_key(self, session_id: str) -> str:
        return f"{self.PREFIX}{session_id}{self.CONTROL_SUFFIX}"

    async def set_generating(
        self, session_id: str, owner: str, user_msg: str = "",
    ) -> None:
        """标记会话为"生成中"状态

        Args:
            session_id: 会话 ID
            owner: 持有者设备标识（device_id）
            user_msg: 用户消息预览（截取前 100 字符）
        """
        data = {
            "state": SessionState.GENERATING,
            "owner": owner,
            "started_at": time.time(),
            "user_msg_preview": user_msg[:100],
        }
        await self._redis.set(
            self._status_key(session_id),
            json.dumps(data, ensure_ascii=False),
            ex=self._ttl,
        )

    async def set_idle(self, session_id: str) -> None:
        """标记会话为空闲，清理 status + control key"""
        await self._redis.delete(self._status_key(session_id))
        await self._redis.delete(self._control_key(session_id))

    async def get_status(self, session_id: str) -> dict:
        """获取会话当前状态

        Returns:
            {"state": "idle"} 或 {"state": "generating", "owner": "...", ...}
        """
        raw = await self._redis.get(self._status_key(session_id))
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return {"state": SessionState.IDLE}

    async def request_cancel(self, session_id: str, from_device: str) -> bool:
        """请求取消当前生成（任意设备可调用）

        Args:
            session_id: 会话 ID
            from_device: 发起取消的设备标识

        Returns:
            True 如果有活跃生成被标记为取消，False 如果当前无活跃生成
        """
        status = await self.get_status(session_id)
        if status.get("state") != SessionState.GENERATING:
            return False

        control = {
            "action": "cancel",
            "from": from_device,
            "at": time.time(),
        }
        await self._redis.set(
            self._control_key(session_id),
            json.dumps(control, ensure_ascii=False),
            ex=60,  # 60s 过期，防止残留
        )
        # 同时更新状态为 interrupting
        status["state"] = SessionState.INTERRUPTING
        await self._redis.set(
            self._status_key(session_id),
            json.dumps(status, ensure_ascii=False),
            ex=self._ttl,
        )
        logger.info(
            "会话中断请求: session={} from_device={}",
            session_id, from_device,
        )
        return True

    async def should_cancel(self, session_id: str) -> bool:
        """检查是否应该取消（生成者在每个 event 迭代中调用）

        Returns:
            True 如果有 pending 的 cancel 请求
        """
        return await self._redis.exists(self._control_key(session_id)) > 0
