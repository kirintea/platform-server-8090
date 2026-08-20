# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
import re


logger = logging.getLogger(__name__)


class EnvVarResolver:
    """解析配置中的 ${ENV_VAR} 和 ${ENV_VAR:-default} 占位符"""

    _PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

    @classmethod
    def resolve(cls, obj, env_mapping: dict | None = None):
        """递归解析字典 / 列表 / 字符串中的环境变量引用

        对于没有 :-default 且环境变量也未设置的占位符，默认 **硬失败**
        （fail-fast）：抛出 RuntimeError 列出未解析变量及引用位置，
        避免字面量 "${VAR}" 流入配置、在运行时才失败（例如 LLM 返回 401、
        健康检查却显示 green）。
        设置环境变量 ``ALLOW_UNRESOLVED_ENV=1`` 可降级为告警（仅高级用户）。
        """
        unresolved: dict[str, list[str]] = {}
        result = cls._resolve(obj, env_mapping, unresolved, path="")

        if unresolved:
            details = "; ".join(
                f"${{{var}}} (引用位置: {', '.join(paths)})"
                for var, paths in sorted(unresolved.items())
            )
            if os.getenv("ALLOW_UNRESOLVED_ENV") == "1":
                logger.warning(
                    "未解析的环境变量占位符（ALLOW_UNRESOLVED_ENV=1，已降级为告警，"
                    "字面量保留，可能造成运行时配置异常）: %s",
                    details,
                )
            else:
                raise RuntimeError(
                    "配置中存在未解析的环境变量占位符（无 :-default 且环境变量未设置），"
                    "为避免运行时失败（例如 LLM 返回 401）已 fail-fast 中止启动。\n"
                    "请设置对应环境变量，或临时设置 ALLOW_UNRESOLVED_ENV=1 降级为告警。\n"
                    f"未解析变量及引用位置: {details}"
                )
        return result

    @classmethod
    def _resolve(cls, obj, env_mapping, unresolved, path):
        if isinstance(obj, dict):
            return {
                k: cls._resolve(v, env_mapping, unresolved, f"{path}.{k}" if path else k)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [
                cls._resolve(item, env_mapping, unresolved, f"{path}[{i}]")
                for i, item in enumerate(obj)
            ]
        if isinstance(obj, str):
            return cls._resolve_string(obj, env_mapping, unresolved, path)
        return obj

    @classmethod
    def _resolve_string(
        cls, text: str, env_mapping, unresolved, path: str,
    ) -> str:
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

            # 无环境变量且无默认值：记录变量 -> 引用位置（用于硬失败/告警）
            unresolved.setdefault(var_name, []).append(path or "<root>")
            return match.group(0)

        return cls._PATTERN.sub(_replacer, text)
