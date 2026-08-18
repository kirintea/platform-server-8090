# -*- coding: utf-8 -*-

"""Task 5 — Fork API 端点 + 元数据透传 集成测试

验证：
1. POST /sessions/{user_id}/{session_id}/fork 创建分支会话
2. 子会话 session_id 与父会话不同
3. 响应包含 parent_session_id 指向父会话
4. 子会话消息历史与父会话一致（深拷贝：条数相等 + 内容一致）
5. GET /sessions/{user_id} 富化逻辑：子会话透传 parent_session_id / depth

测试使用 POST /chat/stream 端点创建父会话，消费完整 SSE 流确保
LLM 回复完成且 state 持久化到 Redis 后再执行 fork。
测试前清理 Redis 中可能残留的旧 state/meta/lock，避免历史运行污染。
"""

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from main import app
from core.config import ConfigManager

UID = "u_api_fork"
SID = "s_parent_01"


def _cleanup_redis():
    """按 user 前缀扫描清理，覆盖动态生成的 child_sid"""
    cfg = ConfigManager.get_instance().load()
    r = redis_sync.from_url(cfg.redis.url, decode_responses=True)
    cursor = 0
    while True:
        cursor, keys = r.scan(
            cursor=cursor,
            match=f"{cfg.redis.key_prefix}{UID}:*",
            count=50,
        )
        if keys:
            r.delete(*keys)
        if cursor == 0:
            break
    r.close()


@pytest.fixture
def client():
    _cleanup_redis()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    _cleanup_redis()


def test_fork_endpoint_creates_branch(client):
    # 1. POST /chat/stream 触发对话，消费 SSE 流确保 state 持久化到 Redis
    with client.stream("POST", "/chat/stream", json={
        "user_id": UID,
        "session_id": SID,
        "message": "你好，请记住我叫小明",
    }) as r:
        assert r.status_code == 200, f"POST /chat/stream 失败: {r.status_code}"
        for line in r.iter_lines():
            pass
    sid = SID

    # 2. fork
    r2 = client.post(f"/sessions/{UID}/{sid}/fork")
    assert r2.status_code == 200, f"fork 失败: {r2.status_code} {r2.text}"
    data = r2.json()
    child_sid = data["session_id"]
    assert child_sid is not None and child_sid != sid
    assert data["parent_session_id"] == sid

    # 3. 查看会话消息应相同（深拷贝验证：条数相等 + 内容一致）
    r3 = client.get(f"/sessions/{UID}/{child_sid}/messages")
    r4 = client.get(f"/sessions/{UID}/{sid}/messages")
    msgs_child = r3.json().get("messages", [])
    msgs_parent = r4.json().get("messages", [])
    assert len(msgs_child) == len(msgs_parent), (
        f"子会话消息条数 {len(msgs_child)} 应等于父会话 {len(msgs_parent)}"
    )
    for mc, mp in zip(msgs_child, msgs_parent):
        assert mc.get("role") == mp.get("role"), f"角色不匹配: {mc} vs {mp}"
        assert mc.get("content") == mp.get("content"), f"内容不匹配: {mc} vs {mp}"

    # 4. 验证 list_user_sessions 富化（parent_session_id / depth）
    r5 = client.get(f"/sessions/{UID}")
    sessions = r5.json().get("sessions", [])
    child_in_list = next((s for s in sessions if s.get("session_id") == child_sid), None)
    assert child_in_list is not None, "fork 出的子会话应出现在列表中"
    assert child_in_list.get("parent_session_id") == sid
    assert child_in_list.get("depth") is not None and child_in_list.get("depth") >= 1

    # 5. 清理
    client.delete(f"/sessions/{UID}/{child_sid}")
    client.delete(f"/sessions/{UID}/{sid}")
