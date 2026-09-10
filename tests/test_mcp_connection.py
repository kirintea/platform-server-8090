# -*- coding: utf-8 -*-
"""独立测试 MCP 连接 — 验证 mind-map-mcp 可达性与工具发现

用法:
    .venv/Scripts/python.exe tests/test_mcp_connection.py
"""

import asyncio
from agentscope.mcp import MCPClient, HttpMCPConfig


async def main():
    config = HttpMCPConfig(
        url="https://mcp.api-inference.modelscope.net/13750357b87b44/mcp",
        headers={},
        timeout=30.0,
    )

    # 无状态模式 — HTTP MCP 推荐方式，无需 connect()/close()
    client = MCPClient(mcp_config=config, name="mind-map-mcp", is_stateful=False)

    print(f"[1] 测试无状态模式连接 {config.url} ...")
    print(f"    is_stateful={client.is_stateful}")

    print("\n[2] 列出远端工具 ...")
    try:
        tools = await client.list_tools()
        print(f"[OK] 发现 {len(tools)} 个工具:")
        for t in tools:
            print(f"  - {t.name}: {getattr(t, 'description', '')[:80]}")
    except Exception as e:
        print(f"[FAIL] 列出工具失败: {e}")
        return

    print("\n[OK] 无状态模式测试通过，无需 close()")


if __name__ == "__main__":
    asyncio.run(main())
