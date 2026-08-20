# -*- coding: utf-8 -*-

"""输入校验与路径安全工具

集中处理用户提供的标识符（user_id / session_id）与沙箱路径的越界防护，
避免在 api / middleware / workspace 各层重复实现导致不一致。
"""

from __future__ import annotations

import os
import re

# 合法标识符：1-64 字符，字母/数字/下划线开头，仅含字母数字下划线连字符。
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")


def is_valid_id(value: object) -> bool:
    """判断值是否为合法的用户/会话标识符。

    Args:
        value: 待校验值（仅接受字符串）。

    Returns:
        合法返回 True，否则 False。
    """
    if not isinstance(value, str) or not value:
        return False
    return bool(_ID_RE.match(value))


def coerce_id(value: object, default: str = "anonymous") -> str:
    """将任意输入规范为合法标识符。

    非法（None / 空 / 含路径穿越字符）时回退到 ``default``，
    防止 ``user_id="/"`` 或 ``"../../"`` 被拼接进文件系统路径。
    """
    return value if is_valid_id(value) else default


def is_path_within(base_dir: str, *parts: str) -> bool:
    """判断拼接路径是否落在 ``base_dir`` 之内（解析符号链接后）。"""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, *parts))
    return target == base or target.startswith(base + os.sep)


def resolve_within(base_dir: str, *parts: str) -> str | None:
    """在 ``base_dir`` 内解析路径，越界返回 None。"""
    if not is_path_within(base_dir, *parts):
        return None
    return os.path.realpath(os.path.join(base_dir, *parts))
