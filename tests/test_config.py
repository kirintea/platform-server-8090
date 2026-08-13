# -*- coding: utf-8 -*-

"""配置管理模块测试"""

import os

import pytest
import yaml
from pydantic import ValidationError

from core.config.schemas import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    MCPConfig,
    OTelConfig,
    ServerConfig,
    ToolGuardConfig,
)
from core.config.resolver import EnvVarResolver
from core.config.manager import ConfigManager


# ============================================================
# Schema 测试
# ============================================================

class TestOTelConfig:
    def test_valid(self):
        cfg = OTelConfig(endpoint="http://localhost:4317", environment="development")
        assert cfg.enabled is True
        assert cfg.service_name == "platform-agent"

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            OTelConfig()


class TestLLMConfig:
    def test_valid(self):
        cfg = LLMConfig(api_key="sk-xxx", base_url="http://llm/v1", model="glm-5")
        assert cfg.provider == "openai"
        assert cfg.stream is True


class TestToolGuardConfig:
    def test_defaults(self):
        cfg = ToolGuardConfig()
        assert cfg.enabled is False
        assert cfg.mode == "blocklist"
        assert cfg.tools == []


class TestAppConfig:
    def test_full_config(self):
        cfg = AppConfig(
            otel=OTelConfig(endpoint="http://localhost:4317", environment="dev"),
            llm=LLMConfig(api_key="sk-xxx", base_url="http://llm/v1", model="glm-5"),
        )
        assert cfg.server.port == 8090
        assert cfg.agent.name == "platform_agent"
        assert cfg.mcp_servers == []


# ============================================================
# 环境变量解析测试
# ============================================================

class TestEnvVarResolver:
    def test_simple(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "hello")
        result = EnvVarResolver.resolve({"k": "${TEST_KEY}"})
        assert result["k"] == "hello"

    def test_default_value(self):
        result = EnvVarResolver.resolve({"k": "${MISSING_VAR:-fallback}"})
        assert result["k"] == "fallback"

    def test_missing_keeps_placeholder(self):
        result = EnvVarResolver.resolve({"k": "${TOTALLY_MISSING}"})
        assert result["k"] == "${TOTALLY_MISSING}"

    def test_nested(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db.local")
        result = EnvVarResolver.resolve({"db": {"url": "pg://${DB_HOST}/test"}})
        assert result["db"]["url"] == "pg://db.local/test"

    def test_list(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        result = EnvVarResolver.resolve(["${A}", "b"])
        assert result == ["1", "b"]

    def test_non_string_passthrough(self):
        result = EnvVarResolver.resolve({"n": 42, "f": True})
        assert result == {"n": 42, "f": True}


# ============================================================
# ConfigManager 测试
# ============================================================

class TestConfigManager:
    def _write_yaml(self, path: str, data: dict):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def test_singleton(self):
        ConfigManager._instance = None
        a = ConfigManager.get_instance()
        b = ConfigManager.get_instance()
        assert a is b
        ConfigManager._instance = None

    def test_load_dev(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "dev.yaml")
        self._write_yaml(cfg_path, {
            "otel": {"endpoint": "http://localhost:4317", "environment": "dev"},
            "llm": {"api_key": "sk-test", "base_url": "http://llm/v1", "model": "test"},
        })
        monkeypatch.setattr(ConfigManager, "_get_config_path", staticmethod(lambda env: cfg_path))

        ConfigManager._instance = None
        ConfigManager._config = None
        cfg = ConfigManager.get_instance().load("dev")
        assert cfg.otel.environment == "dev"
        assert cfg.llm.api_key == "sk-test"
        ConfigManager._instance = None
        ConfigManager._config = None

    def test_env_resolution(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "dev.yaml")
        self._write_yaml(cfg_path, {
            "otel": {"endpoint": "http://localhost:4317", "environment": "dev"},
            "llm": {"api_key": "${MY_KEY}", "base_url": "http://llm/v1", "model": "m"},
        })
        monkeypatch.setenv("MY_KEY", "sk-resolved")
        monkeypatch.setattr(ConfigManager, "_get_config_path", staticmethod(lambda env: cfg_path))

        ConfigManager._instance = None
        ConfigManager._config = None
        cfg = ConfigManager.get_instance().load("dev")
        assert cfg.llm.api_key == "sk-resolved"
        ConfigManager._instance = None
        ConfigManager._config = None
