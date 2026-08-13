# -*- coding: utf-8 -*-

from __future__ import annotations

import os

import yaml

from .resolver import EnvVarResolver


class YamlLoader:
    """YAML 配置文件加载器，自动解析 ${ENV_VAR} 占位符"""

    @staticmethod
    def load(file_path: str) -> dict:
        """加载并解析 YAML 配置文件

        Args:
            file_path: YAML 文件路径

        Returns:
            解析后的配置字典

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"配置文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            raw = {}

        return EnvVarResolver.resolve(raw)
