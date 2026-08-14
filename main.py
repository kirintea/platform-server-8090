# -*- coding: utf-8 -*-

"""AgentScope 对话智能体平台 — FastAPI 入口

启动流程:
    1. load_dotenv() 加载 .env 敏感配置
    2. ConfigManager.load() 读取 YAML 配置 (APP_ENV 控制环境)
    3. TracingSetup.init() 初始化 OTel SDK (如果 otel.enabled)
    4. 创建 SessionManager 管理多用户会话
    5. 创建 DatabaseManager 管理 PostgreSQL 连接池
    6. FastAPI lifespan 管理资源生命周期
    7. 注册 API 路由 + 前端静态文件, uvicorn 启动

启动命令:
    APP_ENV=dev python main.py
    APP_ENV=prod python main.py

前端访问:
    http://localhost:8090/         — 对话界面
    http://localhost:8090/docs     — Swagger API 文档
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager

from loguru import logger

import uvicorn
from dotenv import load_dotenv

# ---------- 加载 .env (必须最先执行) ----------
load_dotenv(override=True)

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
from core.log.logger import setup_logging
from core.redis_message_bus import RedisMessageBus
from core.session import SessionManager
from core.storage import PostgresStorage
from core.tracing import TracingSetup
from core.workspace import LocalWorkspaceManager


# ============================================================
# 1. 加载配置
# ============================================================
config = ConfigManager.get_instance().load()

# 初始化日志（必须在其他模块 logger 输出之前）
setup_logging(
    log_dir=config.server.log_dir,
    log_level=config.server.log_level,
    backup_count=config.server.log_backup_count,
)

# ============================================================
# 2. 初始化 OTel 追踪 (必须在 Agent 创建之前)
# ============================================================
if config.otel.enabled:
    TracingSetup.init(config.otel)
    logger.info("OTel 追踪已初始化: {}", config.otel.endpoint)


# ============================================================
# 3. FastAPI 生命周期
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

    # 创建工作区管理器
    workspace_mgr = LocalWorkspaceManager(base_dir="./workspaces")
    app.state.workspace_manager = workspace_mgr
    logger.info("工作区管理器已就绪")

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
        TracingSetup.shutdown()
        logger.info("OTel 追踪已关闭")


# ============================================================
# 4. 创建 FastAPI 应用
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


# 注册 API 路由
app.include_router(chat_router, tags=["chat"])
app.include_router(agent_router)
app.include_router(mcp_router)
app.include_router(skill_router)
app.include_router(workspace_router)
app.include_router(schedule_router)
app.include_router(webui_router)  # WebUI 兼容层 (/webui/*)

# 静态文件服务（CSS/JS 等）
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ============================================================
# 5. 启动入口
# ============================================================
if __name__ == "__main__":
    workers = config.server.workers
    if workers == 0:
        workers = os.cpu_count() or 1

    log_level = config.server.log_level.lower()

    if workers > 1:
        # 多 worker 必须用 import string（uvicorn 子进程会重新 import 模块）
        logger.info("启动模式: 多 Worker ({} workers)", workers)
        uvicorn.run(
            "main:app",
            host=config.server.host,
            port=config.server.port,
            workers=workers,
            log_level=log_level,
        )
    else:
        # 单 worker: 传 app 对象，开发模式
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=log_level,
        )
