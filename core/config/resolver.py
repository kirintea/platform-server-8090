# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re


class EnvVarResolver:
    """解析配置中的 ${ENV_VAR} 和 ${ENV_VAR:-default} 占位符"""

    _PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

    @classmethod
    def resolve(cls, obj, env_mapping: dict | None = None):
        """递归解析字典 / 列表 / 字符串中的环境变量引用"""
        if isinstance(obj, dict):
            return {k: cls.resolve(v, env_mapping) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls.resolve(item, env_mapping) for item in obj]
        if isinstance(obj, str):
            return cls._resolve_string(obj, env_mapping)
        return obj

    @classmethod
    def _resolve_string(cls, text: str, env_mapping: dict | None = None) -> str:
        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2)

            if env_mapping and var_name in env_mapping:
                return env_mapping[var_name]

            value = os.getenv(var_name)
            if value is not None:
                return value

            if default is not None:
                return default

            # 找不到则保留原引用
            return match.group(0)

        return cls._PATTERN.sub(_replacer, text)
