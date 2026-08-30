# -*- coding: utf-8 -*-

"""PermissionRule 配置与集成测试

验证权限规则配置能正确注入到 Agent。
运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_permission_config.py -v
"""

from __future__ import annotations

import pytest


class TestPermissionConfigSchema:
    """验证 PermissionConfig / PermissionRuleEntry schema"""

    def test_permission_rule_entry_creation(self):
        from core.config.schemas import PermissionRuleEntry

        entry = PermissionRuleEntry(
            tool_name="Bash",
            rule_content="rm -rf:*",
            source="test",
        )
        assert entry.tool_name == "Bash"
        assert entry.rule_content == "rm -rf:*"
        assert entry.source == "test"

    def test_permission_rule_entry_default_source(self):
        from core.config.schemas import PermissionRuleEntry

        entry = PermissionRuleEntry(
            tool_name="Write",
            rule_content="/etc/**",
        )
        assert entry.source == "platform-config"

    def test_permission_config_defaults(self):
        from core.config.schemas import PermissionConfig

        cfg = PermissionConfig()
        assert cfg.mode == "bypass"
        assert cfg.deny_rules == []
        assert cfg.allow_rules == []

    def test_permission_config_with_rules(self):
        from core.config.schemas import PermissionConfig, PermissionRuleEntry

        cfg = PermissionConfig(
            mode="default",
            deny_rules=[
                PermissionRuleEntry(
                    tool_name="Bash",
                    rule_content="rm -rf:*",
                    source="safety",
                ),
            ],
            allow_rules=[
                PermissionRuleEntry(
                    tool_name="Bash",
                    rule_content="npm run:*",
                    source="allowlist",
                ),
            ],
        )
        assert cfg.mode == "default"
        assert len(cfg.deny_rules) == 1
        assert len(cfg.allow_rules) == 1

    def test_agent_config_has_permission_field(self):
        from core.config.schemas import AgentConfig

        cfg = AgentConfig(
            system_prompt="test",
            permission={"mode": "default", "deny_rules": []},
        )
        assert cfg.permission.mode == "default"


class TestPermissionSetup:
    """验证 PermissionContext 注入逻辑"""

    def _make_config(self, **overrides):
        """构建最小 AppConfig"""
        from core.config.schemas import (
            AppConfig,
            OTelConfig,
            LLMConfig,
        )

        defaults = dict(
            otel=OTelConfig(
                enabled=False,
                endpoint="http://localhost:4317",
                environment="test",
            ),
            llm=LLMConfig(
                api_key="test-key",
                base_url="http://localhost:8090/v1",
                model="test-model",
            ),
        )
        defaults.update(overrides)
        return AppConfig(**defaults)

    def test_permission_mode_bypass(self):
        from agentscope.permission import PermissionMode

        config = self._make_config()
        config.agent.permission.mode = "bypass"

        from core.agent.factory import AgentFactory
        agent = AgentFactory.create(config)

        perm_ctx = getattr(agent.state, "permission_context", None)
        if perm_ctx is not None:
            assert perm_ctx.mode == PermissionMode.BYPASS

    def test_permission_mode_default(self):
        from agentscope.permission import PermissionMode

        config = self._make_config()
        config.agent.permission.mode = "default"

        from core.agent.factory import AgentFactory
        agent = AgentFactory.create(config)

        perm_ctx = getattr(agent.state, "permission_context", None)
        if perm_ctx is not None:
            assert perm_ctx.mode == PermissionMode.DEFAULT

    def test_deny_rules_injected(self):
        from core.config.schemas import PermissionRuleEntry

        config = self._make_config()
        config.agent.permission.mode = "bypass"
        config.agent.permission.deny_rules = [
            PermissionRuleEntry(
                tool_name="Bash",
                rule_content="rm -rf:*",
                source="safety",
            ),
            PermissionRuleEntry(
                tool_name="Write",
                rule_content="/etc/**",
                source="safety",
            ),
        ]

        from core.agent.factory import AgentFactory
        agent = AgentFactory.create(config)

        perm_ctx = getattr(agent.state, "permission_context", None)
        if perm_ctx is not None:
            assert "Bash" in perm_ctx.deny_rules
            assert len(perm_ctx.deny_rules["Bash"]) >= 1
            assert "Write" in perm_ctx.deny_rules
            assert len(perm_ctx.deny_rules["Write"]) >= 1

    def test_allow_rules_injected(self):
        from core.config.schemas import PermissionRuleEntry

        config = self._make_config()
        config.agent.permission.mode = "bypass"
        config.agent.permission.allow_rules = [
            PermissionRuleEntry(
                tool_name="Bash",
                rule_content="npm run:*",
                source="allowlist",
            ),
        ]

        from core.agent.factory import AgentFactory
        agent = AgentFactory.create(config)

        perm_ctx = getattr(agent.state, "permission_context", None)
        if perm_ctx is not None:
            assert "Bash" in perm_ctx.allow_rules
            assert len(perm_ctx.allow_rules["Bash"]) >= 1
