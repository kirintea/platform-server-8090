# -*- coding: utf-8 -*-

"""Token 计数测试

覆盖 core/token_counter.py 的 count_tokens：
- 非空字符串返回 > 0
- 空串 / None / 非 str 返回 0
- 编码器选择（glm / gpt-4o / qwen）不抛异常
- 编码器失败时的 fallback 仍返回 int

tiktoken 可用时走正常路径；若 tiktoken 不可用，依赖 fallback 返回 int。
"""

import sys

import pytest

from core.token_counter import count_tokens, get_encoder


def test_count_tokens_nonempty_returns_positive():
    assert count_tokens("hello world") > 0
    assert count_tokens("你好世界，这是一段中文文本") > 0


def test_count_tokens_empty_returns_zero():
    assert count_tokens("") == 0
    assert count_tokens(None) == 0
    assert count_tokens(0) == 0


def test_get_encoder_selection_does_not_raise():
    for model in ("glm-5", "gpt-4o", "qwen-max", "deepseek-chat", "unknown-model"):
        enc = get_encoder(model)
        assert enc is not None
        # tiktoken.Encoding 必须具备 encode 方法
        assert hasattr(enc, "encode")


def test_count_tokens_returns_int_for_known_models():
    for model in ("glm-5", "gpt-4o", "qwen-max"):
        result = count_tokens("the quick brown fox", model_name=model)
        assert isinstance(result, int)
        assert result > 0


def test_count_tokens_fallback_returns_int(monkeypatch):
    """get_encoder 抛异常时，count_tokens 必须仍以字节估算返回 int。"""
    def _boom(_model_name):
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr("core.token_counter.get_encoder", _boom)
    result = count_tokens("some text")
    assert isinstance(result, int)
    assert result >= 0
