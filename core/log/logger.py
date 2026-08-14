# -*- coding: utf-8 -*-
"""loguru 日志配置模块

职责：
- 控制台 + 文件双输出
- 异步写盘（enqueue=True），不阻塞 asyncio 事件循环
- 北京时间次日 00:00 或文件 >50MB 自动轮转
- 桥接标准 logging → loguru，拦截第三方库 DEBUG 噪音

注意：
- 多进程部署时每个进程必须使用独立日志文件，否则日志错乱
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from loguru import logger

# ============================================================
# 常量
# ============================================================
_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOG_ROTATE_SIZE = 50 * 1024 * 1024  # 50MB
_NOISE_LOG_LIBS = (
    "openai", "httpcore", "httpx",
    "redis.asyncio", "asyncio",
    "uvicorn", "urllib3",
)


# ============================================================
# 轮转回调
# ============================================================
def _next_beijing_midnight() -> datetime:
    """计算下一个北京时间 00:00"""
    now = datetime.now(_TZ_SHANGHAI)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if midnight <= now:
        midnight += timedelta(days=1)
    return midnight


def _beijing_rotation(message, file) -> bool | datetime:
    """北京时间次日 00:00 或文件 >50MB 时轮转

    返回值语义（loguru 约定）：
    - datetime → 轮转时刻，到达该时间后轮转并自动计算下一个北京时间零点
    - True     → 立即轮转（文件超限）
    - False    → 不轮转
    """
    try:
        file.seek(0, 2)
        if file.tell() > _LOG_ROTATE_SIZE:
            return True
    except (OSError, ValueError):
        pass

    return _next_beijing_midnight()


# ============================================================
# logging 桥接器
# ============================================================
class _InterceptHandler(logging.Handler):
    """标准 logging → loguru 桥接"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info, diagnose=False).log(
            level, record.getMessage()
        )


# ============================================================
# 初始化入口
# ============================================================
def setup_logging(
    log_dir: str = "./logs",
    log_level: str = "DEBUG",
    backup_count: int = 30,
    dev_mode: Optional[bool] = None,
) -> None:
    """配置 loguru 日志

    Args:
        log_dir: 日志文件目录
        log_level: DEBUG / INFO / WARNING / ERROR
        backup_count: 日志保留天数
        dev_mode: None=自动判断, True=强制彩色, False=强制无色

    Raises:
        ValueError: log_level 不合法
    """
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    log_level = log_level.upper()
    if log_level not in valid_levels:
        raise ValueError(f"日志级别仅支持 {valid_levels}，传入：{log_level}")

    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True, parents=True)
    log_file = str(log_path / "platform-server.log")

    logger.remove()

    # Windows 旧终端不支持 ANSI 彩色
    colorize = sys.platform != "win32" if dev_mode is None else (dev_mode and sys.platform != "win32")

    # 1. 控制台
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{process}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=colorize,
        diagnose=False,
    )

    # 2. 文件（异步写盘）
    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {process} | {name}:{function}:{line} | {message}",
        rotation=_beijing_rotation,
        retention=f"{backup_count} days",
        encoding="utf-8",
        enqueue=True,
        colorize=False,
        diagnose=False,
    )

    # 3. 桥接标准 logging
    _reset_origin_logging()
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)

    logger.info(
        "日志初始化完成 | 文件:{} | 级别:{} | 保留:{}天 | 彩色:{}",
        log_file, log_level, backup_count, colorize,
    )


def _reset_origin_logging() -> None:
    """清空原生 logging handler，防止重复打印"""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    for lib in _NOISE_LOG_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)


def flush_log_on_exit() -> None:
    """程序退出时刷新异步队列，防止日志丢失"""
    logger.complete()
