# -*- coding: utf-8 -*-

"""沙箱路径守卫中间件

拦截文件操作类工具的路径参数，校验是否在沙箱目录内。
防止 Agent 通过 Read/Write/Edit/Glob/Grep/Bash 等工具访问沙箱外的文件。

与 tool_guard / command_guard 并列:
  - tool_guard   → 工具名级 (Bash, Read, Write ...)
  - command_guard → 命令内容级 (rm -rf, curl|bash ...)
  - path_guard   → 路径级 (限制文件操作在沙箱目录内)

配置 (schemas.py):
    agent:
      sandbox_dir: "workspaces"

工具路径参数映射:
  - Read:    file_path
  - Write:   file_path
  - Edit:    file_path
  - Glob:    path
  - Grep:    path
  - Bash:    command（解析命令中的文件路径）
  - 其他:    放行（无路径概念的工具不受限）
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from loguru import logger

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

# 工具名 → 路径参数字段
# 注意：字段名必须与 AgentScope 工具 input_schema 中的 property name 一致
_TOOL_PATH_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}

# Bash 命令中需要检查路径的子命令（文件操作类）
_BASH_FILE_CMDS: set[str] = {
    "cat", "head", "tail", "less", "more",
    "ls", "dir", "tree", "find", "locate",
    "cp", "copy", "mv", "move", "rm", "del", "rmdir",
    "mkdir", "md", "touch", "ln",
    "chmod", "chown", "chgrp",
    "grep", "egrep", "fgrep", "rg",
    "sed", "awk", "tee",
    "wc", "sort", "uniq", "cut", "tr",
    "diff", "cmp", "comm",
    "tar", "zip", "unzip", "gzip", "gunzip",
    "dd", "truncate",
    "stat", "file", "du", "df",
    "readlink", "realpath",
    "source", ".",
}

# 重定向符号后跟的路径也需要检查（覆盖 > / >> / < / << / <<< / 2> / 1>> 等）
_REDIRECT_RE = re.compile(r'\d*[<>]{1,3}\s*([^\s;|&]+)')


class PathGuardMiddleware(MiddlewareBase):
    """沙箱路径守卫 — 限制文件操作在沙箱目录内"""

    def __init__(self, sandbox_dir: str) -> None:
        super().__init__()
        # 保留原始路径用于日志，解析后的绝对路径用于校验
        self._sandbox_dir = sandbox_dir
        self._sandbox_resolved = Path(sandbox_dir).resolve()
        logger.info(
            "PathGuard 已初始化: sandbox=%s (resolved=%s)",
            sandbox_dir,
            self._sandbox_resolved,
        )

    # ------------------------------------------------------------------
    # 系统提示词注入 — 让 Agent 第一步就知道沙箱路径
    # ------------------------------------------------------------------

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """在系统提示词末尾追加沙箱路径信息"""
        sandbox_hint = (
            f"\n\n## 沙箱路径（必读）\n"
            f"所有文件操作必须在以下沙箱目录内：\n"
            f"```\n{self._sandbox_resolved}\n```\n"
            f"创建、读取、编辑文件时，必须使用此目录的绝对路径。"
            f"禁止访问此路径之外的任何文件。"
        )
        return current_prompt + sandbox_hint

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行 — 检查路径参数是否在沙箱内"""
        tool_call = input_kwargs["tool_call"]
        tool_name: str = tool_call.name

        # --- Bash/PowerShell: 解析命令中的文件路径 ---
        if tool_name in ("Bash", "PowerShell"):
            ok, bad_path = self._check_bash_command(tool_call.input)
            if ok:
                async for item in next_handler(**input_kwargs):
                    yield item
                return
            logger.warning(
                "PathGuard 拦截 %s: 命令中路径 '%s' 超出沙箱范围 (sandbox=%s)",
                tool_name, bad_path, self._sandbox_resolved,
            )
            yield ToolResponse(
                id=tool_call.id,
                content=[TextBlock(
                    text=(
                        f"[PathGuard] 命令被拒绝: 包含沙箱外路径 '{bad_path}'。"
                        f"所有文件操作必须在沙箱目录内: {self._sandbox_dir}"
                    ),
                )],
                state=ToolResultState.DENIED,
            )
            return

        # --- Read/Write/Edit/Glob/Grep: 检查路径参数 ---
        path_key = _TOOL_PATH_KEYS.get(tool_name)
        if path_key is None:
            # 无路径映射的工具直接放行
            async for item in next_handler(**input_kwargs):
                yield item
            return

        path_value = self._extract_path(tool_call.input, path_key)
        if not path_value:
            # 无路径值（如 Glob 不传 path 参数时默认 "."），放行
            async for item in next_handler(**input_kwargs):
                yield item
            return

        if self._is_within_sandbox(path_value):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # 路径越界 — 拦截
        logger.warning(
            "PathGuard 拦截 %s: 路径 '%s' 超出沙箱范围 (sandbox=%s)",
            tool_name, path_value, self._sandbox_resolved,
        )
        yield ToolResponse(
            id=tool_call.id,
            content=[TextBlock(
                text=(
                    f"[PathGuard] 路径被拒绝: '{path_value}' 超出沙箱范围。"
                    f"所有文件操作必须在沙箱目录内: {self._sandbox_dir}"
                ),
            )],
            state=ToolResultState.DENIED,
        )

    # ------------------------------------------------------------------
    # Bash 命令路径检查
    # ------------------------------------------------------------------

    def _check_bash_command(
        self,
        tool_input: str,
    ) -> tuple[bool, str | None]:
        """检查 Bash 命令中是否包含沙箱外路径

        Args:
            tool_input: 工具输入 JSON 字符串

        Returns:
            (True, None) 表示通过，(False, 问题路径) 表示拦截
        """
        command = self._extract_command(tool_input)
        if not command:
            return True, None

        # 如果是相对路径且不包含 .. 则视为沙箱内
        # 检查 cd 到沙箱外的目录
        paths = self._extract_paths_from_command(command)

        for p in paths:
            if not self._is_within_sandbox(p):
                return False, p

        return True, None

    def _extract_paths_from_command(self, command: str) -> list[str]:
        """从 Bash 命令中提取文件路径

        策略:
        1. 用 shlex 分词
        2. 识别文件操作子命令，提取其后的路径参数
        3. 检查重定向目标路径
        """
        paths: list[str] = []

        # 1. 提取重定向目标
        for m in _REDIRECT_RE.finditer(command):
            redir_path = m.group(1)
            if redir_path and redir_path != "/dev/null":
                paths.append(redir_path)

        # 2. 分词并提取子命令参数
        try:
            tokens = shlex.split(command)
        except ValueError:
            # shlex 解析失败（如未闭合的引号），退回简单分割
            tokens = command.split()

        i = 0
        while i < len(tokens):
            tok = tokens[i]

            # 跳过选项标志
            if tok.startswith("-") and len(tok) > 1:
                # 某些选项带参数（如 sed -i 's/x/y/' file）
                # -i, -o, --output, --input 等后面可能跟路径
                if tok in ("-i", "-o", "--input", "--output") and i + 1 < len(tokens):
                    paths.append(tokens[i + 1])
                    i += 2
                    continue
                i += 1
                continue

            # 识别文件操作子命令
            base_cmd = os.path.basename(tok)
            if base_cmd in _BASH_FILE_CMDS:
                # 子命令之后的所有非选项 token 都可能是路径
                # （跳过选项标志和它们的参数值）
                i += 1
                skip_next = False
                while i < len(tokens):
                    arg = tokens[i]
                    if skip_next:
                        skip_next = False
                        i += 1
                        continue
                    if arg.startswith("-") and len(arg) > 1:
                        # 带参数的选项
                        # 注意：这里不再跳过 -f / -o 的下一个 token。
                        # 对 grep -f FILE / cp -o FILE 等，-f/-o 后面跟的是
                        # 真实文件路径，必须进入下面的路径校验；盲目跳过会
                        # 导致 grep -f /etc/passwd 这类越界路径被放行。
                        if arg in ("-n", "-r", "-R", "-d", "-e", "-w", "-x",
                                   "-c", "-m", "-t", "-p", "--max-depth",
                                   "--include", "--exclude", "--pattern"):
                            skip_next = True
                        i += 1
                        continue
                    # 非选项 → 可能是路径
                    if arg and arg not in ("|", "&&", "||", ";", ">"):
                        paths.append(arg)
                    i += 1
                continue

            # python -c "..." / python -e "..." 情况
            # 注意：python/python3 已从 _BASH_FILE_CMDS 移除，否则这里的
            # 代码字符串扫描分支永远不可达（会被上面的文件命令分支吞掉）。
            if base_cmd in ("python", "python3", "pip", "pip3"):
                # 扫描 -c / -e 后面的代码字符串中的路径型字面量
                for opt in ("-c", "-e"):
                    if opt in tokens[i + 1:]:
                        idx = tokens.index(opt, i + 1)
                        if idx + 1 < len(tokens):
                            code = tokens[idx + 1]
                            # 提取所有字符串字面量，凡像路径（绝对 / ~/ ./ ../
                            # 或含 ".."）的都送交沙箱校验
                            code_paths = re.findall(
                                r'''['"]([^'"]+)['"]''', code,
                            )
                            for s in code_paths:
                                if (
                                    s.startswith("/")
                                    or s.startswith("~")
                                    or s.startswith(".")
                                    or ".." in s
                                ):
                                    paths.append(s)
                # 同时扫描脚本/参数中的本地路径型 token（如 python ../x.py）
                j = i + 1
                while j < len(tokens):
                    arg = tokens[j]
                    if self._looks_like_local_path(arg):
                        paths.append(arg)
                    j += 1
                i += 1
                continue

            # [MEDIUM] 未识别命令（如 scp/rsync/vim/nano/xargs/dd 等）：
            # 采用 fail-closed 策略，扫描后续所有参数中的本地路径型 token
            # 并做沙箱校验，而不是完全放行。
            i += 1
            while i < len(tokens):
                arg = tokens[i]
                if self._looks_like_local_path(arg):
                    paths.append(arg)
                i += 1
            continue

        return paths

    @staticmethod
    def _extract_command(tool_input: str) -> str:
        """从 tool_call.input JSON 中提取 command 字段"""
        if not tool_input:
            return ""
        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return tool_input if isinstance(tool_input, str) else ""
        if not isinstance(data, dict):
            return ""
        cmd = data.get("command")
        return cmd if isinstance(cmd, str) else ""

    # ------------------------------------------------------------------
    # 路径提取（Read/Write/Edit/Glob/Grep）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_path(tool_input: str, path_key: str) -> str | None:
        """从 tool_call.input JSON 中提取路径字段值

        Args:
            tool_input: 工具输入 JSON 字符串
            path_key: 路径字段名（如 "file_path", "path"）

        Returns:
            路径字符串，未找到则返回 None
        """
        if not tool_input:
            return None

        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        value = data.get(path_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    # ------------------------------------------------------------------
    # 路径校验
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_local_path(tok: str) -> bool:
        """判断 token 是否可能是本地文件路径（用于未识别命令的 fail-closed 扫描）

        仅对看起来像本地路径的 token 做沙箱校验，避免误伤普通参数。
        远端写法（含 @ 或 host:path / scheme://）直接排除，因为这是
        scp/rsync/curl 等的远端地址，不当作本地文件来校验。
        """
        if not tok:
            return False
        # `@` 仅当后接 host 模式 (含 : 或 .) 才视为远端 (user@host / user@host:path)。
        # 否则 `@/etc/passwd` 或 `@file` 这类本地写法 (curl -d @file 上传本地文件)
        # 必须当作本地路径做沙箱校验，否则会被上面无脑排除而漏拦 (攻击评审发现)。
        if "@" in tok:
            if (":" in tok or "." in tok) and not tok.startswith("@"):
                return False  # user@host[:path] 远端写法 (scp/rsync)
            return True  # @/path 或 @file 均为本地路径, 必须校验
        if ":" in tok and not tok.startswith("/") and not tok.startswith("."):
            return False  # scheme:// 或 host:path 远端写法
        t = os.path.expanduser(os.path.expandvars(tok))
        if t.startswith("-"):
            return False  # 选项标志
        if t.isdigit():
            return False  # 纯数字（如 -m 644 的模式）
        if "/" in t or t.startswith("~") or t.startswith(".") or ".." in t:
            return True
        return False

    def _is_within_sandbox(self, path_str: str) -> bool:
        """检查路径是否在沙箱目录内

        处理逻辑：
        1. 相对路径 → 基于沙箱根目录解析
        2. 绝对路径 → 直接解析
        3. 使用 Path.resolve() 消除 .. 和符号链接
        4. 检查解析后的路径是否以沙箱根目录开头

        Args:
            path_str: 待检查的路径字符串

        Returns:
            True 表示路径在沙箱内
        """
        try:
            if not path_str:
                return True

            # [HIGH] `@`-prefixed local file (curl -d @/etc/passwd) — strip the
            # leading @ so the real path is containment-checked, otherwise it
            # would be misread as a sandbox-relative path and wrongly pass.
            if path_str.startswith("@"):
                path_str = path_str[1:]

            # [HIGH] 在 containment 检查前先做 shell 展开（~ 与环境变量）。
            # 否则 "~" / "$HOME" 会被当成无害的相对 token 直接放行，
            # 而 shell 实际会将其解析到沙箱之外。
            expanded = os.path.expanduser(os.path.expandvars(path_str))
            if not expanded:
                # 展开后为空（如未设置的环境变量），保守地判定为越界。
                return False

            # 跳过明显的非路径字符串（选项参数、纯数字等），但绝不把
            # "/" 或绝对路径 fast-path 成“在沙箱内”——它们必须被校验。
            if expanded.startswith("-") and not expanded.startswith("--"):
                return True
            if expanded.isdigit():
                return True

            target = Path(expanded)
            if not target.is_absolute():
                # 相对路径：基于沙箱根目录解析
                target = (self._sandbox_resolved / target).resolve()
            else:
                target = target.resolve()

            sandbox = self._sandbox_resolved

            # 精确匹配沙箱根目录
            if target == sandbox:
                return True

            # 检查是否为沙箱的子路径
            return str(target).startswith(str(sandbox) + os.sep)
        except (ValueError, OSError):
            # 路径解析异常（如非法字符），拒绝
            return False
