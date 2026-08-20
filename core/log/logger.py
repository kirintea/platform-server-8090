# -*- coding: utf-8 -*-
"""loguru 日志配置模块"""

import logging
import sys
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from loguru import logger

# ============================================================
# 常量
# ============================================================
_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NOISE_LOG_LIBS = (
    "openai", "httpcore", "httpx",
    "redis.asyncio", "asyncio",
    "uvicorn", "urllib3",
    "opentelemetry",  # OTel async generator 上下文清理噪声（task.cancel 时 detach token 报错）
)

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
        
        logger.opt(
            depth=6,
            exception=record.exc_info,
        ).log(level, record.getMessage())


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
    """
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    log_level = log_level.upper()
    if log_level not in valid_levels:
        raise ValueError(f"日志级别仅支持 {valid_levels}，传入：{log_level}")

    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True, parents=True)
    
    # 当前活跃日志文件（不带日期）
    log_file = str(log_path / "platform-server.log")

    # 清除所有已添加的 sink
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

    # 2. 文件（按天轮转，异步写盘）
    # loguru 会自动将轮转后的文件命名为 platform-server.log.YYYY-MM-DD
    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {process} | {name}:{function}:{line} | {message}",
        rotation="1 day",  # 每天轮转一次
        retention=f"{backup_count} days",  # 保留 N 天的日志
        encoding="utf-8",
        enqueue=True,  # 异步写盘
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


# # ============================================================
# # 测试代码
# # ============================================================
# if __name__ == "__main__":
#     setup_logging(log_dir="./logs", log_level="DEBUG")
    
#     logger.debug("这是 DEBUG 日志")
#     logger.info("这是 INFO 日志")
#     logger.warning("这是 WARNING 日志")
#     logger.error("这是 ERROR 日志")
    
#     # 测试大量日志
#     import time
#     print("\n开始写入测试日志...")
#     for i in range(100):
#         logger.info(f"测试日志第 {i+1} 条")
#         if i % 10 == 0:
#             print(f"已写入 {i+1} 条日志")
#         time.sleep(0.01)
    
#     print("日志写入完成，请检查 logs 目录")
#     print("当前文件: platform-server.log")
#     print("轮转后: platform-server.log.YYYY-MM-DD")
#     flush_log_on_exit()