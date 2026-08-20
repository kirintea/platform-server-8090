# -*- coding: utf-8 -*-

"""allowlist 反转盲点回归测试

攻击审查证明: allowlist 模式下，若 `_is_blocked` 把 `matched == allow` 误判为
「命中白名单即放行」(反转 bug)，则「不在白名单内且 medium-risk」的命令会漏过。

正确语义 (command_guard.py `_is_blocked`, allowlist 分支):
    - 命中高风险管理           -> 拦截 (DENIED)
    - 未命中高风险管理         -> 拦截, 当且仅当命中白名单规则 (not rule_matched)

因此「不在白名单且 medium-risk」的命令必须被拦截 (DENIED)。本文件直接断言
`_is_blocked` 的返回: True == 拦截。这些用例当前就已通过 (反转修复已落地),
用于固化该行为, 防止未来回归。
"""

from middleware.command_guard import CommandGuardMiddleware


def _allowlist_guard() -> CommandGuardMiddleware:
    # 白名单仅放行 ls*; 其余一律必须拦截
    return CommandGuardMiddleware(mode="allowlist", enabled=True, rules=["ls*"])


def test_medium_risk_npm_off_allowlist_is_blocked():
    # npm install 属 MEDIUM 风险且不在白名单(ls*)内 -> 必须拦截
    assert _allowlist_guard()._is_blocked("npm install foo") is True


def test_medium_risk_docker_off_allowlist_is_blocked():
    assert _allowlist_guard()._is_blocked("docker run img") is True


def test_medium_risk_python_c_off_allowlist_is_blocked():
    assert _allowlist_guard()._is_blocked('python -c "..."') is True


def test_high_risk_always_blocked_even_on_allowlist():
    # 高危险命令无论是否在白名单内都必须拦截
    assert _allowlist_guard()._is_blocked("rm -rf /") is True


def test_allowlisted_command_is_allowed():
    # 命中白名单且非高危险 -> 放行
    assert _allowlist_guard()._is_blocked("ls -la") is False
