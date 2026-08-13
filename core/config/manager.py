# -*- coding: utf-8 -*-

from __future__ import annotations

import os

from .loader import YamlLoader
from .schemas import AppConfig


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
        self._config = AppConfig(**resolved)
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
