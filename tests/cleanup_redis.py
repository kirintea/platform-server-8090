# -*- coding: utf-8 -*-

"""Redis 会话数据清理工具

用法:
    python -m tests.cleanup_redis --stats              # 查看统计
    python -m tests.cleanup_redis --list               # 列出所有会话 key
    python -m tests.cleanup_redis --list --user u001   # 列出指定用户的会话
    python -m tests.cleanup_redis --user u001          # 删除指定用户的所有会话
    python -m tests.cleanup_redis --all                # 删除所有会话数据
    python -m tests.cleanup_redis --session sid001     # 删除指定 session_id（所有用户）
    python -m tests.cleanup_redis --dry-run --all      # 预览模式，不实际删除

连接配置:
    默认 redis://localhost:6379/0，可通过 --url 或环境变量 REDIS_URL 覆盖。
"""

import argparse
import asyncio
import os
import sys

import redis.asyncio as aioredis

DEFAULT_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
KEY_PREFIX = "agentscope:session:"


async def get_all_session_keys(r: aioredis.Redis, pattern: str = "*") -> list[str]:
    """扫描所有会话相关 key"""
    keys = []
    full_pattern = f"{KEY_PREFIX}{pattern}"
    async for key in r.scan_iter(match=full_pattern, count=200):
        keys.append(key.decode() if isinstance(key, bytes) else key)
    return sorted(keys)


async def show_stats(r: aioredis.Redis) -> None:
    """显示会话数据统计"""
    all_keys = await get_all_session_keys(r)
    meta_keys = [k for k in all_keys if k.endswith(":meta")]
    state_keys = [k for k in all_keys if not k.endswith(":meta")]

    # 按用户分组
    users: dict[str, int] = {}
    for k in state_keys:
        parts = k.replace(KEY_PREFIX, "").split(":")
        if len(parts) >= 2:
            uid = parts[0]
            users[uid] = users.get(uid, 0) + 1

    print(f"=== Redis 会话统计 ===")
    print(f"  总 key 数:     {len(all_keys)}")
    print(f"  AgentState:    {len(state_keys)}")
    print(f"  元数据 (meta): {len(meta_keys)}")
    print(f"  用户数:        {len(users)}")
    if users:
        print(f"\n  用户会话数:")
        for uid, count in sorted(users.items(), key=lambda x: -x[1]):
            print(f"    {uid}: {count} 个会话")


async def list_keys(r: aioredis.Redis, user_id: str | None = None) -> None:
    """列出会话 key"""
    pattern = f"{user_id}:*" if user_id else "*"
    keys = await get_all_session_keys(r, pattern)
    if not keys:
        print("没有找到匹配的会话 key")
        return
    print(f"共 {len(keys)} 个 key:")
    for k in keys:
        ttl = await r.ttl(k)
        ttl_str = f"  TTL={ttl}s" if ttl >= 0 else "  无过期"
        print(f"  {k}{ttl_str}")


async def delete_by_user(r: aioredis.Redis, user_id: str, dry_run: bool = False) -> None:
    """删除指定用户的所有会话"""
    pattern = f"{user_id}:*"
    keys = await get_all_session_keys(r, pattern)
    if not keys:
        print(f"用户 {user_id} 没有会话数据")
        return
    print(f"{'[预览] ' if dry_run else ''}删除用户 {user_id} 的 {len(keys)} 个 key:")
    for k in keys:
        print(f"  {k}")
        if not dry_run:
            await r.delete(k)
    if not dry_run:
        print(f"已删除 {len(keys)} 个 key")


async def delete_by_session(r: aioredis.Redis, session_id: str, dry_run: bool = False) -> None:
    """删除指定 session_id 的所有 key（跨用户）"""
    pattern = f"*:{session_id}"
    keys = await get_all_session_keys(r, pattern)
    # 也匹配 :session_id:meta
    pattern2 = f"*:{session_id}:*"
    keys2 = await get_all_session_keys(r, pattern2)
    all_keys = sorted(set(keys + keys2))
    if not all_keys:
        print(f"会话 {session_id} 没有数据")
        return
    print(f"{'[预览] ' if dry_run else ''}删除会话 {session_id} 的 {len(all_keys)} 个 key:")
    for k in all_keys:
        print(f"  {k}")
        if not dry_run:
            await r.delete(k)
    if not dry_run:
        print(f"已删除 {len(all_keys)} 个 key")


async def delete_all(r: aioredis.Redis, dry_run: bool = False) -> None:
    """删除所有会话数据"""
    keys = await get_all_session_keys(r)
    if not keys:
        print("没有会话数据")
        return
    print(f"{'[预览] ' if dry_run else ''}删除全部 {len(keys)} 个会话 key")
    if not dry_run:
        deleted = 0
        for k in keys:
            await r.delete(k)
            deleted += 1
        print(f"已删除 {deleted} 个 key")


async def main():
    parser = argparse.ArgumentParser(
        description="Redis 会话数据清理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Redis 连接 URL")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--list", action="store_true", help="列出会话 key")
    parser.add_argument("--user", type=str, help="指定用户 ID")
    parser.add_argument("--session", type=str, help="指定会话 ID")
    parser.add_argument("--all", action="store_true", help="删除所有会话数据")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际删除")

    args = parser.parse_args()

    r = aioredis.from_url(args.url, decode_responses=True)
    try:
        await r.ping()
    except Exception as e:
        print(f"Redis 连接失败: {e}")
        sys.exit(1)

    try:
        if args.stats:
            await show_stats(r)
        elif args.list:
            await list_keys(r, args.user)
        elif args.user and not args.all:
            await delete_by_user(r, args.user, args.dry_run)
        elif args.session:
            await delete_by_session(r, args.session, args.dry_run)
        elif args.all:
            await delete_all(r, args.dry_run)
        else:
            parser.print_help()
    finally:
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
