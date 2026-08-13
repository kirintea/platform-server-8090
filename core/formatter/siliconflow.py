# -*- coding: utf-8 -*-

"""SiliconFlow 兼容 Formatter

SiliconFlow 不支持 OpenAI 多模态 content list 格式，
需要将单 TextBlock 扁平化为纯字符串。
"""

from __future__ import annotations

from typing import Any

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg


class SiliconFlowFormatter(OpenAIChatFormatter):
    """兼容 SiliconFlow API 的 Formatter。

    将只含单个 TextBlock 的 content 从 list 格式扁平化为纯字符串。
    """

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        formatted = await super().format(msgs)
        for msg in formatted:
            content = msg.get("content")
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
                and "text" in content[0]
            ):
                msg["content"] = content[0]["text"]
        return formatted
