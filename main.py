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

import logging
import logging.handlers
import os
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

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
from core.redis_message_bus import RedisMessageBus
from core.session import SessionManager
from core.storage import PostgresStorage
from core.tracing import TracingSetup
from core.workspace import LocalWorkspaceManager

logger = logging.getLogger(__name__)


# ============================================================
# 0. 日志配置
# ============================================================

class BeijingTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """按北京时间 00:00 轮转日志，同时支持文件大小限制（>50MB 立即轮转）"""

    _TZ = None

    @classmethod
    def _get_tz(cls):
        if cls._TZ is None:
            try:
                import zoneinfo
                cls._TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
            except ImportError:
                # Python < 3.9 fallback（不会走到，3.12 必有）
                cls._TZ = None
        return cls._TZ

    def computeRollover(self, currentTime):
        """计算下一次轮转时间：北京时间次日 00:00"""
        tz = self._get_tz()
        if tz:
            now = datetime.fromtimestamp(currentTime, tz=tz)
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            return int(tomorrow.timestamp())
        # fallback: 使用父类逻辑（本地时间 midnight）
        return super().computeRollover(currentTime)

    def shouldRollover(self, record):
        """日期轮转 或 文件 >50MB 时轮转"""
        if super().shouldRollover(record):
            return True
        try:
            if self.stream and self.stream.tell() > 50 * 1024 * 1024:
                return True
        except Exception:
            pass
        return False


def setup_logging(log_dir: str, log_level: str, backup_count: int) -> None:
    """配置 root logger：控制台 + 文件双输出

    Args:
        log_dir: 日志文件目录
        log_level: 日志级别
        backup_count: 日志保留天数
    """
    # 创建日志目录
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "platform-server.log")

    # 日志格式
    fmt = "%(asctime)s | %(levelname)-7s | %(process)d | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 清除已有 handler（避免重复添加）
    root.handlers.clear()

    # 1. 控制台 handler → stderr
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 2. 文件 handler → logs/platform-server.log（北京时间 00:00 轮转）
    file_handler = BeijingTimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,  # 延迟创建文件（多 worker 安全）
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 3. 抑制第三方库的 DEBUG 噪音（OTel 已覆盖请求追踪，日志只需保留应用层信息）
    _NOISY_LOGGERS = [
        "openai",           # openai SDK — dump 整个请求 JSON（含 prompt / tool 定义）
        "httpcore",         # HTTP 连接底层细节（connect/send/receive 逐步日志）
        "httpx",            # HTTP 请求摘要（已有 OTel span 覆盖）
        "redis.asyncio",    # Redis 协议细节
        "asyncio",          # 事件循环内部日志
    ]
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "日志已配置: file=%s, level=%s, backup=%d",
        log_file, log_level, backup_count,
    )


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
    logger.info("OTel 追踪已初始化: %s", config.otel.endpoint)


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
    logger.info("消息总线已就绪 (RedisMessageBus: %s)", config.redis.url)

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
    logger.info("会话管理器已就绪 (Redis: %s)", config.redis.url)

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
        logger.info("启动模式: 多 Worker (%d workers)", workers)
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
