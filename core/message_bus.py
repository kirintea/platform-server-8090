# -*- coding: utf-8 -*-

"""内存消息总线 — 单进程版本，用于开发和测试

实现 AgentScope MessageBus 接口的子集，支持：
- Mode A: drain queue（单消费者队列）
- Mode C: replay log（多消费者回放日志）
- Mode D: transient broadcast（发布/订阅）
- Mode E: distributed lock（进程内 asyncio.Lock）
- Mode F: registry map（hash 键值对）

参考: example/.../app/message_bus/_in_memory_message_bus.py

注意: 此实现仅适用于单进程部署。多进程生产环境应使用 Redis 消息总线。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Callable

from agentscope.app.message_bus import MessageBus, MessageBusKeys


class InMemoryMessageBus(MessageBus):
    """内存消息总线 — 基于 asyncio 原语和 Python dict

    消费模式映射：
    - Mode A (drain queue): list[(entry_id, payload, expire_at)]
    - Mode C (replay log): list[(entry_id, payload)]，非破坏性读取
    - Mode D (broadcast): set[asyncio.Queue]，fire-and-forget
    - Mode E (lock): asyncio.Lock，进程内互斥
    - Mode F (registry): dict[str, dict[str, str]]，hash 存储
    """

    def __init__(self) -> None:
        self._seq: int = 0

        # Mode A — drain queues
        self._queues: dict[
            str,
            list[tuple[str, dict, float | None]],
        ] = defaultdict(list)

        # Mode C — replay logs
        self._logs: dict[str, list[tuple[str, dict]]] = defaultdict(list)

        # Mode D — pub/sub
        self._subscribers: dict[
            str,
            set[asyncio.Queue[dict | None]],
        ] = defaultdict(set)

        # Mode E — locks
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._lock_holders: dict[str, str] = {}

        # Mode F — registry maps
        self._registries: dict[str, dict[str, str]] = defaultdict(dict)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        """生成单调递增的 entry ID"""
        self._seq += 1
        return f"{self._seq}-0"

    # ------------------------------------------------------------------
    # Mode A — drain queue
    # ------------------------------------------------------------------

    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        entry_id = self._next_id()
        queue = self._queues[key]
        now = time.monotonic()
        # 清理过期条目
        queue[:] = [e for e in queue if e[2] is None or e[2] > now]
        expire_at = now + ttl_secs if ttl_secs else None
        queue.append((entry_id, payload, expire_at))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        q = self._queues.get(key)
        if not q:
            return []
        now = time.monotonic()
        alive = [e for e in q if e[2] is None or e[2] > now]
        drained = alive[:max_count]
        self._queues[key] = alive[max_count:]
        return [(entry_id, payload) for entry_id, payload, _ in drained]

    async def queue_delete(self, key: str) -> None:
        self._queues.pop(key, None)

    # ------------------------------------------------------------------
    # Mode C — replay log
    # ------------------------------------------------------------------

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        entry_id = self._next_id()
        log = self._logs[key]
        log.append((entry_id, payload))
        if max_len is not None and len(log) > max_len:
            del log[: len(log) - max_len]
        return entry_id

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        log = self._logs.get(key)
        if not log:
            return []
        if since is None:
            return log[:max_count]
        since_seq = int(since.split("-")[0])
        start = 0
        for i, (eid, _) in enumerate(log):
            if int(eid.split("-")[0]) > since_seq:
                start = i
                break
        else:
            return []
        return log[start: start + max_count]

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        if before_id is None:
            self._logs.pop(key, None)
            return
        log = self._logs.get(key)
        if not log:
            return
        before_seq = int(before_id.split("-")[0])
        self._logs[key] = [
            (eid, p) for eid, p in log if int(eid.split("-")[0]) >= before_seq
        ]

    # ------------------------------------------------------------------
    # Mode D — transient broadcast
    # ------------------------------------------------------------------

    async def publish(self, key: str, payload: dict) -> None:
        for q in self._subscribers.get(key, set()):
            q.put_nowait(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        q: asyncio.Queue[dict | None] = asyncio.Queue()
        self._subscribers[key].add(q)
        try:
            if on_ready is not None:
                on_ready()
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            self._subscribers[key].discard(q)

    # ------------------------------------------------------------------
    # Mode E — distributed lock（进程内）
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        lock = self._locks[key]
        token = uuid.uuid4().hex
        async with lock:
            self._lock_holders[key] = token
            try:
                yield
            finally:
                self._lock_holders.pop(key, None)

    async def is_locked(self, key: str) -> bool:
        return key in self._lock_holders

    async def try_lock(self, key: str, *, ttl_secs: int = 600) -> bool:
        if key in self._lock_holders:
            return False
        self._lock_holders[key] = "1"
        return True

    async def unlock(self, key: str) -> None:
        self._lock_holders.pop(key, None)

    # ------------------------------------------------------------------
    # Mode F — registry map
    # ------------------------------------------------------------------

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        self._registries[namespace][field] = value

    async def registry_del(self, namespace: str, field: str) -> None:
        reg = self._registries.get(namespace)
        if reg is not None:
            reg.pop(field, None)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        return field in self._registries.get(namespace, {})

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        return dict(self._registries.get(namespace, {}))

    async def registry_get(self, namespace: str, field: str) -> str | None:
        return self._registries.get(namespace, {}).get(field)

    async def registry_drop(self, namespace: str) -> None:
        self._registries.pop(namespace, None)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """关闭所有订阅者"""
        for subs in self._subscribers.values():
            for q in subs:
                q.put_nowait(None)
        self._subscribers.clear()
