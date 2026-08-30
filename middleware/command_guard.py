# -*- coding: utf-8 -*-

"""命令内容安全守卫

解析 tool_call.input JSON 中的实际命令，按黑白名单模式匹配。
与 tool_guard.py 并列:
  - tool_guard  → 工具名级 (Bash, Read, Write ...)
  - command_guard → 命令内容级 (rm -rf, curl|bash ...)

配置 (configs/dev.yaml):
    agent:
      command_guard:
        enabled: true
        mode: "blocklist"        # allowlist | blocklist
        rules:
          - "rm -rf /"           # 精确匹配
          - "rm -rf *"           # 通配符匹配
          - "curl *|*bash*"      # 管道执行
          - "*/dev/tcp/*"        # Bash 反弹 shell
          - "Invoke-Expression*" # PowerShell 远程执行
          - "certutil*"          # Windows 下载
"""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

from loguru import logger

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse


# High-risk patterns are ALWAYS blocked in BOTH blocklist and allowlist modes.
# Anchors are deliberately loosened (word boundary instead of `^`) so the
# dangerous token is caught anywhere in the command, and several new
# obfuscation vectors are covered. Inline interpreter execution
# (`python -c`, `node -e`, ...) was MOVED to _MEDIUM_RISK_PREFIXES on purpose
# (see issue #4) so it is no longer hard-blocked in blocklist mode.
_HIGH_RISK_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        # --- Destructive / filesystem ---
        r"\brm\s+-rf\s+/",
        # `rm -rf .` / `rm -rf ./` (current dir) is destructive; but allow
        # relative subdirs like `rm -rf ./build` (attack-review fix #1).
        r"\brm\s+-rf\s+\./?(?:\s|$)",
        r"\brm\s+-rf\s+\*",
        r"\brm\s+-rf\s+~",
        r"git\s+clean\s+-fdx",
        # --- Download & pipe-to-shell ---
        r"(?:curl|wget)[^\n]*\|\s*(?:bash|sh)",
        # --- Privilege / process control (anchors loosened to match anywhere) ---
        r"\bsudo(?:\s|$)",
        r"\bchmod\s+-R\b",
        r"\bchown\b",
        r"\bdd\s+if=/dev/zero",
        r"\bmkfs\b",
        r"\bfdisk\b",
        r"\beval\b",
        r"\bshutdown(?:\s|$)",
        r"\breboot(?:\s|$)",
        r"\bkill\s+-9\s+1\b",
        r"\bpkill\s+-9\b",
        r"\bsystemctl\s+(?:stop|disable|mask)\b",
        # --- Arbitrary command execution wrappers ---
        r"(?:^|\s)(?:ba)?sh\s+-c\b",            # bash -c / sh -c
        r"(?:^|\s)exec(?:\s|$)",                # exec (not -exec, handled by flag)
        r"(?:^|\s)source\b",                    # source
        r"(?:^|\s)\.\s+(?:\.|/)",               # dot-source: `. /path` or `. ./script`
        r"\b(?:doas|su|pkexec)\b",              # privilege escalation wrappers
        # --- Reverse shells / network ---
        r"\bnc\b[^\n]*(?:-e\b|-c\b)",           # netcat exec
        r"/dev/(?:tcp|udp)/",                   # bash /dev/tcp|udp reverse shell
        # --- Process / command substitution ---
        r"<\([^)]*\)",                          # process substitution <(...)
        # --- Pipe output into an interpreter (data-execution sink) ---
        r"\|\s*(?:python|python3|perl|ruby|node|php|lua)\b",
    )
)
_SAFE_REDIRECT_RE = re.compile(r"\d*>&\d+")
_SHELL_META_CHARS = (";", "&&", "||", "|", "$(", "`", ">", "<", "\n")
_LOW_RISK_PREFIXES = (
    "ls", "cat", "grep", "find", "sed", "pwd", "echo", "head", "tail", "wc",
    "mkdir", "touch", "cd", "npm test", "npm run build", "npm run lint",
    "npm run dev", "npm run start", "cargo check", "cargo build", "cargo test",
    "cargo clippy", "cargo fmt", "git status", "git log", "git diff", "git show",
    "git branch", "python --version", "node --version", "rustc --version",
)
# NOTE: inline interpreter execution (e.g. `python -c`, `node -e`, `ruby -e`,
# `perl -e`) was MOVED HERE from the always-block _HIGH_RISK_PATTERNS on purpose
# (issue #4). These are flagged MEDIUM-risk: they are NOT auto-blocked in
# blocklist mode unless an explicit glob rule matches them, so legitimate dev
# workflows (running a quick inline script) keep working. Pipe-to-interpreter
# (e.g. `curl ... | python`) remains HIGH-risk via _HIGH_RISK_PATTERNS.
# HOWEVER, a dedicated RCE-token check (_check_inline_interpreter_rce) now
# hard-blocks any inline-interpreter code string that embeds RCE-prone tokens
# (os.system, subprocess, eval(, ...) — see attack-review fix #2). This closes
# the `python -c "os.system('id')"` RCE primitive while keeping benign
# `python -c "print(1)"` allowed.
_MEDIUM_RISK_PREFIXES = (
    "npm install", "npm ci", "npm uninstall", "git push", "git pull", "git merge",
    "git rebase", "docker", "pip install", "pip uninstall", "cargo install",
    "yarn add", "yarn remove", "pnpm install", "pnpm add",
    "python -c", "python3 -c", "node -e", "ruby -e", "perl -e",
)
_DANGEROUS_FLAGS = (
    "-exec", "-delete", "-ok", "-execdir", "-okdir", "-fprint", "-fprint0",
    "-fprintf", "-fls", "-i",
)
_UNICODE_META_TRANSLATION = str.maketrans({
    "＆": "&", "｜": "|", "﹔": ";", "；": ";", "＞": ">", "＜": "<",
    "＄": "$", "｀": "`", "（": "(", "）": ")", "｛": "{", "｝": "}",
    "！": "!", "／": "/", "　": " ",
})

# Inline-interpreter code strings that embed RCE-prone tokens are blocked even
# though `python -c` / `node -e` / `perl -e` / `ruby -e` are otherwise
# MEDIUM-risk. Benign inline scripts (e.g. `python -c "print(1)"`) stay allowed
# (attack-review fix #2).
_INLINE_INTERPRETER_RE = re.compile(
    r"\b(?:python3?|node|perl|ruby)\b\s+-[ce]\s+("
    r'"[^"]*"'
    r"|'[^']*'"
    r"|\S+)"
)
_RCE_TOKENS = (
    "os.system", "subprocess", "popen", "eval(", "exec(", "__import__",
    "socket", "urllib", "requests", "pickle", "ctypes", "os.popen",
)
# `<interpreter> <file>` detection for download-and-run script-wrap escapes.
_SCRIPT_INTERPRETERS_RE = re.compile(r"\b(bash|sh|python3?|node|ruby|perl)\b\s+(\S+)")
_SCRIPT_EXTS = (".sh", ".py", ".pl", ".rb", ".js")
_DOWNLOAD_RE = re.compile(r"\b(?:curl|wget)\b")
# Matches `rm` and captures everything after it for structural flag analysis.
_RM_RE = re.compile(r"\brm\b\s*(.*)", re.DOTALL)


class CommandGuardMiddleware(MiddlewareBase):
    """命令内容安全守卫 — 按黑白名单模式匹配命令内容"""

    def __init__(
        self,
        enabled: bool = True,
        mode: str = "blocklist",
        rules: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._enabled = enabled
        self._mode = mode  # "allowlist" | "blocklist"
        self._rules = rules or []

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行 — 检查命令内容是否命中名单规则"""
        if not self._enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_call = input_kwargs["tool_call"]
        command = self._extract_command(tool_call.input)

        if not self._is_blocked(command):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        logger.info(
            "CommandGuard [%s] 拦截 %s: %s",
            self._mode, tool_call.name, command[:200],
        )
        yield ToolResponse(
            id=tool_call.id,
            content=[TextBlock(
                text=f"[CommandGuard] 命令被拦截 ({self._mode}): {command[:200]}",
            )],
            state=ToolResultState.DENIED,
        )

    # ------------------------------------------------------------------
    # 命令提取
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_command(tool_input: Any) -> str:
        """从 tool_call.input 中提取实际命令文本

        支持的 input 格式:
          - Bash/PowerShell: {"command": "ls -la"}
          - Write: {"path": "/tmp/x", "content": "..."}
          - Read: {"file_path": "/tmp/x"}
          - MCP: 各种格式, 取 command/content/path 字段
          - 非 JSON: 原样返回

        返回: 始终为 str。这是关键——若返回非 str, 上层 `_is_blocked`
        会对其调用 `.strip()` 而在 agent 循环内抛出 AttributeError 崩溃
        (issue #5)。因此对非 str / 无法提取命令的情况一律返回 "".
        """
        # Non-string input (dict / list / number / None) must never be returned
        # as-is; coerce to a str or fall back to "".
        if tool_input is None:
            return ""
        if not isinstance(tool_input, str):
            if isinstance(tool_input, dict):
                for key in ("command", "content", "path", "file_path"):
                    if key in tool_input and isinstance(tool_input[key], str):
                        return tool_input[key]
                return ""
            try:
                tool_input = json.dumps(tool_input, ensure_ascii=False)
            except (TypeError, ValueError):
                return ""

        if not isinstance(tool_input, str):
            return ""

        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return tool_input

        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("command", "content", "path", "file_path"):
                if key in data and isinstance(data[key], str):
                    return data[key]
            return ""
        # data 是 list / number 等, 没有可守卫的命令内容
        return ""

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _is_blocked(self, command: str) -> bool:
        """判断命令内容是否被拦截

        Args:
            command: 提取后的命令文本

        Returns:
            True 表示应被拦截
        """
        # _extract_command 始终返回 str; 这里再防御一次, 避免非 str 崩溃 (issue #5)
        if not isinstance(command, str):
            command = str(command)
        command = command or ""

        if not command:
            # 空命令: allowlist 模式下拦截, blocklist 模式下放行
            return self._mode == "allowlist"

        rule_matched = any(
            fnmatch.fnmatch(command, pattern)
            for pattern in self._rules
        )
        # High-risk detection is independent of the list direction.
        high_risk = self._matches_high_risk(command)

        if self._mode == "blocklist":
            # 命中黑名单规则 → 拦截；或命中高风险管理 → 拦截
            return rule_matched or high_risk

        # allowlist 模式 —— 高风险管理在两种模式下一律拦截。
        # 仅当某条白名单规则命中且非高风险管理时, 才放行。
        # 即: 未命中白名单, 或命中高风险管理 → 拦截。
        if high_risk:
            return True
        return not rule_matched

    @classmethod
    def _normalize_for_high_risk(cls, command: str) -> str:
        """Normalize a command before high-risk matching.

        Strips one layer of surrounding quotes, expands the end-of-options
        ``--`` marker, removes shell escape backslashes and applies the unicode
        fullwidth translation so simple obfuscation can't bypass the patterns.
        """
        s = command.strip()
        # Strip a single layer of surrounding matching quotes/backticks.
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"', "`"):
            s = s[1:-1].strip()
        # Unicode fullwidth normalization (e.g. U+FF0F ／ -> /, U+3000 fullwidth space).
        s = s.translate(_UNICODE_META_TRANSLATION)
        # Expand `-- ` (end of options) so `rm -rf -- /` matches `rm -rf /`.
        s = s.replace("-- ", " ")
        # Remove shell escape backslashes used to obfuscate tokens.
        s = re.sub(r"\\ ", " ", s)        # backslash before a space
        s = re.sub(r"\\(?=\S)", "", s)    # backslash before a non-space char
        # Obfuscation hardening: collapse empty quote pairs (e.g. r''m -> rm)
        # and strip quotes around binaries / flags / tokens so quote-wrapped
        # commands (e.g. `rm '-rf' /`, `r''m -rf /`) can't slip past the
        # high-risk checks (attack-review fix #1).
        s = s.replace("''", "").replace('""', "")
        s = s.replace("'", "").replace('"', "")
        return s

    @classmethod
    def _matches_high_risk(cls, command: str) -> bool:
        """Detect high-risk commands and shell injection constructs.

        High-risk detection is *always-on* and independent of the list mode:
        a high-risk command is blocked in BOTH blocklist and allowlist modes.
        AgentScope has no confirmation callback at this middleware boundary,
        so KSI_RUST's ``RequiresConfirmation`` outcomes are conservatively
        represented as blocked commands here.

        Shell-injection meta/flag inspection (``_has_dangerous_meta`` /
        ``_has_dangerous_flag``) is applied to EVERY command, not only to
        low-risk ones, so non-low-risk commands can no longer skip inspection
        (issue #2).
        """
        normalized = cls._normalize_for_high_risk(command)

        # 1) Always-block high-risk patterns (both modes).
        if any(pattern.search(normalized) for pattern in _HIGH_RISK_PATTERNS):
            return True

        # 1b) Structural high-risk detectors — cover obfuscation the static
        #     regexes above can't, and close the RCE primitives the attack
        #     review proved live:
        #       - rm flag reorder / split / quote obfuscation (fix #1)
        #       - inline-interpreter RCE tokens (fix #2) — run on the RAW
        #         command so the quoted code string stays intact.
        #       - download-and-run script-wrap escape (fix #3)
        if cls._high_risk_rm(normalized):
            return True
        if cls._check_inline_interpreter_rce(command):
            return True
        if cls._check_script_wrap_rce(normalized):
            return True

        # 2) Compound commands: inspect EVERY part for injection. Pipes are
        #    split too so legitimate pipelines (e.g. `cat x | grep y`) survive.
        parts = cls._split_compound_command(normalized)
        if parts is not None:
            for part in parts:
                if cls._has_dangerous_meta(part) or cls._has_dangerous_flag(part):
                    return True
            # No high-risk and no dangerous meta/flag in any part: leave the
            # decision to the configured glob policy instead of blanket-blocking.
            return False

        # 3) Single command: apply meta/flag checks to EVERY command, including
        #    medium-risk / unknown ones (low-risk fast-path still ends here too).
        if cls._has_dangerous_meta(normalized) or cls._has_dangerous_flag(normalized):
            return True

        # 4) Otherwise this command is not auto-blocked; the glob policy
        #    (blocklist rules / allowlist rules) decides.
        return False

    # ------------------------------------------------------------------
    # Structural high-risk detectors (obfuscation-resistant)
    # ------------------------------------------------------------------

    @classmethod
    def _high_risk_rm(cls, command: str) -> bool:
        """Block `rm` carrying BOTH recursive (-r/-R/--recursive) and force
        (-f/--force) intent that targets an absolute (/), home (~) or parent
        (..) path.

        Covers flag reorder (`-fr`), split flags (`-r -f`), the `--` end-of-
        options marker, and quote / empty-quote obfuscation (`'-rf'`,
        `r''m`). Relative targets (e.g. `rm -rf ./build`) stay allowed
        (attack-review fix #1).
        """
        m = _RM_RE.search(command)
        if not m:
            return False
        tokens = m.group(1).split()
        flags: list[str] = []
        idx = 0
        for tok in tokens:
            if tok.startswith("-") and tok != "-":
                flags.append(tok)
                idx += 1
            elif tok == "--":
                # end-of-options: following tokens are paths, not flags
                idx += 1
                break
            else:
                break
        has_recursive = any("r" in f or "R" in f for f in flags)
        has_force = any("f" in f for f in flags)
        if not (has_recursive and has_force):
            return False
        if idx < len(tokens):
            target = tokens[idx]
            # After `--`, the next token is the first path argument.
            if target == "--" and idx + 1 < len(tokens):
                target = tokens[idx + 1]
            if target.startswith("/") or target.startswith("~") or target.startswith(".."):
                return True
        return False

    @classmethod
    def _check_inline_interpreter_rce(cls, command: str) -> bool:
        """Block inline-interpreter code (`python -c`, `node -e`, `perl -e`,
        `ruby -e`) that embeds RCE-prone tokens. Benign inline scripts such as
        `python -c "print(1)"` remain allowed (attack-review fix #2).
        """
        for m in _INLINE_INTERPRETER_RE.finditer(command):
            code = m.group(1)
            if len(code) >= 2 and code[0] == code[-1] and code[0] in ("'", '"'):
                code = code[1:-1]
            if any(token in code for token in _RCE_TOKENS):
                return True
        return False

    @classmethod
    def _check_script_wrap_rce(cls, command: str) -> bool:
        """Block download-and-run script-wrap sandbox escapes: a command that
        both fetches a file (curl/wget) AND executes it via an interpreter
        (bash/sh/python/node/ruby/perl) in the same compound command
        (`curl ... -o x.sh && bash x.sh`).

        Bare `bash ./local.sh` (no download) stays MEDIUM — NOT auto-blocked —
        because the OS-level sandbox is the real boundary (attack-review fix #3).
        """
        if not _DOWNLOAD_RE.search(command):
            return False
        for m in _SCRIPT_INTERPRETERS_RE.finditer(command):
            arg = m.group(2)
            if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in ("'", '"'):
                arg = arg[1:-1]
            if arg.startswith("-"):
                continue
            if arg.lower().endswith(_SCRIPT_EXTS):
                return True
            # A fetched file executed by an interpreter (relative/absolute path)
            # within the same download command is treated as an escape attempt.
            if ("/" in arg or arg.startswith(".")) and not arg.lower().startswith("http"):
                return True
        return False

    @staticmethod
    def _split_compound_command(command: str) -> list[str] | None:
        separators = ("&&", "||", ";", "\n", "|")
        if not any(separator in command for separator in separators):
            return None
        parts = [command]
        for separator in separators:
            parts = [piece for part in parts for piece in part.split(separator)]
        parts = [part.strip() for part in parts if part.strip()]
        return parts if len(parts) > 1 else None

    @staticmethod
    def _is_low_risk(command: str) -> bool:
        return any(command == prefix or command.startswith(f"{prefix} ") for prefix in _LOW_RISK_PREFIXES)

    @classmethod
    def _all_low_risk(cls, commands: list[str]) -> bool:
        return all(cls._is_low_risk(command) for command in commands)

    @staticmethod
    def _has_dangerous_flag(command: str) -> bool:
        return any(flag in command for flag in _DANGEROUS_FLAGS)

    @staticmethod
    def _has_dangerous_meta(command: str) -> bool:
        sanitized = _SAFE_REDIRECT_RE.sub("", command)
        normalized = sanitized.translate(_UNICODE_META_TRANSLATION)
        return any(marker in sanitized or marker in normalized for marker in _SHELL_META_CHARS)
