# -*- coding: utf-8 -*-

"""AgentScope 对话智能体平台 — 启动入口

启动流程:
    1. load_dotenv() 加载 .env 敏感配置
    2. ConfigManager.load() 读取 YAML 配置 (APP_ENV 控制环境)
    3. setup_logging() 初始化日志
    4. TracingSetup.init() 初始化 OTel SDK (如果 otel.enabled)
    5. create_app(config) 创建 FastAPI 应用
    6. uvicorn.run() 启动服务

启动命令:
    APP_ENV=dev python main.py
    APP_ENV=prod python main.py

前端访问:
    http://localhost:8090/         — 对话界面
    http://localhost:8090/docs     — Swagger API 文档
"""

from __future__ import annotations

import atexit
import os

from loguru import logger

import uvicorn
from dotenv import load_dotenv

# ---------- 加载 .env (必须最先执行) ----------
# override=False: 已存在的环境变量（如 Docker 注入）优先于 .env 文件
load_dotenv(override=False)

from core.config import ConfigManager
from core.log.logger import setup_logging, flush_log_on_exit
from core.tracing import TracingSetup


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
atexit.register(flush_log_on_exit)

# ============================================================
# 2. 初始化 OTel 追踪 (必须在 Agent 创建之前)
# ============================================================
if config.otel.enabled:
    TracingSetup.init(config.otel)
    logger.info("OTel 追踪已初始化: {}", config.otel.endpoint)


# ============================================================
# 3. 创建 FastAPI 应用（通过 server.py 工厂函数）
# ============================================================
from server import create_app

app = create_app(config)


# ============================================================
# 4. 启动入口
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
