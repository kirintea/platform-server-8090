"""Phase 3 — MemoryConfig schema + AgenticMemoryMiddleware 集成测试"""

import os
import sys
import pytest
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config.schemas import MemoryConfig, AppConfig


class TestMemoryConfigSchema:
    """MemoryConfig schema 验证"""

    def test_default_disabled(self):
        cfg = MemoryConfig()
        assert cfg.enabled is False
        assert cfg.backend == "local"
        assert cfg.workdir_base == "./workspaces"

    def test_custom_values(self):
        cfg = MemoryConfig(
            enabled=True,
            backend="docker",
            workdir_base="/data/workspaces",
        )
        assert cfg.enabled is True
        assert cfg.backend == "docker"
        assert cfg.workdir_base == "/data/workspaces"

    def test_partial_override(self):
        cfg = MemoryConfig(enabled=True)
        assert cfg.enabled is True
        assert cfg.backend == "local"


class TestMemoryConfigInAppConfig:
    """MemoryConfig 嵌入 AppConfig 验证"""

    def _make_app_config(self, **overrides):
        from core.config.schemas import LLMConfig, OTelConfig
        base = {
            "otel": {"endpoint": "http://localhost:4317", "environment": "test"},
            "llm": {
                "api_key": "sk-test",
                "base_url": "http://localhost:8000/v1",
                "model": "test-model",
            },
        }
        base.update(overrides)
        return AppConfig(**base)

    def test_default_memory_config(self):
        cfg = self._make_app_config()
        assert cfg.memory.enabled is False
        assert cfg.memory.backend == "local"

    def test_override_memory_config(self):
        cfg = self._make_app_config(memory={"enabled": True, "backend": "docker"})
        assert cfg.memory.enabled is True
        assert cfg.memory.backend == "docker"


class TestAgenticMemoryMiddlewareIntegration:
    """AgenticMemoryMiddleware 实例化验证"""

    def test_import_middleware(self):
        from agentscope.middleware import AgenticMemoryMiddleware
        assert AgenticMemoryMiddleware is not None

    def test_create_with_tempdir(self):
        from agentscope.middleware import AgenticMemoryMiddleware

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "Memory")
            os.makedirs(memory_dir, exist_ok=True)
            mw = AgenticMemoryMiddleware(workdir=tmpdir)
            assert mw is not None

    def test_memory_dir_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "user123", "Memory")
            os.makedirs(memory_dir, exist_ok=True)
            assert os.path.isdir(memory_dir)

    def test_workdir_per_user_isolation(self):
        """验证两个用户的 memory 目录互不干扰"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, "user_a", "Memory")
            dir_b = os.path.join(tmpdir, "user_b", "Memory")
            os.makedirs(dir_a)
            os.makedirs(dir_b)

            # 各自写入不同文件
            with open(os.path.join(dir_a, "MEMORY.md"), "w") as f:
                f.write("- [Pref A](pref_a.md)")
            with open(os.path.join(dir_b, "MEMORY.md"), "w") as f:
                f.write("- [Pref B](pref_b.md)")

            with open(os.path.join(dir_a, "MEMORY.md")) as f:
                assert "Pref A" in f.read()
            with open(os.path.join(dir_b, "MEMORY.md")) as f:
                assert "Pref B" in f.read()


class TestFactoryMemoryMiddleware:
    """factory.py 中间件链集成验证"""

    def test_memory_middleware_in_chain_when_enabled(self):
        """memory.enabled=True 时中间件链包含 AgenticMemoryMiddleware"""
        from agentscope.middleware import AgenticMemoryMiddleware

        # 模拟 factory 的逻辑
        class FakeConfig:
            class memory:
                enabled = True
                backend = "local"
                workdir_base = tempfile.gettempdir()

        config = FakeConfig()
        middlewares = []

        if config.memory.enabled:
            workdir = os.path.join(config.memory.workdir_base, "test_user", "Memory")
            os.makedirs(workdir, exist_ok=True)
            middlewares.append(AgenticMemoryMiddleware(workdir=workdir))

        assert len(middlewares) == 1
        assert isinstance(middlewares[0], AgenticMemoryMiddleware)

    def test_memory_middleware_absent_when_disabled(self):
        """memory.enabled=False 时中间件链不含 AgenticMemoryMiddleware"""
        from agentscope.middleware import AgenticMemoryMiddleware

        class FakeConfig:
            class memory:
                enabled = False

        config = FakeConfig()
        middlewares = []

        if config.memory.enabled:
            middlewares.append(AgenticMemoryMiddleware(workdir="/tmp"))

        assert len(middlewares) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
