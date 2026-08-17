# -*- coding: utf-8 -*-

"""HTTP 服务 + API 端点检查

可单独执行: python health_check/check_http.py
"""

from __future__ import annotations

import sys
import urllib.request
import urllib.error
import json

from utils import CheckReport, load_config


def check_http_health(base_url: str, report: CheckReport):
    """检查 HTTP 服务可达性"""
    try:
        req = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            report.add(
                "HTTP 服务",
                True,
                f"{resp.status} OK | active_sessions={data.get('active_sessions', '?')}",
            )
    except urllib.error.URLError as e:
        report.add("HTTP 服务", False, "", str(e))
    except Exception as e:
        report.add("HTTP 服务", False, "", str(e))


def check_api_endpoints(base_url: str, report: CheckReport):
    """检查各 API 端点可达性"""
    endpoints = [
        ("GET",  "/agent",          "Agent 列表"),
        ("GET",  "/mcp",            "MCP 列表"),
        ("GET",  "/skill",          "Skill 列表"),
        ("GET",  "/schedule",       "定时任务列表"),
        ("GET",  "/sessions",       "会话列表"),
        ("GET",  "/webui/health",   "WebUI 健康检查"),
    ]

    for method, path, name in endpoints:
        try:
            req = urllib.request.Request(
                f"{base_url}{path}",
                method=method,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                report.add(f"API {name}", True, f"{resp.status}")
        except urllib.error.HTTPError as e:
            # 4xx/5xx 也算端点可达
            if e.code < 500:
                report.add(f"API {name}", True, f"{e.code} (业务错误，端点可达)")
            else:
                report.add(f"API {name}", False, "", f"{e.code} {e.reason}")
        except Exception as e:
            report.add(f"API {name}", False, "", str(e))


def run():
    """运行 HTTP 检查"""
    config = load_config()
    host = config.server.host if config else "0.0.0.0"
    port = config.server.port if config else 8090

    # 用 localhost 做健康检查（避免 0.0.0.0 绑定问题）
    base_url = f"http://localhost:{port}"

    report = CheckReport()
    check_http_health(base_url, report)
    check_api_endpoints(base_url, report)
    return report.print_report()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
