# -*- coding: utf-8 -*-

"""API-Key 鉴权中间件单元测试 (无需真实服务器)

直接导入 server.py 中的 `_api_key_auth_middleware` (纯 ASGI 闭包), 用内存版
scope / receive / send 驱动, 验证:
  - AUTH_REQUIRED=true 且缺 X-API-Key                  -> 401
  - 携带正确 X-API-Key                                  -> 200 且下游被调用
  - 携带错误 X-API-Key                                  -> 401
  - 豁免路径 (/webui, /health, /docs, /openapi.json,
    /redoc, 以及所有 /webui/*) 免鉴权, 即使无 key
  - AUTH_REQUIRED 非 "true" 时全量放行

若 server.py 因环境缺少依赖而无法导入, 整体跳过并给出明确说明 (不报错污染)。
"""

import asyncio

import pytest

try:
    from server import _api_key_auth_middleware
    _HAVE_SERVER = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    _HAVE_SERVER = False
    _IMPORT_ERROR = exc


@pytest.fixture(autouse=True)
def _require_server():
    if not _HAVE_SERVER:
        pytest.skip(f"server.py 不可导入, 跳过鉴权测试: {_IMPORT_ERROR}")


@pytest.fixture
def auth_env(monkeypatch):
    # 打开鉴权并设置一个非空密钥
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("API_KEY", "secret-key-123")
    yield


def _run(path: str, api_key=None):
    """驱动中间件: 返回 (下游被调用次数, HTTP 状态码)。"""
    headers = []
    if api_key is not None:
        headers.append((b"x-api-key", api_key.encode("latin-1")))
    scope = {"type": "http", "path": path, "headers": headers}

    calls = {"n": 0}

    async def downstream(dscope, receive, send):
        calls["n"] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    received = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        received.append(msg)

    mw = _api_key_auth_middleware(downstream)
    asyncio.run(mw(scope, receive, send))

    status = None
    for msg in received:
        if msg.get("type") == "http.response.start":
            status = msg.get("status")
            break
    return calls["n"], status


# ----------------------------------------------------------------
# 受保护路径: 鉴权行为
# ----------------------------------------------------------------

def test_missing_key_protected_path_401(auth_env):
    calls, status = _run("/chat/stream")
    assert status == 401
    assert calls == 0, "无 key 的受保护路径不应调用下游"


def test_correct_key_protected_path_200(auth_env):
    calls, status = _run("/chat/stream", api_key="secret-key-123")
    assert status == 200
    assert calls == 1, "携带正确 key 的受保护路径应放行到下游"


def test_wrong_key_protected_path_401(auth_env):
    calls, status = _run("/chat/stream", api_key="wrong-key")
    assert status == 401
    assert calls == 0, "错误 key 应被拒绝且不调用下游"


# ----------------------------------------------------------------
# 豁免路径: 免鉴权
# ----------------------------------------------------------------

def test_exempt_webui_bypasses(auth_env):
    calls, status = _run("/webui")
    assert status == 200
    assert calls == 1


def test_exempt_webui_subpath_bypasses(auth_env):
    calls, status = _run("/webui/assets/index.js")
    assert status == 200
    assert calls == 1


def test_exempt_health_bypasses(auth_env):
    calls, status = _run("/health")
    assert status == 200
    assert calls == 1


def test_exempt_docs_bypasses(auth_env):
    calls, status = _run("/docs")
    assert status == 200
    assert calls == 1


def test_exempt_openapi_bypasses(auth_env):
    calls, status = _run("/openapi.json")
    assert status == 200
    assert calls == 1


def test_exempt_redoc_bypasses(auth_env):
    calls, status = _run("/redoc")
    assert status == 200
    assert calls == 1


# ----------------------------------------------------------------
# 鉴权关闭: 全量放行
# ----------------------------------------------------------------

def test_auth_disabled_passthrough(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("API_KEY", raising=False)
    calls, status = _run("/chat/stream")  # 无 key, 鉴权关闭
    assert status == 200
    assert calls == 1
