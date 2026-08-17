# -*- coding: utf-8 -*-

"""工具管理中间件 — 从配置文件选择性加载工具

读取 configs/tools.yaml，根据 enabled 标记筛选内置工具，
支持通过 module + class_name / func_name 动态加载自定义工具。

与 ToolGuardMiddleware 的分工：
  - tool_manager → 注册时：决定工具是否存在（YAML 配置）
  - tool_guard   → 运行时：决定工具是否执行（黑白名单）
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from agentscope.tool import (
    Bash,
    Edit,
    FunctionTool,
    Glob,
    Grep,
    PowerShell,
    Read,
    TaskCreate,
    TaskGet,
    TaskList,
    TaskUpdate,
    ToolBase,
    Write,
)

logger = logging.getLogger(__name__)


class ToolManagerMiddleware:
    """工具管理器 — 从配置文件选择性加载工具"""

    # 内置工具名称 → AgentScope 类
    BUILTIN_TOOL_MAP: dict[str, type[ToolBase]] = {
        "Bash": Bash,
        "PowerShell": PowerShell,
        "Read": Read,
        "Write": Write,
        "Edit": Edit,
        "Glob": Glob,
        "Grep": Grep,
        "TaskCreate": TaskCreate,
        "TaskList": TaskList,
        "TaskGet": TaskGet,
        "TaskUpdate": TaskUpdate,
    }

    def __init__(self, config_path: str = "configs/tools.yaml") -> None:
        self._config_path = Path(config_path)
        self._config: dict[str, Any] = self._load_yaml(self._config_path)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def load_tools(self) -> list[ToolBase]:
        """返回经过配置筛选的工具列表

        Returns:
            启用的 ToolBase 实例列表
        """
        tools: list[ToolBase] = []

        # 1. 筛选内置工具
        builtin_cfg: dict = self._config.get("builtin_tools", {})
        for name, entry in builtin_cfg.items():
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            tool_cls = self.BUILTIN_TOOL_MAP.get(name)
            if tool_cls is None:
                logger.warning("未知内置工具: %s，跳过", name)
                continue
            tools.append(tool_cls())

        # 2. 动态加载自定义工具
        for ct in self._config.get("custom_tools", []):
            if not isinstance(ct, dict) or not ct.get("enabled", True):
                continue
            try:
                tools.append(self._load_custom_tool(ct))
            except Exception:
                logger.exception("加载自定义工具失败: %s", ct.get("name", ct))

        logger.info(
            "ToolManager: 加载 %d 个工具 (%d 内置 + %d 自定义)",
            len(tools),
            sum(1 for t in tools if type(t) in self.BUILTIN_TOOL_MAP.values()),
            len(tools) - sum(1 for t in tools if type(t) in self.BUILTIN_TOOL_MAP.values()),
        )
        return tools

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        """加载 YAML 配置文件"""
        if not path.exists():
            logger.warning("工具配置文件不存在: %s，使用空配置", path)
            return {}
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _load_custom_tool(entry: dict[str, Any]) -> ToolBase:
        """通过 module + class_name / func_name 动态导入工具

        Args:
            entry: 单个 custom_tools 条目

        Returns:
            ToolBase 实例

        Raises:
            ValueError: 条目格式错误
            ImportError: 模块不存在
            AttributeError: 类/函数不存在
        """
        module_path: str = entry["module"]
        mod = importlib.import_module(module_path)

        # 类式工具：继承 ToolBase
        class_name = entry.get("class_name")
        if class_name:
            cls: type = getattr(mod, class_name)
            kwargs: dict = entry.get("kwargs", {})
            return cls(**kwargs)

        # 函数式工具：用 FunctionTool 包装
        func_name = entry.get("func_name")
        if func_name:
            func = getattr(mod, func_name)
            return FunctionTool(
                func=func,
                name=entry.get("name"),
                description=entry.get("description", func.__doc__ or ""),
                is_read_only=entry.get("is_read_only", False),
            )

        raise ValueError(
            f"custom_tools 条目必须指定 class_name 或 func_name: {entry}"
        )
