# -*- coding: utf-8 -*-

"""loguru 日志配置模块

职责：
- 控制台 + 文件双输出
- 异步写盘（enqueue=True），不阻塞 asyncio 事件循环
- 北京时间 00:00 或文件 >50MB 自动轮转
- 桥接标准 logging → loguru，拦截第三方库 DEBUG 噪音
"""

import logging
import os
import sys
from datetime import datetime, timedelta

import zoneinfo
from loguru import logger

_TZ_SHANGHAI = zoneinfo.ZoneInfo("Asia/Shanghai")


# ============================================================
# rotation 回调（必须返回 bool）
# ============================================================

def _beijing_rotation(message, file) -> bool:
    """北京时间次日 00:00 或文件 >50MB 时轮转

    loguru 的 rotation callable 只检查返回值真假：
    - True  → 立即轮转
    - False → 继续写入
    返回 datetime/timedelta 等非 bool 类型会被当作 truthy，每条消息都轮转。
    """
    # 1. 文件大小轮转
    file.seek(0, 2)  # seek to end
    if file.tell() > 50 * 1024 * 1024:
        return True

    # 2. 时间轮转：文件最后修改时间是否在今天（北京时间）之前
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(file.name), tz=_TZ_SHANGHAI)
        today = datetime.now(_TZ_SHANGHAI).date()
        if mtime.date() < today:
            return True
    except (OSError, ValueError):
        pass

    return False


# ============================================================
# 标准 logging → loguru 桥接
# ============================================================

class _InterceptHandler(logging.Handler):
    """标准 logging → loguru 桥接（拦截第三方库日志）"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


# ============================================================
# 公开接口
# ============================================================

def setup_logging(log_dir: str, log_level: str, backup_count: int) -> None:
    """配置 loguru 日志：异步写盘 + 北京时区轮转 + 第三方库拦截

    Args:
        log_dir: 日志文件目录
        log_level: 日志级别 (DEBUG / INFO / WARNING / ERROR)
        backup_count: 日志保留天数
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "platform-server.log")

    # 移除 loguru 默认 handler
    logger.remove()

    # 1. 控制台输出 → stderr（带颜色）
    logger.add(
        sys.stderr,
        level=log_level.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <7}</level> | "
               "<cyan>{process}</cyan> | "
               "<cyan>{name}</cyan> | "
               "<level>{message}</level>",
        colorize=True,
    )

    # 2. 文件输出（异步写盘，北京时间 00:00 或 >50MB 轮转）
    logger.add(
        log_file,
        level=log_level.upper(),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {process} | {name} | {message}",
        rotation=_beijing_rotation,
        retention=f"{backup_count} days",
        encoding="utf-8",
        enqueue=True,  # 异步写盘，不阻塞事件循环
    )

    # 3. 桥接标准 logging → loguru（拦截第三方库 DEBUG 噪音）
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("openai", "httpcore", "httpx", "redis.asyncio", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logger.info("日志已配置: file={}, level={}, backup={}", log_file, log_level, backup_count)
