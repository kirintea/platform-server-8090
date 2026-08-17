# -*- coding: utf-8 -*-

"""PostgreSQL 连接与表结构检查

可单独执行: python health_check/check_postgres.py
"""

from __future__ import annotations

import sys
import asyncio

from utils import CheckReport, load_config


async def check_pg_connection(db_url: str, report: CheckReport):
    """检查 PostgreSQL 连接"""
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url, timeout=5)
        version = await conn.fetchval("SELECT version()")
        report.add("PostgreSQL 连接", True, version[:60] + "...")
        return conn
    except Exception as e:
        report.add("PostgreSQL 连接", False, db_url, str(e))
        return None


async def check_pg_tables(conn, report: CheckReport):
    """检查核心表是否存在"""
    expected_tables = ["users", "conversations", "sessions", "agents", "mcps", "skills", "schedules", "messages"]
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        existing = {r["table_name"] for r in rows}

        found = [t for t in expected_tables if t in existing]
        missing = [t for t in expected_tables if t not in existing]

        if not missing:
            report.add("PostgreSQL 表结构", True, f"{len(found)} 张核心表就绪")
        else:
            report.add(
                "PostgreSQL 表结构",
                False,
                f"已存在: {found}",
                f"缺失: {missing}",
            )
    except Exception as e:
        report.add("PostgreSQL 表结构", False, "", str(e))


async def check_pg_read_write(conn, report: CheckReport):
    """检查 PostgreSQL 读写能力"""
    try:
        # 测试查询
        result = await conn.fetchval("SELECT 1")
        if result == 1:
            report.add("PostgreSQL 读写", True, "SELECT 1 = OK")
        else:
            report.add("PostgreSQL 读写", False, "", f"SELECT 1 返回 {result}")
    except Exception as e:
        report.add("PostgreSQL 读写", False, "", str(e))


async def check_pg_pool(db_url: str, report: CheckReport):
    """检查 asyncpg 连接池"""
    try:
        import asyncpg
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3, timeout=5)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await pool.close()
        report.add("PostgreSQL 连接池", True, "min=1, max=3")
    except Exception as e:
        report.add("PostgreSQL 连接池", False, "", str(e))


async def _run_async():
    config = load_config()
    db_url = config.database.url if config else None

    report = CheckReport()

    if not db_url:
        report.add("PostgreSQL", False, "", "DATABASE_URL 未配置")
        return report.print_report()

    conn = await check_pg_connection(db_url, report)
    if conn:
        await check_pg_tables(conn, report)
        await check_pg_read_write(conn, report)
        await conn.close()
    await check_pg_pool(db_url, report)

    return report.print_report()


def run():
    """运行 PostgreSQL 检查"""
    return asyncio.run(_run_async())


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
