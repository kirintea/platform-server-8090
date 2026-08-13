# -*- coding: utf-8 -*-

"""Redis 分布式消息总线 — 实现 MessageBus 接口的多实例版本

映射关系：
- Mode A drain queue   → Redis List (RPUSH + LPOP range)
- Mode C replay log    → Redis List (RPUSH + LRANGE, 附 entry_id 单调序列)
- Mode D broadcast     → Redis Pub/Sub (PUBLISH / SUBSCRIBE)
- Mode E distributed lock → Redis SET NX EX + 随机 token 释放校验
- Mode F registry map  → Redis Hash

参考: agentscope.app.message_bus._base.MessageBus
      core/message_bus.py (InMemoryMessageBus — 单进程版本)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Callable

import redis.asyncio as aioredis
from agentscope.app.message_bus import MessageBus


class RedisMessageBus(MessageBus):
    """基于 Redis 的多实例消息总线

    所有实例共享同一个 Redis，通过 key 隔离不同会话/通道。
    支持 Mode A/C/D/E/F 五种消费语义（见 MessageBus 基类文档）。
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        # subscribe 消费者 → 任务管理
        self._pubsub_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        """创建 Redis 连接并验证可达

        decode_responses=True 让所有返回值自动 decode 为 str，
        保证 registry_get/getall 等方法的 ``str`` / ``dict[str, str]``
        类型契约（否则默认返回 bytes）。
        """
        self._redis = aioredis.from_url(
            self._redis_url, decode_responses=True,
        )
        await self._redis.ping()

    # --------------------------------------------------------------
    # Mode E — distributed lock (SET NX EX + token verify)
    # --------------------------------------------------------------

    @asynccontextmanager
    async def acquire_lock(
        self, key: str, *, ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """获取分布式锁（自旋等待），持锁期间心跳续约，释放时校验 token 防误删

        - SET NX EX 原子抢锁，失败则自旋重试（最多 ttl_secs 秒）
        - 持锁期间启动心跳 task，每 ttl_secs/2 秒 EXPIRE 续约，
          避免长 run（如多轮 ReAct）超过 ttl 后锁自然过期被他实例抢占
        - 释放用 Lua 脚本 GET+DEL 校验 token，防止误删他人的锁
        """
        token = uuid.uuid4().hex
        acquired = False
        heartbeat_task: asyncio.Task | None = None
        try:
            acquired = await self._redis.set(key, token, nx=True, ex=ttl_secs)
            if not acquired:
                # 自旋等待（最多 ttl 秒，避免死等）
                deadline = time.time() + ttl_secs
                while not acquired and time.time() < deadline:
                    await asyncio.sleep(0.05)
                    acquired = await self._redis.set(
                        key, token, nx=True, ex=ttl_secs,
                    )
                if not acquired:
                    raise RuntimeError(f"获取分布式锁超时: {key}")

            # 心跳续约：每 ttl/2 秒刷新 TTL，保证长 run 不掉锁
            async def _heartbeat() -> None:
                while True:
                    await asyncio.sleep(max(0.1, ttl_secs / 2))
                    try:
                        # 仅当 token 仍属本持有人时续约（防误续他人的锁）
                        renew_script = """
                        if redis.call('GET', KEYS[1]) == ARGV[1] then
                            return redis.call('EXPIRE', KEYS[1], ARGV[2])
                        else
                            return 0
                        end
                        """
                        await self._redis.eval(
                            renew_script, 1, key, token, ttl_secs,
                        )
                    except Exception:
                        pass

            heartbeat_task = asyncio.create_task(_heartbeat())
            yield
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            if acquired:
                # Lua 脚本：token 匹配才删除，防止误删他人的锁
                script = """
                if redis.call('GET', KEYS[1]) == ARGV[1] then
                    return redis.call('DEL', KEYS[1])
                else
                    return 0
                end
                """
                await self._redis.eval(script, 1, key, token)

    async def is_locked(self, key: str) -> bool:
        return await self._redis.exists(key) > 0

    async def try_lock(self, key: str, *, ttl_secs: int = 600) -> bool:
        token = uuid.uuid4().hex
        ok = await self._redis.set(key, token, nx=True, ex=ttl_secs)
        return bool(ok)

    async def unlock(self, key: str) -> None:
        await self._redis.delete(key)

    # --------------------------------------------------------------
    # Mode C — replay log (Redis List + 单调递增 entry_id)
    # --------------------------------------------------------------

    async def log_append(
        self, key: str, payload: dict, *,
        ttl_secs: int | None = None, max_len: int | None = None,
    ) -> str:
        """追加到回放日志

        entry_id 使用 Redis INCR 生成的单调递增序列号，格式 ``{seq}-0``，
        与 InMemoryMessageBus 一致，保证 log_read 的 since 游标
        在并发追加时仍能正确按顺序比较。

        实现用 Lua 脚本原子完成 INCR+RPUSH+LTRIM+EXPIRE，
        避免并发追加时 INCR 与 RPUSH 之间的竞态导致 List 物理顺序
        与 seq 顺序不一致（破坏 append-order 语义）。
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        seq_key = f"{key}:seq"
        max_len_str = str(max_len) if max_len is not None else ""
        ttl_str = str(ttl_secs) if ttl_secs is not None else ""

        # Lua 用字符串拼接构造 wrapped JSON（避免 cjson key 顺序不确定）
        script = """
        local seq = redis.call('INCR', KEYS[2])
        local entry_id = seq .. '-0'
        local wrapped = '{"_entry_id":"' .. entry_id .. '","payload":' .. ARGV[1] .. '}'
        redis.call('RPUSH', KEYS[1], wrapped)
        if ARGV[2] ~= '' then
            redis.call('LTRIM', KEYS[1], -tonumber(ARGV[2]), -1)
        end
        if ARGV[3] ~= '' then
            redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
            redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
        end
        return entry_id
        """
        entry_id = await self._redis.eval(
            script, 2, key, seq_key, payload_json, max_len_str, ttl_str,
        )
        return entry_id

    async def log_read(
        self, key: str, since: str | None = None, max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """非破坏性读取，返回比 since 更新的条目（按追加顺序）"""
        raw = await self._redis.lrange(key, 0, -1)
        since_seq = int(since.split("-")[0]) if since is not None else None
        entries: list[tuple[str, dict]] = []
        for r in raw:
            w = json.loads(r)
            eid = w["_entry_id"]
            seq = int(eid.split("-")[0])
            if since_seq is not None and seq <= since_seq:
                continue
            entries.append((eid, w["payload"]))
        return entries[:max_count]

    async def log_trim(self, key: str, before_id: str | None = None) -> None:
        """裁剪回放日志；before_id=None 删除整个日志（含序列号 key）"""
        if before_id is None:
            pipe = self._redis.pipeline()
            pipe.delete(key)
            pipe.delete(f"{key}:seq")
            await pipe.execute()
            return
        before_seq = int(before_id.split("-")[0])
        raw = await self._redis.lrange(key, 0, -1)
        keep: list[str] = []
        for r in raw:
            w = json.loads(r)
            seq = int(w["_entry_id"].split("-")[0])
            if seq >= before_seq:
                keep.append(r)
        pipe = self._redis.pipeline()
        pipe.delete(key)
        if keep:
            pipe.rpush(key, *keep)
        await pipe.execute()

    # --------------------------------------------------------------
    # Mode D — broadcast (Redis Pub/Sub)
    # --------------------------------------------------------------

    async def publish(self, key: str, payload: dict) -> None:
        await self._redis.publish(key, json.dumps(payload, ensure_ascii=False))

    async def subscribe(
        self, key: str, *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """订阅广播通道，yield 收到的 payload，直到消费者关闭生成器"""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(key)
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _listener() -> None:
            try:
                async for msg in pubsub.listen():
                    if msg["type"] == "subscribe":
                        continue
                    if msg["type"] == "message":
                        data = json.loads(msg["data"])
                        await queue.put(data)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                await queue.put(None)

        task: asyncio.Task | None = None
        try:
            if on_ready:
                on_ready()
            task = asyncio.create_task(_listener())
            self._pubsub_tasks.add(task)
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                self._pubsub_tasks.discard(task)
            try:
                await pubsub.unsubscribe(key)
                await pubsub.aclose()
            except Exception:
                pass

    # --------------------------------------------------------------
    # Mode A — drain queue (Redis List RPUSH / LPOP range)
    # --------------------------------------------------------------

    async def queue_push(
        self, key: str, payload: dict, *, ttl_secs: int | None = None,
    ) -> str:
        entry_id = f"{int(time.time() * 1e6)}-{uuid.uuid4().hex[:8]}"
        wrapped = {"_entry_id": entry_id, "payload": payload}
        if ttl_secs:
            wrapped["_expire_at"] = time.time() + ttl_secs
        pipe = self._redis.pipeline()
        pipe.rpush(key, json.dumps(wrapped, ensure_ascii=False))
        # sliding TTL：每次 push 刷新 key 过期时间，消费者消失时队列自动清理
        if ttl_secs:
            pipe.expire(key, ttl_secs)
        await pipe.execute()
        return entry_id

    async def queue_drain(
        self, key: str, max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        now = time.time()
        for _ in range(max_count):
            raw = await self._redis.lpop(key)
            if raw is None:
                break
            w = json.loads(raw)
            exp = w.get("_expire_at")
            if exp and exp < now:
                continue
            out.append((w["_entry_id"], w["payload"]))
        return out

    async def queue_delete(self, key: str) -> None:
        await self._redis.delete(key)

    # --------------------------------------------------------------
    # Mode F — registry map (Redis Hash)
    # --------------------------------------------------------------

    async def registry_set(
        self, namespace: str, field: str, value: str, *,
        ttl_secs: int | None = None,
    ) -> None:
        await self._redis.hset(namespace, field, value)
        if ttl_secs:
            await self._redis.expire(namespace, ttl_secs)

    async def registry_del(self, namespace: str, field: str) -> None:
        await self._redis.hdel(namespace, field)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        return await self._redis.hexists(namespace, field)

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        raw = await self._redis.hgetall(namespace)
        return dict(raw)

    async def registry_get(self, namespace: str, field: str) -> str | None:
        return await self._redis.hget(namespace, field)

    async def registry_drop(self, namespace: str) -> None:
        await self._redis.delete(namespace)

    # --------------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------------

    async def aclose(self) -> None:
        """关闭所有订阅任务与 Redis 连接"""
        for t in list(self._pubsub_tasks):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._pubsub_tasks.clear()
        if self._redis:
            await self._redis.aclose()
            self._redis = None
