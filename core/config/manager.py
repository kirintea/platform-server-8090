# -*- coding: utf-8 -*-

from __future__ import annotations

import os

from pydantic import ValidationError

from .loader import YamlLoader
from .schemas import AppConfig


def _config_validation_error(
    exc: ValidationError, yaml_path: str, model_name: str,
) -> RuntimeError:
    """将 pydantic ValidationError 转换为可诊断的启动错误

    明确列出出错的配置文件、模型，以及每个非法字段（extra='forbid'
    的拼写错误/多余键），让启动失败可读、可修，而不是裸 ValidationError。
    """
    lines = [
        f"配置校验失败: 文件 '{yaml_path}' 中模型 '{model_name}' 不合法，"
        f"共 {len(exc.errors())} 处问题:",
    ]
    for err in exc.errors():
        loc = " -> ".join(str(p) for p in err.get("loc", []))
        err_type = err.get("type", "")
        msg = err.get("msg", "")
        if err_type == "extra_forbidden":
            lines.append(
                f"  - [{loc or '<root>'}] 多余/未知字段 "
                f"(extra='forbid' 已禁止，请删除或修正拼写)"
            )
        else:
            lines.append(f"  - [{loc or '<root>'}] {msg} (type={err_type})")
    lines.append(
        "请修正上述配置后重试（不要移除 extra='forbid'，它是有意的安全守卫）。"
    )
    return RuntimeError("\n".join(lines))


class ConfigManager:
    """全局配置管理器 — 单例模式

    Usage:
        config = ConfigManager.get_instance().load()       # 自动读取 APP_ENV
        config = ConfigManager.get_instance().load("prod")  # 指定环境
    """

    _instance: ConfigManager | None = None
    _config: AppConfig | None = None

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> ConfigManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def load(self, env: str | None = None) -> AppConfig:
        """加载指定环境的配置

        Args:
            env: 'dev' / 'prod' / None（自动读取 APP_ENV 环境变量，默认 dev）

        Returns:
            校验通过的 AppConfig 实例
        """
        if env is None:
            env = os.getenv("APP_ENV", "dev")

        yaml_path = self._get_config_path(env)
        resolved = YamlLoader.load(yaml_path)
        try:
            self._config = AppConfig(**resolved)
        except ValidationError as exc:
            raise _config_validation_error(exc, yaml_path, "AppConfig") from exc
        return self._config

    def get(self) -> AppConfig:
        """获取当前配置（未加载则自动加载）"""
        if self._config is None:
            self.load()
        return self._config

    def reload(self) -> AppConfig:
        """强制重新加载"""
        self._config = None
        return self.load()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _get_config_path(env: str) -> str:
        return os.path.join("configs", f"{env}.yaml")
