# -*- coding: utf-8 -*-

"""输入校验与路径安全工具测试

覆盖 core/validators.py：
- is_valid_id 拒绝非法 id、接受合法 id
- coerce_id 在非法时回退默认值
- is_path_within / resolve_within 正确识别越界与包含（含软链接越界）

仅依赖标准库（os / tempfile），无需任何外部服务。
"""

import os
import tempfile

import pytest

from core.validators import (
    coerce_id,
    is_path_within,
    is_valid_id,
    resolve_within,
)


# ============================================================
# is_valid_id
# ============================================================

def test_is_valid_id_rejects_illegal():
    for bad in ("/", "../x", "", "..", "a/b", "a\\b", "a b", "x" * 65):
        assert not is_valid_id(bad), f"应拒绝非法 id: {bad!r}"
    # 非字符串一律拒绝
    assert not is_valid_id(None)
    assert not is_valid_id(123)
    assert not is_valid_id(object())


def test_is_valid_id_accepts_legal():
    for good in ("user_1", "sess-2", "a", "A9", "x" * 64, "user-name_3"):
        assert is_valid_id(good), f"应接受合法 id: {good!r}"


# ============================================================
# coerce_id
# ============================================================

def test_coerce_id_falls_back_on_invalid():
    assert coerce_id("/") == "anonymous"
    assert coerce_id("../x") == "anonymous"
    assert coerce_id("") == "anonymous"
    assert coerce_id(None) == "anonymous"
    assert coerce_id(123) == "anonymous"


def test_coerce_id_keeps_valid():
    assert coerce_id("user_1") == "user_1"
    assert coerce_id("sess-2", default="guest") == "sess-2"
    assert coerce_id("/", default="root") == "root"


# ============================================================
# 路径越界检测
# ============================================================

def test_is_path_within_and_resolve_within_normal():
    with tempfile.TemporaryDirectory() as base:
        f = os.path.join(base, "sub", "file.txt")
        os.makedirs(os.path.dirname(f))
        with open(f, "w") as fh:
            fh.write("x")
        assert is_path_within(base, "sub", "file.txt") is True
        resolved = resolve_within(base, "sub", "file.txt")
        assert resolved is not None
        assert os.path.realpath(resolved) == os.path.realpath(f)


def test_is_path_within_detects_dotdot_escape():
    with tempfile.TemporaryDirectory() as base:
        assert is_path_within(base, "..", "escape.txt") is False
        assert is_path_within(base, "sub", "..", "..", "escape.txt") is False
        assert resolve_within(base, "..", "escape.txt") is None


def test_is_path_within_detects_absolute_outside():
    with tempfile.TemporaryDirectory() as base:
        assert is_path_within(base, "/etc/passwd") is False
        assert resolve_within(base, "/etc/passwd") is None


def test_is_path_within_detects_symlink_escape():
    """软链接指向沙箱外时，解析真实路径后应判定为越界。"""
    with tempfile.TemporaryDirectory() as root:
        outside = os.path.join(root, "outside")
        os.makedirs(outside)
        inside = os.path.join(root, "inside")
        os.makedirs(inside)
        link = os.path.join(inside, "escape_link")
        os.symlink(outside, link)

        # 通过软链接的「字符串路径」看似在 base 内，但真实路径在外
        assert is_path_within(inside, "escape_link") is False
        assert resolve_within(inside, "escape_link") is None

        # 正常子目录/文件仍判定为包含
        normal = os.path.join(inside, "ok.txt")
        with open(normal, "w") as fh:
            fh.write("x")
        assert is_path_within(inside, "ok.txt") is True
        assert resolve_within(inside, "ok.txt") is not None


# ============================================================
# 攻击审查新增边界: null-byte id + 多重 ../ 越界组合
# ============================================================

def test_is_valid_id_rejects_null_byte():
    # NUL 字节会破坏路径拼接 / 文件系统语义, 必须判为非法 id
    assert not is_valid_id("a\x00b")
    assert not is_valid_id("a\x00")
    assert not is_valid_id("\x00")


def test_coerce_id_null_byte_falls_back():
    # 非法 (含 NUL) id 应回退到默认匿名标识
    assert coerce_id("a\x00b") == "anonymous"
    assert coerce_id("a\x00b", default="guest") == "guest"


def test_is_path_within_detects_multi_dotdot_escape():
    """多次 ../ 越过 base 目录应判定为越界。"""
    with tempfile.TemporaryDirectory() as base:
        # 单一子目录后连跳两级 -> 落在 base 之上
        assert is_path_within(base, "sub", "..", "..", "escape.txt") is False
        assert resolve_within(base, "sub", "..", "..", "escape.txt") is None

        # 多级嵌套后越界
        assert is_path_within(base, "a", "b", "..", "..", "..", "escape.txt") is False
        assert resolve_within(base, "a", "b", "..", "..", "..", "escape.txt") is None


def test_is_path_within_dotdot_plus_absolute_escape():
    """../ 与绝对路径混用 (../../etc/passwd) 必须判为越界。"""
    with tempfile.TemporaryDirectory() as base:
        assert is_path_within(base, "..", "..", "/etc/passwd") is False
        assert resolve_within(base, "..", "..", "/etc/passwd") is None


def test_is_path_within_inside_dotdot_not_false_positive():
    """../ 若解析后仍在 base 内 (未越界), 不应被误判为越界。"""
    with tempfile.TemporaryDirectory() as base:
        # sub/../escape.txt == base/escape.txt, 仍在 base 内
        assert is_path_within(base, "sub", "..", "escape.txt") is True
        resolved = resolve_within(base, "sub", "..", "escape.txt")
        assert resolved is not None
        assert os.path.realpath(resolved) == os.path.realpath(
            os.path.join(base, "escape.txt")
        )

        # sub/../sub2/file 同样在 base 内
        assert is_path_within(base, "sub", "..", "sub2", "file") is True
