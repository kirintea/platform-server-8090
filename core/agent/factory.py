# -*- coding: utf-8 -*-

"""Agent 工厂 — 从 AppConfig 创建完整的 Agent 实例

职责：
- 根据 LLMConfig 创建模型实例
- 组装 Toolkit（内置工具 + MCP + Skills）
- 配置中间件（Tracing + ToolGuard）
- 设置 ReAct / Context / Injection 配置
"""

from __future__ import annotations

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.middleware import TracingMiddleware
from agentscope.model import OpenAIChatModel
from agentscope.state import AgentState
from agentscope.tool import (
    Bash,
    Edit,
    Glob,
    Grep,
    Read,
    TaskCreate,
    TaskGet,
    TaskList,
    TaskUpdate,
    Toolkit,
    Write,
)
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
from agentscope.skill import LocalSkillLoader

from core.config.schemas import AppConfig, LLMConfig, MCPConfig
from core.formatter import SiliconFlowFormatter
from middleware.tool_guard import ToolGuardMiddleware


class AgentFactory:
    """从配置创建 Agent 实例的工厂类"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def create(config: AppConfig, state: AgentState | None = None) -> Agent:
        """根据配置创建完整的 Agent 实例

        Args:
            config: 应用配置
            state: 已有的 AgentState（从 Redis 恢复时使用），为 None 则创建新状态

        Returns:
            配置好的 Agent 实例
        """
        # 1. 创建模型
        model = AgentFactory._create_model(config.llm)

        # 2. 创建 Toolkit
        toolkit = AgentFactory._create_toolkit(config)

        # 3. 创建中间件列表
        middlewares = AgentFactory._create_middlewares(config)

        # 4. 创建 Agent 配置
        react_config = ReActConfig(
            max_iters=config.agent.max_iters,
            stop_on_reject=False,
        )

        context_config = ContextConfig(
            trigger_ratio=config.agent.context_trigger_ratio,
            reserve_ratio=config.agent.context_reserve_ratio,
        )

        injection_config = InjectionConfig(
            timezone="Asia/Shanghai",
        )

        # 5. 创建 Agent（传入已有状态用于会话恢复）
        agent = Agent(
            name=config.agent.name,
            system_prompt=config.agent.system_prompt,
            model=model,
            toolkit=toolkit,
            middlewares=middlewares,
            react_config=react_config,
            context_config=context_config,
            injection_config=injection_config,
            state=state,
        )

        # 6. 设置权限模式
        if config.agent.permission_mode == "bypass":
            from agentscope.permission import PermissionMode
            agent.state.permission_context.mode = PermissionMode.BYPASS

        return agent

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _create_model(llm_config: LLMConfig) -> OpenAIChatModel:
        """创建 LLM 模型实例

        当前支持 OpenAI 兼容协议（覆盖大多数国产 LLM 代理）。
        后续可根据 provider 字段扩展 DashScope / Anthropic 等。

        特殊处理：
        - SiliconFlow API 需要自定义 Formatter 扁平化 content 格式
        """
        credential = OpenAICredential(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        )

        # 根据 API 地址选择合适的 Formatter
        formatter = None
        if "siliconflow" in llm_config.base_url.lower():
            formatter = SiliconFlowFormatter()

        return OpenAIChatModel(
            credential=credential,
            model=llm_config.model,
            stream=llm_config.stream,
            context_size=llm_config.context_size,
            formatter=formatter,
            parameters=OpenAIChatModel.Parameters(
                max_tokens=llm_config.max_tokens,
                temperature=llm_config.temperature,
                parallel_tool_calls=True,
            ),
        )

    @staticmethod
    def _create_toolkit(config: AppConfig) -> Toolkit:
        """组装 Toolkit：内置工具 + 任务管理 + MCP + Skills"""
        # 内置工具
        builtin_tools = [
            Bash(),
            Read(),
            Write(),
            Edit(),
            Glob(),
            Grep(),
            # 任务规划工具（支持复杂长任务拆解）
            TaskCreate(),
            TaskList(),
            TaskGet(),
            TaskUpdate(),
        ]

        # MCP 客户端
        mcp_clients = AgentFactory._create_mcp_clients(config.mcp_servers)

        # Skills 加载器
        skill_loaders = []
        try:
            loader = LocalSkillLoader(directory="./skills", scan_subdir=True)
            skill_loaders.append(loader)
        except Exception:
            pass  # skills 目录不存在时忽略

        return Toolkit(
            tools=builtin_tools,
            mcps=mcp_clients or None,
            skills_or_loaders=skill_loaders or None,
        )

    @staticmethod
    def _create_mcp_clients(mcp_configs: list[MCPConfig]) -> list[MCPClient]:
        """从配置创建 MCP 客户端列表"""
        clients = []
        for cfg in mcp_configs:
            if cfg.transport == "stdio" and cfg.command:
                mcp_config = StdioMCPConfig(
                    command=cfg.command,
                    args=cfg.args or None,
                )
                clients.append(MCPClient(config=mcp_config))
            elif cfg.transport == "http" and cfg.url:
                mcp_config = HttpMCPConfig(
                    url=cfg.url,
                    headers=cfg.headers or None,
                )
                clients.append(MCPClient(config=mcp_config))
        return clients

    @staticmethod
    def _create_middlewares(config: AppConfig) -> list:
        """创建中间件列表"""
        middlewares = []

        # OTel 追踪中间件（仅在启用时添加）
        if config.otel.enabled:
            middlewares.append(TracingMiddleware())

        # 工具守卫中间件
        if config.agent.tool_guard.enabled:
            middlewares.append(ToolGuardMiddleware(config.agent.tool_guard))

        return middlewares
