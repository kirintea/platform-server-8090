# -*- coding: utf-8 -*-

"""命令守卫 enforcement 边界测试

补充 middleware/command_guard.py 的真实拦截边界覆盖（test_command_guard.py
只覆盖了 `_is_blocked`）。这里测试：
1. `_extract_command` 的 JSON 字段优先级与健壮解析
2. `on_acting` 的真实拦截/放行行为（next_handler 是否真的被短路）
3. allowlist 模式的语义回归（高危险命令必须被拦截，安全命令必须放行）

注意：本文件依赖 agentscope（agentscope.tool.ToolResponse / agentscope.message），
在缺少 agentscope 的环境中无法 import，属预期（与现有 test_command_guard.py 一致）。
"""

import asyncio
import json
import types

from middleware.command_guard import CommandGuardMiddleware

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse


def _make_tool_call(name: str, tool_input) -> object:
    """构造一个最小可用的 tool_call 对象（仅需 .name / .id / .input）。"""
    return types.SimpleNamespace(name=name, id="call_1", input=tool_input)


async def _collect(guard, call_log, tool_call):
    """收集 on_acting 的全部产出项（避免 async comprehension 需 async 上下文）。"""
    return [
        item
        async for item in guard.on_acting(
            agent=None,
            input_kwargs={"tool_call": tool_call},
            next_handler=_capturing_next_handler(call_log),
        )
    ]


def _capturing_next_handler(call_log: list):
    """返回一个 async generator，记录是否被调用，并产出一条通过信号。"""

    async def handler(**kwargs):
        call_log.append(kwargs)
        yield ToolResponse(
            id="captured",
            content=[TextBlock(text="ok")],
            state=ToolResultState.SUCCESS,
        )

    return handler


# ============================================================
# _extract_command 解析
# ============================================================

def test_extract_command_priority_command_content_path_file_path():
    # command 优先级最高
    assert CommandGuardMiddleware._extract_command(
        json.dumps({"command": "rm -rf /", "content": "echo hi", "path": "/x", "file_path": "/y"})
    ) == "rm -rf /"
    # 无 command 时取 content
    assert CommandGuardMiddleware._extract_command(
        json.dumps({"content": "echo hi", "path": "/x", "file_path": "/y"})
    ) == "echo hi"
    # 无 command/content 时取 path
    assert CommandGuardMiddleware._extract_command(
        json.dumps({"path": "/x", "file_path": "/y"})
    ) == "/x"
    # 仅 file_path
    assert CommandGuardMiddleware._extract_command(
        json.dumps({"file_path": "/y"})
    ) == "/y"


def test_extract_command_non_json_passthrough():
    # 非 JSON 字符串原样返回
    assert CommandGuardMiddleware._extract_command("echo hello world") == "echo hello world"
    # 看起来像 JSON 但不是 dict（标量）无法提取命令, 安全返回 "" (不崩溃)
    assert CommandGuardMiddleware._extract_command("123") == ""


def test_extract_command_malformed_or_non_str_does_not_raise():
    # 这些输入都不应抛异常，且 falsy 输入应返回 ""
    assert CommandGuardMiddleware._extract_command(None) == ""
    assert CommandGuardMiddleware._extract_command("") == ""
    assert CommandGuardMiddleware._extract_command(0) == ""

    # 非 str 且非 falsy：不应抛异常（按原样返回）
    non_str_inputs = [123, b"some-bytes", {"command": "ls"}, ["a", "b"], 3.14, object()]
    for value in non_str_inputs:
        try:
            result = CommandGuardMiddleware._extract_command(value)
        except Exception as exc:  # pragma: no cover
            raise AssertionError(f"_extract_command 对非 str 输入抛异常: {value!r} -> {exc!r}")
        # 结果不为 None（至少安全返回了某值）
        assert result is not None


# ============================================================
# on_acting 真实拦截 / 放行
# ============================================================

def test_on_acting_blocked_command_denies_and_short_circuits_next_handler():
    guard = CommandGuardMiddleware(mode="blocklist", enabled=True)
    call_log: list = []
    tool_call = _make_tool_call("Bash", json.dumps({"command": "rm -rf /"}))

    results = asyncio.run(_collect(guard, call_log, tool_call))

    # next_handler 绝不能被调用（命令被拦截，短路）
    assert call_log == [], "被拦截的命令不应执行 next_handler"
    assert len(results) == 1
    assert isinstance(results[0], ToolResponse)
    assert results[0].state == ToolResultState.DENIED
    assert results[0].id == tool_call.id


def test_on_acting_allowed_command_passes_through_to_next_handler():
    guard = CommandGuardMiddleware(mode="blocklist", enabled=True)
    call_log: list = []
    tool_call = _make_tool_call("Bash", json.dumps({"command": "ls -la"}))

    results = asyncio.run(_collect(guard, call_log, tool_call))

    # 放行命令必须真的执行 next_handler
    assert len(call_log) == 1, "放行的命令应当执行 next_handler"
    assert len(results) == 1
    assert isinstance(results[0], ToolResponse)
    assert results[0].state == ToolResultState.SUCCESS
    assert results[0].id == "captured"


def test_on_acting_disabled_passthrough():
    guard = CommandGuardMiddleware(enabled=False)
    call_log: list = []
    tool_call = _make_tool_call("Bash", json.dumps({"command": "rm -rf /"}))

    results = asyncio.run(_collect(guard, call_log, tool_call))

    assert len(call_log) == 1, "disabled 守卫必须透传"
    assert len(results) == 1
    assert results[0].state == ToolResultState.SUCCESS


# ============================================================
# allowlist 语义回归（必须断言「正确」行为，用于捕获反转 bug）
# ============================================================

def test_allowlist_blocks_high_risk_command():
    # 正确语义：allowlist 模式下，高危险命令（rm -rf /）必须被拦截 (DENIED)。
    # 当前代码存在「反转」bug（allowlist 下 matched=True 反而放行），
    # 本测试预期会捕获该 bug —— 修复后应通过。
    guard = CommandGuardMiddleware(mode="allowlist", enabled=True)
    call_log: list = []
    tool_call = _make_tool_call("Bash", json.dumps({"command": "rm -rf /"}))

    results = asyncio.run(_collect(guard, call_log, tool_call))

    assert call_log == [], "allowlist 下 rm -rf / 不应执行 next_handler"
    assert len(results) == 1
    assert results[0].state == ToolResultState.DENIED


def test_allowlist_allows_safe_command():
    # 正确语义：allowlist 模式下，安全命令（ls -la）必须被放行。
    guard = CommandGuardMiddleware(mode="allowlist", enabled=True, rules=["ls*"])
    call_log: list = []
    tool_call = _make_tool_call("Bash", json.dumps({"command": "ls -la"}))

    results = asyncio.run(_collect(guard, call_log, tool_call))

    assert len(call_log) == 1, "allowlist 下命中白名单的安全命令应当放行"
    assert len(results) == 1
    assert results[0].state == ToolResultState.SUCCESS
