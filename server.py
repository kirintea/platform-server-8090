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

import os
import threading
import webbrowser

from loguru import logger

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.chat import router as chat_router
from api.agent import router as agent_router
from api.mcp import router as mcp_router
from api.skill import router as skill_router
from api.workspace import router as workspace_router
from api.schedule import router as schedule_router
from api.webui import router as webui_router
from core.chat_service import ChatService
from core.config import ConfigManager
from core.database import DatabaseManager
from core.redis_message_bus import RedisMessageBus
from core.session import SessionManager
from core.storage import PostgresStorage
from core.workspace import LocalWorkspaceManager


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
        # 创建数据库管理器（PostgreSQL 连接池 + 自动建表）
        db_mgr = DatabaseManager(config.database)
        await db_mgr.initialize()
        app.state.database_manager = db_mgr

        # 创建 PostgreSQL 存储层（Agent/Session/MCP/Skill/Message/Schedule CRUD）
        storage = PostgresStorage(db_mgr)
        app.state.storage = storage
        logger.info("PostgreSQL 存储层已就绪")

        # 创建消息总线（Redis 分布式实现，支持多实例无状态部署）
        message_bus = RedisMessageBus(config.redis.url)
        await message_bus.initialize()
        app.state.message_bus = message_bus
        logger.info("消息总线已就绪 (RedisMessageBus: {})", config.redis.url)

        # 创建会话管理器（Agent 实例按需创建）
        session_mgr = SessionManager(
            config=config,
            session_ttl=config.redis.session_ttl,
            max_sessions=100,
            storage=storage,
            db=db_mgr,
        )
        # 初始化 Redis 连接
        await session_mgr.initialize()
        app.state.session_manager = session_mgr
        logger.info("会话管理器已就绪 (Redis: {})", config.redis.url)

        # 创建工作区管理器（沙箱根目录，启动时自动创建）
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        workspace_mgr = LocalWorkspaceManager(base_dir=sandbox_dir)
        app.state.workspace_manager = workspace_mgr
        logger.info("工作区管理器已就绪 (沙箱目录: {})", sandbox_dir)

        # 创建 Chat 服务（Fire-and-Forget 模式）
        chat_service = ChatService(session_mgr, message_bus)
        app.state.chat_service = chat_service
        logger.info("Chat 服务已就绪")

        # 自动打开浏览器 (仅开发环境)
        if config.otel.environment == "development":
            def _open_browser():
                webbrowser.open(f"http://localhost:{config.server.port}/")
            threading.Thread(target=_open_browser, daemon=True).start()

        yield

        # --- 关闭 ---
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
        version="0.1.0",
        lifespan=lifespan,
    )

    # 静态文件（前端界面）
    _static_dir = os.path.join(os.getcwd(), "api", "static")

    @app.get("/")
    async def serve_frontend():
        """前端对话界面"""
        index_path = os.path.join(_static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not found", "path": index_path}

    # ============================================================
    # 3. 注册 API 路由
    # ============================================================
    app.include_router(chat_router, tags=["chat"])
    app.include_router(agent_router)
    app.include_router(mcp_router)
    app.include_router(skill_router)
    app.include_router(workspace_router)
    app.include_router(schedule_router)
    app.include_router(webui_router)  # WebUI 兼容层 (/webui/*)

    # 静态文件服务（CSS/JS 等）
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    return app
