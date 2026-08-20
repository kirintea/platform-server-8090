# -*- coding: utf-8 -*-

"""rm 变体硬化测试

验证 `_is_blocked` 对 `rm -rf /` 的各种混淆/变体一律拦截; 同时放行沙箱内的
相对路径删除 `rm -rf ./build` (属预期内行为, 不应误拦)。

注意: 其中若干混淆变体当前代码尚未覆盖 (攻击审查标记的 rm 硬化盲点);
这里断言的是「修复后应达到的安全结果」。这些用例用 `pytest.mark.xfail`
标注为待修复项 —— 当前运行若 FAIL 表示 fix agent 尚未落地, 属预期, 不视为测试错误;
一旦修复落地, 它们将变为 xpass (通过)。
"""

import pytest

from middleware.command_guard import CommandGuardMiddleware


def _guard() -> CommandGuardMiddleware:
    # 默认 blocklist + enabled; rm 硬化与名单方向无关, 此处用默认即可
    return CommandGuardMiddleware()


@pytest.mark.xfail(
    reason="rm 混淆变体硬化尚未落地 (攻击审查盲点: '-fr' 顺序变体); 断言修复后安全结果",
    strict=False,
)
def test_rm_dash_fr_slash_blocked():
    assert _guard()._is_blocked("rm -fr /") is True


@pytest.mark.xfail(
    reason="rm 混淆变体硬化尚未落地 (攻击审查盲点: 引号包裹 -rf); 断言修复后安全结果",
    strict=False,
)
def test_rm_quoted_rf_blocked():
    assert _guard()._is_blocked("rm '-rf' /") is True


@pytest.mark.xfail(
    reason="rm 混淆变体硬化尚未落地 (攻击审查盲点: 拆分 -r -f); 断言修复后安全结果",
    strict=False,
)
def test_rm_split_flags_blocked():
    assert _guard()._is_blocked("rm -r -f /") is True


@pytest.mark.xfail(
    reason="rm 混淆变体硬化尚未落地 (攻击审查盲点: 注入引号混淆 rm); 断言修复后安全结果",
    strict=False,
)
def test_rm_obfuscated_quotes_blocked():
    assert _guard()._is_blocked("r''m -rf /") is True


@pytest.mark.xfail(
    reason="rm 混淆变体硬化尚未落地 (攻击审查盲点: -- 选项分隔混淆); 断言修复后安全结果",
    strict=False,
)
def test_rm_dash_dash_rf_blocked():
    assert _guard()._is_blocked("rm --rf /") is True


@pytest.mark.xfail(
    reason="相对路径 rm -rf ./build 应放行 (当前被误拦); 断言修复后安全结果",
    strict=False,
)
def test_rm_relative_build_allowed():
    assert _guard()._is_blocked("rm -rf ./build") is False
