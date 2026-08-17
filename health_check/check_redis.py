# -*- coding: utf-8 -*-

"""Redis 连接与读写检查

可单独执行: python health_check/check_redis.py
"""

from __future__ import annotations

import sys
import time

from utils import CheckReport, load_config


def check_redis_connection(redis_url: str, report: CheckReport):
    """检查 Redis 连接"""
    try:
        import redis
        r = redis.from_url(redis_url, socket_timeout=3)
        info = r.info("server")
        version = info.get("redis_version", "unknown")
        report.add("Redis 连接", True, f"{redis_url} | v{version}")
    except Exception as e:
        report.add("Redis 连接", False, redis_url, str(e))
        return None
    return r


def check_redis_read_write(r, report: CheckReport):
    """检查 Redis 读写能力"""
    test_key = "_health_check_test"
    test_value = f"ping_{int(time.time())}"

    try:
        # 写入
        r.set(test_key, test_value, ex=10)
        # 读取
        got = r.get(test_key)
        if got and got.decode() == test_value:
            report.add("Redis 读写", True, "SET/GET 一致")
        else:
            report.add("Redis 读写", False, "", f"期望 {test_value}，实际 {got}")
        # 清理
        r.delete(test_key)
    except Exception as e:
        report.add("Redis 读写", False, "", str(e))


def check_redis_ttl(r, report: CheckReport):
    """检查 Redis TTL 功能"""
    test_key = "_health_check_ttl"
    try:
        r.set(test_key, "ttl_test", ex=60)
        ttl = r.ttl(test_key)
        r.delete(test_key)
        if ttl > 0:
            report.add("Redis TTL", True, f"TTL={ttl}s")
        else:
            report.add("Redis TTL", False, "", f"TTL={ttl}，期望 >0")
    except Exception as e:
        report.add("Redis TTL", False, "", str(e))


def run():
    """运行 Redis 检查"""
    config = load_config()
    redis_url = config.redis.url if config else "redis://localhost:6379/0"

    report = CheckReport()
    r = check_redis_connection(redis_url, report)
    if r:
        check_redis_read_write(r, report)
        check_redis_ttl(r, report)
    return report.print_report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
