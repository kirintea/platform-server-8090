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
from agentscope.middleware import ReplyBudgetControlMiddleware, TracingMiddleware
from agentscope.model import OpenAIChatModel
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
from agentscope.skill import LocalSkillLoader
from pydantic import SecretStr

from core.config.schemas import AppConfig, LLMConfig, MCPConfig
from core.validators import coerce_id
from loguru import logger
from middleware.context_guard import ContextGuardMiddleware
from core.formatter import SiliconFlowFormatter
from core.workspace import LocalWorkspaceManager
from middleware.tool_guard import ToolGuardMiddleware
from middleware.command_guard import CommandGuardMiddleware
from middleware.tool_manager import ToolManagerMiddleware
from middleware.path_guard import PathGuardMiddleware
from middleware.docker_sandbox_proxy import DockerSandboxProxy


class AgentFactory:
    """从配置创建 Agent 实例的工厂类"""

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def create(
        config: AppConfig,
        state: AgentState | None = None,
        user_id: str | None = None,
    ) -> Agent:
        """根据配置创建完整的 Agent 实例

        Args:
            config: 应用配置
            state: 已有的 AgentState（从 Redis 恢复时使用），为 None 则创建新状态
            user_id: 用户标识（sandbox_per_user 启用时用于拼接沙箱路径）

        Returns:
            配置好的 Agent 实例
        """
        # 1. 创建模型
        model = AgentFactory._create_model(config.llm)

        # 1.1 接入精确 Token 计数（core.token_counter.count_tokens）
        # NOTE: agentscope 2.0.5 的 ContextConfig / OpenAIChatModel 是否接受
        # token_counter 回调需在实际环境中核实。此处仅做防御性接入与可导入性冒烟，
        # 若 API 不支持则静默降级，不影响 agent 创建。
        try:
            from core.token_counter import count_tokens

            _sys_token_est = count_tokens(
                config.agent.system_prompt or "",
                config.llm.model,
            )
            logger.debug(
                "AgentFactory: system_prompt 约 {} tokens (model={})",
                _sys_token_est,
                config.llm.model,
            )
            # TODO: 将 count_tokens 接入 ContextConfig 的 budget / token_counter 回调，
            # 使上下文压缩与预算控制使用精确计数而非字符启发式。
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "AgentFactory: count_tokens 接入失败（降级为框架默认估算）: {}",
                e,
            )

        # 2. 创建 Toolkit
        toolkit = AgentFactory._create_toolkit(config, user_id=user_id)

        # 3. 创建中间件列表
        middlewares = AgentFactory._create_middlewares(config, user_id=user_id)

        # 4. 创建工作区管理器（同时作为 Offloader）
        import os
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        if config.agent.sandbox_per_user and user_id:
            # 防止 user_id="/" 或 "../../" 等越界输入折叠沙箱根目录
            sandbox_dir = os.path.join(sandbox_dir, coerce_id(user_id))
        workspace_manager = LocalWorkspaceManager(base_dir=sandbox_dir)

        # 5. 创建 Agent 配置
        react_config = ReActConfig(
            max_iters=config.agent.max_iters,
            stop_on_reject=False,
        )

        context_config = ContextConfig(
            trigger_ratio=config.agent.context_trigger_ratio,
            reserve_ratio=config.agent.context_reserve_ratio,
            tool_result_limit=config.agent.tool_result_limit,
            compression_prompt=(
                "你是一个上下文压缩助手。请将以下对话历史压缩为结构化摘要。\n"
                "要求：\n"
                "1. 保留所有文件路径、API 端点、错误信息的完整原文\n"
                "2. 保留用户明确表达的偏好和约束\n"
                "3. 保留未完成的任务和下一步计划\n"
                "4. 使用绝对时间（如 2026-08-20 10:30），不要使用相对时间（如刚才）\n"
                "5. 使用完整路径（如 /api/sessions/{id}/messages），不要使用相对引用\n"
            ),
            summary_schema={
                "type": "object",
                "properties": {
                    "task_overview": {
                        "type": "string",
                        "description": "用户的核心需求和成功标准",
                    },
                    "current_state": {
                        "type": "string",
                        "description": "已完成的工作、修改的文件、关键输出",
                    },
                    "important_discoveries": {
                        "type": "string",
                        "description": "约束、决策、错误信息、失败的尝试",
                    },
                    "next_steps": {
                        "type": "string",
                        "description": "具体的待办事项、阻塞项、优先级",
                    },
                    "context_to_preserve": {
                        "type": "string",
                        "description": "用户偏好、领域细节、做出的承诺",
                    },
                    "tool_outputs_summary": {
                        "type": "string",
                        "description": "重要工具调用的结果摘要（文件内容、API 响应等）",
                    },
                },
                "required": [
                    "task_overview",
                    "current_state",
                    "important_discoveries",
                    "next_steps",
                    "context_to_preserve",
                    "tool_outputs_summary",
                ],
            },
        )

        injection_cfg = config.agent.injection
        injection_config = InjectionConfig(
            timezone=injection_cfg.timezone,
            time_interval=injection_cfg.time_interval,
            context_buffer_ratio=injection_cfg.context_buffer_ratio,
            emit_hint_event=injection_cfg.emit_hint_event,
            extra_fields=injection_cfg.extra_fields or {},
        )

        # 6. 创建 Agent（传入已有状态用于会话恢复，挂载 Offloader）
        agent = Agent(
            name=config.agent.name,
            system_prompt=config.agent.system_prompt,
            model=model,
            toolkit=toolkit,
            middlewares=middlewares,
            react_config=react_config,
            context_config=context_config,
            injection_config=injection_config,
            offloader=workspace_manager,
            state=state,
        )

        # 7. 设置权限模式 + 细粒度规则
        AgentFactory._setup_permission(config, agent)

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
            api_key=SecretStr(llm_config.api_key),
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
    def _create_toolkit(
        config: AppConfig,
        user_id: str | None = None,
    ) -> Toolkit:
        """组装 Toolkit：配置驱动的工具 + MCP + Skills"""
        import os

        # 从配置文件加载工具（替代硬编码列表）
        tool_manager = ToolManagerMiddleware(config.agent.tool_manager.config_path)
        tools = tool_manager.load_tools()

        # 沙箱根目录（按需追加 user_id，coerce_id 防止越界）
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        if config.agent.sandbox_per_user and user_id:
            sandbox_dir = os.path.join(sandbox_dir, coerce_id(user_id))

        # MCP 客户端
        if config.agent.sandbox_mcp:
            # 预留：从 sandbox_dir/mcp/ 加载用户级 MCP 配置。
            # 当前 MCP 配置仍统一来自 config.mcp_servers（yaml），
            # 此分支为保留位，暂不执行任何操作。
            pass  # reserved: per-user sandbox MCP loading not yet implemented
        mcp_clients = AgentFactory._create_mcp_clients(config.mcp_servers)

        # Skills 加载器
        skill_loaders = []
        if config.agent.sandbox_skills:
            skills_dir = os.path.join(sandbox_dir, "skills")
        else:
            skills_dir = "./skills"
        try:
            loader = LocalSkillLoader(directory=skills_dir, scan_subdir=True)
            skill_loaders.append(loader)
        except Exception:
            pass  # skills 目录不存在时忽略

        return Toolkit(
            tools=tools,
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
                clients.append(MCPClient(config=mcp_config)) # type: ignore
            elif cfg.transport == "http" and cfg.url:
                mcp_config = HttpMCPConfig(
                    url=cfg.url,
                    headers=cfg.headers or None,
                )
                clients.append(MCPClient(config=mcp_config)) # type: ignore
        return clients

    @staticmethod
    def _create_middlewares(
        config: AppConfig,
        user_id: str | None = None,
    ) -> list:
        """创建中间件列表"""
        middlewares = []

        # OTel 追踪中间件（仅在启用时添加）
        if config.otel.enabled:
            middlewares.append(TracingMiddleware())

        # 工具守卫中间件（工具名级）
        if config.agent.tool_guard.enabled:
            middlewares.append(ToolGuardMiddleware(config.agent.tool_guard))

        # 命令内容守卫中间件（命令内容级）
        if config.agent.command_guard.enabled:
            middlewares.append(CommandGuardMiddleware(
                enabled=True,
                mode=config.agent.command_guard.mode,
                rules=config.agent.command_guard.rules,
            ))

        # 沙箱路径守卫中间件（路径级）— 始终启用
        import os
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        if config.agent.sandbox_per_user and user_id:
            sandbox_dir = os.path.join(sandbox_dir, coerce_id(user_id))

        # Docker 沙箱代理（执行环境级）— sandbox.backend="docker" 时启用
        if config.sandbox.backend == "docker":
            host_project_root = os.path.abspath(".")
            middlewares.append(DockerSandboxProxy(
                sandbox_config=config.sandbox,
                host_project_root=host_project_root,
            ))

        middlewares.append(PathGuardMiddleware(sandbox_dir=sandbox_dir))

        # 长期记忆中间件 — AgenticMemory（Markdown 文件型，按用户隔离）
        if config.memory.enabled:
            from agentscope.middleware import AgenticMemoryMiddleware

            memory_base = os.path.abspath(config.memory.workdir_base)
            memory_workdir = memory_base
            if user_id:
                memory_workdir = os.path.join(memory_base, coerce_id(user_id), "Memory")
            os.makedirs(memory_workdir, exist_ok=True)

            memory_kwargs: dict = {"workdir": memory_workdir}
            if config.memory.backend != "local":
                memory_kwargs["backend"] = config.memory.backend
            middlewares.append(AgenticMemoryMiddleware(**memory_kwargs))

        # 回复预算控制中间件 — 防止单次回复消耗过多 token
        budget_cfg = config.middleware.budget_control
        if budget_cfg.enabled:
            middlewares.append(ReplyBudgetControlMiddleware(
                token_budget=budget_cfg.token_budget,
                input_token_weight=budget_cfg.input_weight,
                output_token_weight=budget_cfg.output_weight,
                hint_message=(
                    "你本次回复的 token 预算即将耗尽。"
                    "请尽快总结当前进展并给出结论，不要再调用工具。"
                ),
            ))

        # 压缩守卫中间件 — 确保内容先卸载再丢弃
        middlewares.append(ContextGuardMiddleware())

        return middlewares

    @staticmethod
    def _setup_permission(config: AppConfig, agent: Agent) -> None:
        """设置权限模式与细粒度规则

        优先使用 config.agent.permission（新字段），
        向后兼容 config.agent.permission_mode（旧字段）。
        """
        from agentscope.permission import (
            PermissionBehavior,
            PermissionContext,
            PermissionMode,
            PermissionRule,
        )

        perm_cfg = config.agent.permission

        # 解析权限模式
        mode_str = perm_cfg.mode or config.agent.permission_mode
        mode_map = {
            "default": PermissionMode.DEFAULT,
            "accept_edits": PermissionMode.ACCEPT_EDITS,
            "explore": PermissionMode.EXPLORE,
            "bypass": PermissionMode.BYPASS,
            "dont_ask": PermissionMode.DONT_ASK,
        }
        mode = mode_map.get(mode_str, PermissionMode.BYPASS)

        # 构建拒绝规则
        deny_rules: dict[str, list[PermissionRule]] = {}
        for entry in perm_cfg.deny_rules:
            rule = PermissionRule(
                tool_name=entry.tool_name,
                rule_content=entry.rule_content,
                behavior=PermissionBehavior.DENY,
                source=entry.source,
            )
            deny_rules.setdefault(entry.tool_name, []).append(rule)

        # 构建允许规则
        allow_rules: dict[str, list[PermissionRule]] = {}
        for entry in perm_cfg.allow_rules:
            rule = PermissionRule(
                tool_name=entry.tool_name,
                rule_content=entry.rule_content,
                behavior=PermissionBehavior.ALLOW,
                source=entry.source,
            )
            allow_rules.setdefault(entry.tool_name, []).append(rule)

        # 注入到 agent.state.permission_context
        perm_ctx = getattr(agent.state, "permission_context", None)
        if perm_ctx is not None:
            perm_ctx.mode = mode
            if deny_rules:
                for tool_name, rules in deny_rules.items():
                    existing = perm_ctx.deny_rules.get(tool_name, [])
                    perm_ctx.deny_rules[tool_name] = existing + rules
            if allow_rules:
                for tool_name, rules in allow_rules.items():
                    existing = perm_ctx.allow_rules.get(tool_name, [])
                    perm_ctx.allow_rules[tool_name] = existing + rules
            logger.info(
                "AgentFactory: 权限已设置 mode={}, deny_rules={}, allow_rules={}",
                mode_str,
                sum(len(v) for v in deny_rules.values()),
                sum(len(v) for v in allow_rules.values()),
            )
        else:
            logger.warning(
                "AgentFactory: agent.state 缺少 permission_context，跳过权限设置"
            )
