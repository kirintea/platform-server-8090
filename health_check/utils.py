# -*- coding: utf-8 -*-

"""共用工具 — 输出格式、配置加载"""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from typing import Any

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class CheckResult:
    """单项检查结果"""
    name: str
    passed: bool
    detail: str = ""
    error: str | None = None


@dataclass
class CheckReport:
    """检查报告"""
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", error: str | None = None):
        self.results.append(CheckResult(name, passed, detail, error))

    def print_report(self) -> bool:
        """打印报告，返回是否全部通过"""
        print()
        for r in self.results:
            icon = "[OK]" if r.passed else "[FAIL]"
            line = f"{icon} {r.name}"
            if r.detail:
                line += f" - {r.detail}"
            if r.error:
                line += f" | ERROR: {r.error}"
            print(line)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        print("-" * 50)
        print(f"Total: {passed}/{total} passed")
        print()
        return passed == total


def load_config():
    """加载项目配置"""
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        from core.config import ConfigManager
        return ConfigManager.get_instance().load()
    except Exception as e:
        print(f"\033[33m警告: 无法加载配置 ({e})，使用环境变量\033[0m")
        return None
