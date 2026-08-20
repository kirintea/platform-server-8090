# -*- coding: utf-8 -*-

"""LLM API 可达性检查

可单独执行: python health_check/check_llm.py
"""

from __future__ import annotations

import sys
import os
import json
import urllib.request
import urllib.error

from utils import CheckReport, load_config


def check_llm_endpoint(base_url: str, api_key: str, model: str, report: CheckReport):
    """检查 LLM API 端点可达性"""
    try:
        # 尝试获取模型列表（兼容 OpenAI 格式）
        req = urllib.request.Request(
            f"{base_url}/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            models = [m.get("id", "") for m in data.get("data", [])]
            if models:
                report.add("LLM API 端点", True, f"{len(models)} 个模型可用")
            else:
                report.add("LLM API 端点", True, "端点可达（模型列表为空）")
    except urllib.error.HTTPError as e:
        # 401/403 说明端点可达但认证失败
        if e.code in (401, 403):
            report.add("LLM API 端点", True, f"{e.code} 端点可达（认证问题）")
        elif e.code == 404:
            # 非 OpenAI 网关（如 DashScope/Anthropic）无 /models 接口，属正常，
            # 降级为非致命警告并跳过该子检查，不计入失败
            report.add(
                "LLM API 端点",
                True,
                f"404 网关无 /models 接口（非 OpenAI 兼容），跳过该子检查",
            )
        else:
            report.add("LLM API 端点", False, "", f"{e.code} {e.reason}")
    except Exception as e:
        report.add("LLM API 端点", False, "", str(e))


def check_llm_completion(base_url: str, api_key: str, model: str, report: CheckReport):
    """检查 LLM 能否完成一次简单请求"""
    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
        }).encode()

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            report.add("LLM 补全请求", True, f'model={model} | "{content[:30]}"')
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:100]
        except Exception:
            pass
        report.add("LLM 补全请求", False, "", f"{e.code} {body}")
    except Exception as e:
        report.add("LLM 补全请求", False, "", str(e))


def run():
    """运行 LLM 检查"""
    config = load_config()

    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL_NAME", "")

    if config:
        base_url = base_url or getattr(config.llm, "base_url", "")
        api_key = api_key or getattr(config.llm, "api_key", "")
        model = model or getattr(config.llm, "model", "")

    report = CheckReport()

    if not base_url:
        report.add("LLM API", False, "", "LLM_BASE_URL 未配置")
        return report.print_report()

    if not api_key:
        report.add("LLM API", False, "", "LLM_API_KEY 未配置")
        return report.print_report()

    check_llm_endpoint(base_url, api_key, model, report)
    check_llm_completion(base_url, api_key, model, report)

    return report.print_report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
