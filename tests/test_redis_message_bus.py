# -*- coding: utf-8 -*-

"""RedisMessageBus 测试 — 分布式锁 / Pub-Sub / 回放日志

测试使用 Redis DB 15，与生产数据隔离。
"""

import asyncio

import pytest

from core.redis_message_bus import RedisMessageBus

pytestmark = pytest.mark.asyncio(loop_scope="session")

REDIS_URL = "redis://localhost:6379/15"  # 用独立 DB 避免污染


async def test_distributed_lock_exclusive():
    """同一 key 同一时刻只有一个持有者"""
    bus1 = RedisMessageBus(REDIS_URL)
    bus2 = RedisMessageBus(REDIS_URL)
    await bus1.initialize()
    await bus2.initialize()

    results = []
    KEY = "test:lock:exclusive"

    async def worker(bus, name):
        async with bus.acquire_lock(KEY, ttl_secs=10):
            results.append(f"{name}:enter")
            await asyncio.sleep(0.2)
            results.append(f"{name}:leave")

    await asyncio.gather(worker(bus1, "A"), worker(bus2, "B"))

    # A enter/leave 或 B enter/leave 必须成对
    assert results.index("A:enter") < results.index("A:leave")
    assert results.index("B:enter") < results.index("B:leave")
    # 无交叉：B enter 必须在 A leave 之后 或 反之
    order_a = results.index("A:enter") < results.index("B:enter")
    if order_a:
        assert results.index("A:leave") < results.index("B:enter")
    else:
        assert results.index("B:leave") < results.index("A:enter")

    await bus1.aclose()
    await bus2.aclose()


async def test_pub_sub_cross():
    """publish 后所有 subscribe 者都收到"""
    bus1 = RedisMessageBus(REDIS_URL)
    bus2 = RedisMessageBus(REDIS_URL)
    await bus1.initialize()
    await bus2.initialize()

    received = {"A": [], "B": []}
    KEY = "test:pubsub:cross"

    async def sub(name, bus):
        async for evt in bus.subscribe(KEY):
            received[name].append(evt)
            if evt.get("done"):
                break

    tA = asyncio.create_task(sub("A", bus1))
    tB = asyncio.create_task(sub("B", bus2))
    await asyncio.sleep(0.3)  # 等待订阅就绪

    await bus1.publish(KEY, {"v": 1})
    await bus2.publish(KEY, {"v": 2})
    await bus1.publish(KEY, {"done": True})
    await asyncio.gather(tA, tB)

    assert {"v": 1} in received["A"] and {"v": 2} in received["A"]
    assert {"v": 1} in received["B"] and {"v": 2} in received["B"]
    await bus1.aclose()
    await bus2.aclose()


async def test_log_replay():
    """log_append 后 log_read 能按顺序回放"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:log:replay"
    await bus.log_trim(KEY)

    e1 = await bus.log_append(KEY, {"n": 1}, max_len=5)
    e2 = await bus.log_append(KEY, {"n": 2}, max_len=5)
    e3 = await bus.log_append(KEY, {"n": 3}, max_len=5)

    # since=None 从头读
    items = await bus.log_read(KEY, None, 100)
    ids = [i[0] for i in items]
    assert [i[1]["n"] for i in items] == [1, 2, 3]

    # since=e1 跳过 1
    items2 = await bus.log_read(KEY, since=e1, max_count=100)
    assert [i[1]["n"] for i in items2] == [2, 3]
    await bus.aclose()


# ============================================================
# Mode A — drain queue
# ============================================================


async def test_queue_push_drain():
    """push 3 条 → drain 返回 3 条且按顺序 → 再 drain 为空"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:queue:drain"
    await bus.queue_delete(KEY)

    await bus.queue_push(KEY, {"n": 1})
    await bus.queue_push(KEY, {"n": 2})
    await bus.queue_push(KEY, {"n": 3})

    drained = await bus.queue_drain(KEY, max_count=100)
    assert [p["n"] for _, p in drained] == [1, 2, 3]

    # 再次 drain 应为空（ack-on-read）
    drained_again = await bus.queue_drain(KEY, max_count=100)
    assert drained_again == []
    await bus.aclose()


async def test_queue_push_ttl_expires():
    """queue_push 带 ttl_secs，过期后 key 自动消失（sliding TTL）"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:queue:ttl"
    await bus.queue_delete(KEY)

    await bus.queue_push(KEY, {"n": 1}, ttl_secs=1)
    # 立即可读
    exists_immediately = await bus._redis.exists(KEY)
    assert exists_immediately > 0

    # 等 1.5s 让 TTL 到期
    await asyncio.sleep(1.5)
    exists_after = await bus._redis.exists(KEY)
    assert exists_after == 0, "queue key 应在 TTL 到期后自动删除"
    await bus.aclose()


# ============================================================
# Mode F — registry map
# ============================================================


async def test_registry_crud():
    """registry_set/get/exists/getall/del 全套，返回类型必须为 str"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    NS = "test:registry:crud"
    await bus.registry_drop(NS)

    # set
    await bus.registry_set(NS, "f1", "v1")
    await bus.registry_set(NS, "f2", "v2")

    # exists
    assert await bus.registry_exists(NS, "f1") is True
    assert await bus.registry_exists(NS, "missing") is False

    # get — 必须是 str 而非 bytes（C1 回归）
    val = await bus.registry_get(NS, "f1")
    assert val == "v1"
    assert isinstance(val, str), f"registry_get 应返回 str，实际 {type(val)}"

    # getall — 必须是 dict[str, str] 而非 dict[bytes, bytes]（C1 回归）
    all_items = await bus.registry_getall(NS)
    assert all_items == {"f1": "v1", "f2": "v2"}
    for k, v in all_items.items():
        assert isinstance(k, str), f"key 应为 str，实际 {type(k)}"
        assert isinstance(v, str), f"value 应为 str，实际 {type(v)}"

    # del
    await bus.registry_del(NS, "f1")
    assert await bus.registry_exists(NS, "f1") is False
    assert await bus.registry_get(NS, "f1") is None
    assert await bus.registry_getall(NS) == {"f2": "v2"}

    # drop
    await bus.registry_drop(NS)
    assert await bus.registry_getall(NS) == {}
    await bus.aclose()


# ============================================================
# Mode E — try_lock / unlock
# ============================================================


async def test_try_lock_and_unlock():
    """try_lock 成功后 is_locked=True；unlock 后 False"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:lock:try"
    await bus.unlock(KEY)  # 清理残留

    # try_lock 成功
    ok = await bus.try_lock(KEY, ttl_secs=10)
    assert ok is True
    assert await bus.is_locked(KEY) is True

    # 再次 try_lock 应失败（已被持有）
    ok2 = await bus.try_lock(KEY, ttl_secs=10)
    assert ok2 is False

    # unlock 后可再次获取
    await bus.unlock(KEY)
    assert await bus.is_locked(KEY) is False
    ok3 = await bus.try_lock(KEY, ttl_secs=10)
    assert ok3 is True
    await bus.unlock(KEY)
    await bus.aclose()


async def test_lock_heartbeat_renews_ttl():
    """持锁时间超过 ttl_secs 时，心跳应续约，锁不应被抢占（I1 回归）"""
    bus1 = RedisMessageBus(REDIS_URL)
    bus2 = RedisMessageBus(REDIS_URL)
    await bus1.initialize()
    await bus2.initialize()
    KEY = "test:lock:heartbeat"
    await bus1.unlock(KEY)

    # 用很短的 ttl（1s），持锁 1.6s，验证心跳续约后 bus2 仍拿不到
    async with bus1.acquire_lock(KEY, ttl_secs=1):
        # 0.3s 时 bus2 抢不到
        await asyncio.sleep(0.3)
        assert await bus2.try_lock(KEY, ttl_secs=5) is False
        # 跨过 ttl 边界（>1s），心跳应已续约
        await asyncio.sleep(1.3)
        assert await bus1.is_locked(KEY) is True, "心跳应续约，锁不应自然过期"
        # bus2 仍抢不到
        assert await bus2.try_lock(KEY, ttl_secs=5) is False

    # 退出后 bus2 可获取
    assert await bus2.try_lock(KEY, ttl_secs=5) is True
    await bus2.unlock(KEY)
    await bus1.aclose()
    await bus2.aclose()


# ============================================================
# Mode C — log_trim(before_id)
# ============================================================


async def test_log_trim_with_before_id():
    """log_trim(before_id=e2) 后只保留 e3 及之后"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:log:trim"
    await bus.log_trim(KEY)

    e1 = await bus.log_append(KEY, {"n": 1})
    e2 = await bus.log_append(KEY, {"n": 2})
    e3 = await bus.log_append(KEY, {"n": 3})

    # 裁剪掉 e2 之前的（保留 e2 及之后）
    await bus.log_trim(KEY, before_id=e2)

    items = await bus.log_read(KEY, None, 100)
    assert [p["n"] for _, p in items] == [2, 3]

    # 再裁剪到 e3 之后
    await bus.log_trim(KEY, before_id=e3)
    items2 = await bus.log_read(KEY, None, 100)
    assert [p["n"] for _, p in items2] == [3]
    await bus.aclose()


# ============================================================
# Mode C — log_append 原子性（I3 回归）
# ============================================================


async def test_log_append_concurrent_order():
    """并发 log_append 时，log_read 的顺序与 entry_id 顺序一致（I3 回归）"""
    bus = RedisMessageBus(REDIS_URL)
    await bus.initialize()
    KEY = "test:log:concurrent"
    await bus.log_trim(KEY)

    N = 50  # 50 个并发 append
    payloads = [{"n": i} for i in range(N)]

    await asyncio.gather(*[
        bus.log_append(KEY, p) for p in payloads
    ])

    items = await bus.log_read(KEY, None, 1000)
    # 条目数量正确
    assert len(items) == N
    # entry_id 单调递增
    seqs = [int(eid.split("-")[0]) for eid, _ in items]
    assert seqs == sorted(seqs), f"entry_id 非单调递增: {seqs}"
    # 物理顺序与 seq 顺序一致（关键：INCR+RPUSH 必须原子）
    for i in range(1, len(items)):
        assert seqs[i] > seqs[i - 1], (
            f"物理顺序与 seq 不一致于 index {i}: "
            f"prev_seq={seqs[i-1]} cur_seq={seqs[i]}"
        )
    # 所有 payload 都在
    ns = sorted(p["n"] for _, p in items)
    assert ns == list(range(N))
    await bus.aclose()
