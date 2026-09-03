# -*- coding: utf-8 -*-

"""FastAPI 应用定义 — 路由 + 生命周期

职责：
    1. create_app(config) 工厂函数，接收已加载的配置
    2. lifespan 管理资源生命周期（DB/Redis/Session/Workspace/ChatService）
    3. 注册所有 API 路由 + 静态文件

由 main.py 调用：
    app = create_app(config)
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from pathlib import Path

from loguru import logger

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.chat import router as chat_router
from api.mcp import router as mcp_router
from api.skill import router as skill_router
from api.ws_chat import router as ws_chat_router
# from api.agent import router as agent_router
# from api.workspace import router as workspace_router
# from api.schedule import router as schedule_router
# from api.webui import router as webui_router

from core.chat_service import ChatService
from core.config import ConfigManager
from core.database import DatabaseManager
from core.redis_message_bus import RedisMessageBus
from core.session import SessionManager
from core.session_status import SessionStatusTracker
from core.storage import PostgresStorage
from core.workspace import LocalWorkspaceManager


# ------------------------------------------------------------
# 轻量级中间件（纯 ASGI 实现，不缓冲响应体，SSE / WebSocket 不受影响）
# ------------------------------------------------------------
def _security_headers_middleware(app):
    """始终开启的安全响应头（对开发 / 生产均无害）"""
    _SEC_HEADERS = {
        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
        b"content-security-policy": (
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data: blob:; "
            b"connect-src 'self' ws: wss:; "
            b"font-src 'self' data:; "
            b"object-src 'none'; "
            b"base-uri 'self'"
        ),
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
    }

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        sent_start = False

        async def send_wrapper(message):
            nonlocal sent_start
            if message["type"] == "http.response.start" and not sent_start:
                sent_start = True
                headers = list(message.get("headers", []))
                existing = {k.lower(): v for k, v in headers}
                for key, val in _SEC_HEADERS.items():
                    if key not in existing:
                        headers.append((key, val))
                message["headers"] = headers
            await send(message)

        await app(scope, receive, send_wrapper)

    return middleware


def _api_key_auth_middleware(app, *, auth_required: bool = False, api_key: str = ""):
    """全局 API-Key 鉴权（默认关闭，仅 auth_required=True 或 AUTH_REQUIRED=true 时生效）

    放行路径：/webui、/webui/、/health、/docs、/openapi.json、/redoc
    以及所有以 /webui/ 开头的路径。其余路径需携带正确的 X-API-Key 请求头。

    优先级：环境变量 AUTH_REQUIRED/API_KEY > YAML 配置 auth.required/auth.api_key。
    """
    _PUBLIC_EXACT = {
        "/webui", "/webui/", "/health", "/docs",
        "/openapi.json", "/redoc",
    }

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # 环境变量优先，YAML 配置兜底
        env_required = os.environ.get("AUTH_REQUIRED", "").lower()
        if env_required == "true":
            is_auth_required = True
        elif env_required == "false":
            is_auth_required = False
        else:
            is_auth_required = auth_required

        if not is_auth_required:
            await app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _PUBLIC_EXACT or path.startswith("/webui/"):
            await app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(b"x-api-key", b"").decode("latin-1")
        expected = os.environ.get("API_KEY") or api_key
        if expected and provided == expected:
            # 将已验证的 API key 存入 scope state，供下游端点使用
            scope.setdefault("state", {})["api_key"] = provided
            await app(scope, receive, send)
            return

        body = json.dumps(
            {"error": "Unauthorized", "hint": "Missing or invalid X-API-Key"}
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})

    return middleware


def create_app(config) -> FastAPI:
    """创建 FastAPI 应用实例

    Args:
        config: ConfigManager.load() 返回的配置对象

    Returns:
        配置完成的 FastAPI 实例
    """

    # ============================================================
    # 1. FastAPI 生命周期
    # ============================================================
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """服务启动 / 关闭时的资源管理"""
        # --- 启动 ---
        app.state.config = config

        # 创建数据库管理器（PostgreSQL 连接池 + 自动建表）
        db_mgr = DatabaseManager(config.database)
        await db_mgr.initialize()
        app.state.database_manager = db_mgr

        # 应用配置对象（供 /context 等端点读取 request.app.state.config）
        app.state.config = config

        # 创建 PostgreSQL 存储层（Agent/Session/MCP/Skill/Message/Schedule CRUD）
        storage = PostgresStorage(db_mgr)
        app.state.storage = storage
        logger.info("PostgreSQL 存储层已就绪")

        # 创建消息总线（Redis 分布式实现，支持多实例无状态部署）
        # 注意：RedisMessageBus 构造函数当前仅接受 redis_url（见 core/redis_message_bus.py:36），
        # key_prefix / session_ttl 由 SessionManager 使用，此处无需传递。
        # Redis 不可达时退避重试；仍失败则降级为 None，应用继续启动（/health 仍可用）。
        message_bus = RedisMessageBus(config.redis.url)
        _bus_ok = False
        for _attempt in range(3):
            try:
                await message_bus.initialize()
                _bus_ok = True
                break
            except Exception as _bus_err:  # noqa: BLE001
                if _attempt < 2:
                    logger.warning(
                        "Redis 消息总线初始化失败（第 {} 次），2s 后重试: {}",
                        _attempt + 1, _bus_err,
                    )
                    await asyncio.sleep(2)
                else:
                    logger.warning(
                        "Redis 消息总线不可用，应用降级运行"
                        "（/health 仍可用，对话/会话的 Redis 依赖将不可用）: {}",
                        _bus_err,
                    )
        app.state.message_bus = message_bus if _bus_ok else None
        if _bus_ok:
            logger.info("消息总线已就绪 (RedisMessageBus: {})", config.redis.url)

        # 创建会话管理器（Agent 实例按需创建）
        session_mgr = SessionManager(
            config=config,
            session_ttl=config.redis.session_ttl,
            max_sessions=getattr(config.server, "max_sessions", 100),
            storage=storage,
            db=db_mgr,
        )
        # 初始化 Redis 连接（与消息总线一致：失败降级而非硬崩溃，app 仍服务 /health）
        try:
            await session_mgr.initialize()
            logger.info("会话管理器已就绪 (Redis: {})", config.redis.url)
        except Exception as _sess_err:  # noqa: BLE001
            logger.warning(
                "SessionManager Redis 初始化失败，降级运行（会话持久化不可用）: {}",
                _sess_err,
            )
        app.state.session_manager = session_mgr

        # 创建工作区管理器（沙箱根目录，启动时自动创建）
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        workspace_mgr = LocalWorkspaceManager(base_dir=sandbox_dir)
        app.state.workspace_manager = workspace_mgr
        logger.info("工作区管理器已就绪 (沙箱目录: {})", sandbox_dir)

        # 创建 Chat 服务（Fire-and-Forget 模式）
        # 先初始化 SessionStatusTracker（多端并发状态广播）
        status_tracker = None
        if _bus_ok and message_bus._redis:
            status_tracker = SessionStatusTracker(message_bus._redis)
            app.state.session_status_tracker = status_tracker
            logger.info("会话状态跟踪器已就绪（多端并发模式）")
        else:
            app.state.session_status_tracker = None
            logger.info("会话状态跟踪器未启用（Redis 不可用）")

        chat_service = ChatService(session_mgr, message_bus, status_tracker)
        app.state.chat_service = chat_service
        logger.info("Chat 服务已就绪")

        # 自动打开浏览器 (仅开发环境)
        if config.otel.environment == "development":
            def _open_browser():
                webbrowser.open(f"http://localhost:{config.server.port}/")
            threading.Thread(target=_open_browser, daemon=True).start()

        yield

        # --- 关闭 ---
        # 先排空后台持久化任务（DRAIN：await gather / 取消），避免关闭时丢失在途对话写入。
        # shutdown_persist_tasks 由 api/chat.py / api/ws_chat.py 提供（同步或异步版本皆可，
        # 用 iscoroutinefunction 兼容；未定义时跳过，不影响关闭流程）。
        try:
            from api.chat import shutdown_persist_tasks as _chat_shutdown  # type: ignore
        except Exception:  # noqa: BLE001
            _chat_shutdown = None
        try:
            from api.ws_chat import shutdown_persist_tasks as _ws_shutdown  # type: ignore
        except Exception:  # noqa: BLE001
            _ws_shutdown = None
        for _fn in (_chat_shutdown, _ws_shutdown):
            if _fn is None:
                continue
            try:
                if asyncio.iscoroutinefunction(_fn):
                    await _fn()
                else:
                    _fn()
            except Exception as _persist_err:  # noqa: BLE001
                logger.warning("关闭时排空持久化任务失败（已忽略）: {}", _persist_err)

        await session_mgr.shutdown()
        logger.info("会话管理器已关闭")

        await message_bus.aclose()
        logger.info("消息总线已关闭")

        await db_mgr.shutdown()
        logger.info("数据库连接池已关闭")

        if config.otel.enabled:
            from core.tracing import TracingSetup
            TracingSetup.shutdown()
            logger.info("OTel 追踪已关闭")

    # ============================================================
    # 2. 创建 FastAPI 应用
    # ============================================================
    app = FastAPI(
        title="AgentScope Platform Server",
        description="基于 AgentScope 2.0.5 的对话智能体平台",
        version="0.1.3",
        lifespan=lifespan,
    )

    # 仓库根目录（server.py 位于仓库根，故 parents[0] 即根目录）
    # 以绝对路径替代 os.getcwd()，避免进程 cwd 变化时静态 / webui 路径错位。
    _repo_root = str(Path(__file__).resolve().parents[0])

    # 静态文件（前端界面）
    _static_dir = os.path.join(_repo_root, "api", "static")

    @app.get("/")
    async def serve_frontend():
        """前端对话界面（旧版）"""
        index_path = os.path.join(_static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not found", "path": index_path}

    @app.get("/webui")
    @app.get("/webui/{path:path}")
    async def serve_webui(path: str = ""):
        """新版 WebUI 入口"""
        webui_dir = os.path.join(_repo_root, "webui", "dist")
        if not os.path.isdir(webui_dir):
            return {"error": "WebUI not built", "hint": "cd webui && npm run build"}
        # 目标文件：默认 index.html，否则拼接 path
        file_path = os.path.join(webui_dir, path) if path else os.path.join(webui_dir, "index.html")
        # 路径穿越防护：解析真实路径并校验仍位于 webui_dir 之内
        real_root = os.path.realpath(webui_dir)
        real_file = os.path.realpath(file_path)
        if not real_file.startswith(real_root + os.sep):
            return {"error": "Forbidden path", "path": path}
        if os.path.exists(real_file) and os.path.isfile(real_file):
            return FileResponse(real_file)
        # SPA fallback: 非文件路径都返回 index.html
        return FileResponse(os.path.join(webui_dir, "index.html"))

    # ============================================================
    # 3. 注册 API 路由
    # ============================================================
    app.include_router(chat_router, tags=["chat"])
    app.include_router(mcp_router)
    app.include_router(skill_router)
    app.include_router(ws_chat_router, tags=["websocket"])

    # app.include_router(agent_router)  # 暂未使用
    # app.include_router(workspace_router)
    # app.include_router(schedule_router)  # 暂未使用
    # app.include_router(webui_router)  # WebUI 兼容层 (/webui/*)

    # 静态文件服务（CSS/JS 等）
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # ------------------------------------------------------------
    # 中间件（纯 ASGI 包裹，置于最终返回前）
    # 顺序：API-Key 鉴权在内，安全响应头在外（对所有响应生效，含 401）
    # ------------------------------------------------------------
    # 速率限制（在 auth 之前，基于 API Key 或 user_id 限流）
    from middleware.rate_limit import RateLimitMiddleware
    rl_config = getattr(config.middleware, "rate_limit", None)
    if rl_config and rl_config.enabled:
        app = RateLimitMiddleware(
            app,
            requests_per_minute=rl_config.requests_per_minute,
            enabled=True,
        )

    app = _api_key_auth_middleware(
        app,
        auth_required=getattr(config.auth, "required", False),
        api_key=getattr(config.auth, "api_key", ""),
    )
    app = _security_headers_middleware(app)

    return app
