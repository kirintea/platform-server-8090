# -*- coding: utf-8 -*-

"""精确 Token 计数器 — 基于 tiktoken

替代框架默认的 len(text.encode("utf-8")) // 4 粗略估算，
对 CJK 文本（中文 3-4 bytes/字）有更准确的计数。

使用方式：
    from core.token_counter import count_tokens
    tokens = count_tokens("你好世界", model_name="glm-5")
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken
from loguru import logger

# 模型名前缀 → tiktoken 编码器映射
ENCODER_MAP: dict[str, str] = {
    "gpt-4o": "o200k_base",
    "gpt-4": "cl100k_base",
    "glm": "cl100k_base",
    "qwen": "cl100k_base",
    "deepseek": "cl100k_base",
    "default": "cl100k_base",
}


@lru_cache(maxsize=8)
def get_encoder(model_name: str) -> tiktoken.Encoding:
    """获取 tiktoken 编码器（带缓存）

    Args:
        model_name: 模型名称

    Returns:
        tiktoken 编码器实例
    """
    model_lower = model_name.lower()
    for prefix, encoding in ENCODER_MAP.items():
        if prefix in model_lower:
            return tiktoken.get_encoding(encoding)
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model_name: str = "default") -> int:
    """精确 token 计数

    Args:
        text: 要计数的文本
        model_name: 模型名称（用于选择编码器）

    Returns:
        token 数量
    """
    if not text:
        return 0
    try:
        encoder = get_encoder(model_name)
        return len(encoder.encode(text))
    except Exception:
        # fallback: 字节估算
        logger.debug("tiktoken 计数失败，使用字节估算 fallback")
        return len(text.encode("utf-8")) // 4
