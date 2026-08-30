# -*- coding: utf-8 -*-

"""API 速率限制中间件 — 滑动窗口 per-user 限流

对 LLM 调用端点（/chat/stream、/chat/）做 per-user 限流。
超限时返回 429 Too Many Requests + Retry-After header。

使用内存滑动窗口实现，适合单 Worker 部署。
多 Worker 场景需切换为 Redis-based 实现。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

from loguru import logger


class RateLimitMiddleware:
    """ASGI 速率限制中间件"""

    def __init__(
        self,
        app,
        *,
        requests_per_minute: int = 10,
        enabled: bool = True,
        protected_paths: set[str] | None = None,
    ):
        self.app = app
        self.rpm = requests_per_minute
        self.enabled = enabled
        # 需要限流的路径（默认仅 LLM 调用端点）
        self.protected_paths = protected_paths or {
            "/chat/stream",
            "/chat/",
        }
        # user_id -> [timestamps]
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _get_user_id(self, scope) -> str:
        """从请求中提取用户标识（用于限流 key）"""
        # 优先从 API Key 限流（认证场景）
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        api_key = headers.get(b"x-api-key", b"").decode("latin-1", errors="replace")
        if api_key:
            return f"key:{api_key[:16]}"  # 用前 16 字符避免存储完整 key

        # 回退到 query param user_id
        query_string = scope.get("query_string", b"").decode("latin-1", errors="replace")
        for part in query_string.split("&"):
            if part.startswith("user_id="):
                uid = part.split("=", 1)[1]
                if uid:
                    return f"uid:{uid}"

        return "anonymous"

    def _cleanup_old_entries(self, user_key: str, now: float):
        """清理 60 秒前的请求记录"""
        cutoff = now - 60.0
        entries = self._requests[user_key]
        # 二分查找第一个 >= cutoff 的位置
        lo, hi = 0, len(entries)
        while lo < hi:
            mid = (lo + hi) // 2
            if entries[mid] < cutoff:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0:
            self._requests[user_key] = entries[lo:]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path not in self.protected_paths:
            await self.app(scope, receive, send)
            return

        user_key = self._get_user_id(scope)
        now = time.monotonic()

        async with self._lock:
            self._cleanup_old_entries(user_key, now)
            count = len(self._requests[user_key])

            if count >= self.rpm:
                retry_after = 60 - (now - self._requests[user_key][0])
                retry_after = max(1, int(retry_after))
                logger.warning(
                    "速率限制触发: user={} path={} count={}/{}",
                    user_key, path, count, self.rpm,
                )
                body = json.dumps({
                    "error": "Too Many Requests",
                    "detail": f"速率限制：每分钟最多 {self.rpm} 次请求",
                    "retry_after": retry_after,
                }).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(retry_after).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return

            self._requests[user_key].append(now)

        await self.app(scope, receive, send)
