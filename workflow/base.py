# -*- coding: utf-8 -*-

"""业务工作流引擎 — 预留扩展点

设计思路：
- WorkflowBase 定义工作流抽象接口
- 具体工作流继承并实现 execute() 方法
- 通过配置文件声明式加载和注册
- 可在 Agent 的 on_reply 前后钩子中触发

示例用法：
    class MyWorkflow(WorkflowBase):
        name = "data_analysis"
        description = "数据分析工作流"

        async def execute(self, context: dict) -> dict:
            # Step 1: 解析数据
            # Step 2: 调用 Agent 分析
            # Step 3: 格式化输出
            return {"result": "..."}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WorkflowBase(ABC):
    """工作流基类

    所有自定义工作流必须继承此类并实现 execute() 方法。
    """

    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """执行工作流

        Args:
            context: 执行上下文，包含:
                - agent: Agent 实例
                - message: 用户消息
                - session_id: 会话 ID
                - config: AppConfig
                - 其他业务数据

        Returns:
            工作流执行结果字典
        """
        ...

    async def on_start(self, context: dict[str, Any]) -> None:
        """工作流开始前的钩子（可选覆盖）"""
        pass

    async def on_end(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        """工作流结束后的钩子（可选覆盖）"""
        pass

    async def on_error(self, context: dict[str, Any], error: Exception) -> None:
        """工作流异常时的钩子（可选覆盖）"""
        pass


class SimpleSequentialWorkflow(WorkflowBase):
    """顺序执行多个步骤的简单工作流示例"""

    name = "sequential"
    description = "按顺序执行多个步骤"

    def __init__(self, steps: list[Any] | None = None) -> None:
        self.steps = steps or []

    async def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        results = []
        for step in self.steps:
            result = await step(context)
            results.append(result)
            context["last_result"] = result
        return {"steps": results}
