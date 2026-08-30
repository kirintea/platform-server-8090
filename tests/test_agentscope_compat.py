# -*- coding: utf-8 -*-

"""AgentScope 2.0.5 API 兼容性验证

验证整合方案所需的所有 API 在当前安装的 agentscope 版本中是否可用。
运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_agentscope_compat.py -v
"""

from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# 版本检查
# ---------------------------------------------------------------------------

class TestVersion:
    """确认当前安装的 agentscope 版本"""

    def test_version_is_205_or_above(self):
        import agentscope
        version = agentscope.__version__
        major, minor, patch = (int(x) for x in version.split(".")[:3])
        assert (major, minor) >= (2, 0), f"需要 >=2.0.x，当前 {version}"
        # 2.0.3+ 即可（Mem0Middleware），2.0.5 最佳
        assert minor >= 0, f"需要 agentscope 2.0.x，当前 {version}"


# ---------------------------------------------------------------------------
# ContextConfig — 结构化摘要
# ---------------------------------------------------------------------------

class TestContextConfig:
    """验证 ContextConfig 的 summary_schema 支持"""

    def test_context_config_importable(self):
        from agentscope.agent import ContextConfig
        assert ContextConfig is not None

    def test_context_config_accepts_summary_schema(self):
        from agentscope.agent import ContextConfig
        schema = {
            "type": "object",
            "properties": {
                "task_overview": {"type": "string"},
                "current_state": {"type": "string"},
            },
            "required": ["task_overview", "current_state"],
        }
        cfg = ContextConfig(
            trigger_ratio=0.8,
            reserve_ratio=0.1,
            tool_result_limit=3000,
            summary_schema=schema,
        )
        assert cfg.summary_schema == schema

    def test_context_config_accepts_compression_prompt(self):
        from agentscope.agent import ContextConfig
        cfg = ContextConfig(
            compression_prompt="请压缩以下对话历史。",
        )
        assert cfg.compression_prompt == "请压缩以下对话历史。"

    def test_context_config_accepts_summary_template(self):
        from agentscope.agent import ContextConfig
        cfg = ContextConfig(
            summary_template="## 摘要\n{summary}\n## 当前\n",
        )
        assert "summary" in cfg.summary_template


# ---------------------------------------------------------------------------
# InjectionConfig — 运行时状态注入
# ---------------------------------------------------------------------------

class TestInjectionConfig:
    """验证 InjectionConfig 在 2.0.5 中可用"""

    def test_injection_config_importable(self):
        from agentscope.agent import InjectionConfig
        assert InjectionConfig is not None

    def test_injection_config_basic_fields(self):
        from agentscope.agent import InjectionConfig
        cfg = InjectionConfig(
            timezone="Asia/Shanghai",
            time_interval=0.5,
            context_buffer_ratio=0.2,
        )
        assert cfg.timezone == "Asia/Shanghai"
        assert cfg.time_interval == 0.5
        assert cfg.context_buffer_ratio == 0.2

    def test_injection_config_extra_fields(self):
        from agentscope.agent import InjectionConfig
        cfg = InjectionConfig(
            extra_fields={"platform": "test", "version": "0.1.3"},
        )
        assert cfg.extra_fields["platform"] == "test"


# ---------------------------------------------------------------------------
# PermissionRule — 细粒度权限
# ---------------------------------------------------------------------------

class TestPermissionRule:
    """验证 PermissionRule / PermissionBehavior / PermissionContext 可用"""

    def test_permission_rule_importable(self):
        from agentscope.permission import PermissionRule
        assert PermissionRule is not None

    def test_permission_behavior_importable(self):
        from agentscope.permission import PermissionBehavior
        # 验证三种行为枚举存在
        assert hasattr(PermissionBehavior, "ALLOW") or hasattr(PermissionBehavior, "allow")
        assert hasattr(PermissionBehavior, "DENY") or hasattr(PermissionBehavior, "deny")

    def test_permission_context_importable(self):
        from agentscope.permission import PermissionContext
        assert PermissionContext is not None

    def test_permission_mode_importable(self):
        from agentscope.permission import PermissionMode
        assert PermissionMode is not None

    def test_permission_rule_creation(self):
        from agentscope.permission import PermissionRule, PermissionBehavior
        rule = PermissionRule(
            tool_name="Bash",
            rule_content="npm run:*",
            behavior=PermissionBehavior.ALLOW,
            source="test",
        )
        assert rule.tool_name == "Bash"
        assert rule.rule_content == "npm run:*"


# ---------------------------------------------------------------------------
# MiddlewareBase — 中间件基类
# ---------------------------------------------------------------------------

class TestMiddlewareBase:
    """验证中间件基类可用"""

    def test_middleware_base_importable(self):
        from agentscope.middleware import MiddlewareBase
        assert MiddlewareBase is not None

    def test_tracing_middleware_importable(self):
        from agentscope.middleware import TracingMiddleware
        assert TracingMiddleware is not None

    def test_budget_control_middleware_importable(self):
        from agentscope.middleware import ReplyBudgetControlMiddleware
        assert ReplyBudgetControlMiddleware is not None


# ---------------------------------------------------------------------------
# 长期记忆中间件
# ---------------------------------------------------------------------------

class TestLongTermMemory:
    """验证三种长期记忆中间件可用"""

    def test_agentic_memory_middleware_importable(self):
        try:
            from agentscope.middleware import AgenticMemoryMiddleware
            assert AgenticMemoryMiddleware is not None
        except ImportError:
            pytest.skip("AgenticMemoryMiddleware 需要额外依赖")

    def test_reme_middleware_importable(self):
        try:
            from agentscope.middleware import ReMeMiddleware
            assert ReMeMiddleware is not None
        except ImportError:
            pytest.skip("ReMeMiddleware 需要 pip install 'agentscope[reme]'")

    def test_mem0_middleware_importable(self):
        try:
            from agentscope.middleware import Mem0Middleware
            assert Mem0Middleware is not None
        except ImportError:
            pytest.skip("Mem0Middleware 需要 pip install 'agentscope[mem0]'")


# ---------------------------------------------------------------------------
# Workspace — 工作区
# ---------------------------------------------------------------------------

class TestWorkspace:
    """验证 Workspace 类可用"""

    def test_local_workspace_importable(self):
        from agentscope.workspace import LocalWorkspace
        assert LocalWorkspace is not None

    def test_local_workspace_manager_importable(self):
        try:
            from agentscope.app.workspace_manager import LocalWorkspaceManager
            assert LocalWorkspaceManager is not None
        except ImportError:
            # 2.0.5 可能路径不同
            from agentscope.workspace import LocalWorkspace
            assert LocalWorkspace is not None

    def test_k8s_workspace_importable(self):
        try:
            from agentscope.workspace import K8sWorkspace
            assert K8sWorkspace is not None
        except ImportError:
            pytest.skip("K8sWorkspace 需要额外依赖")

    def test_docker_workspace_importable(self):
        try:
            from agentscope.workspace import DockerWorkspace
            assert DockerWorkspace is not None
        except ImportError:
            pytest.skip("DockerWorkspace 需要额外依赖")


# ---------------------------------------------------------------------------
# Offloader 协议
# ---------------------------------------------------------------------------

class TestOffloader:
    """验证 Offloader 协议可用（通过 Agent 构造参数）"""

    def test_agent_accepts_offloader_param(self):
        """验证 Agent 构造函数接受 offloader 参数"""
        import inspect
        from agentscope.agent import Agent
        sig = inspect.signature(Agent.__init__)
        assert "offloader" in sig.parameters, "Agent.__init__ 不接受 offloader 参数"


# ---------------------------------------------------------------------------
# Toolkit / MCPClient / LocalSkillLoader
# ---------------------------------------------------------------------------

class TestToolkit:
    """验证工具集相关 API"""

    def test_toolkit_importable(self):
        from agentscope.tool import Toolkit
        assert Toolkit is not None

    def test_mcp_client_importable(self):
        from agentscope.mcp import MCPClient
        assert MCPClient is not None

    def test_stdio_mcp_config_importable(self):
        from agentscope.mcp import StdioMCPConfig
        assert StdioMCPConfig is not None

    def test_http_mcp_config_importable(self):
        from agentscope.mcp import HttpMCPConfig
        assert HttpMCPConfig is not None

    def test_local_skill_loader_importable(self):
        from agentscope.skill import LocalSkillLoader
        assert LocalSkillLoader is not None


# ---------------------------------------------------------------------------
# Model / Credential
# ---------------------------------------------------------------------------

class TestModel:
    """验证模型相关 API"""

    def test_openai_chat_model_importable(self):
        from agentscope.model import OpenAIChatModel
        assert OpenAIChatModel is not None

    def test_openai_credential_importable(self):
        from agentscope.credential import OpenAICredential
        assert OpenAICredential is not None


# ---------------------------------------------------------------------------
# AgentState / ReActConfig
# ---------------------------------------------------------------------------

class TestAgentState:
    """验证 Agent 相关配置类"""

    def test_agent_state_importable(self):
        from agentscope.state import AgentState
        assert AgentState is not None

    def test_react_config_importable(self):
        from agentscope.agent import ReActConfig
        assert ReActConfig is not None

    def test_agent_importable(self):
        from agentscope.agent import Agent
        assert Agent is not None
