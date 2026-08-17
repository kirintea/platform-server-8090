# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 子配置模块
# ============================================================

class OTelConfig(BaseModel):
    """OpenTelemetry 追踪配置"""
    enabled: bool = Field(default=True, description="是否启用追踪")
    endpoint: str = Field(description="OTLP 上报地址 (gRPC)")
    service_name: str = Field(default="platform-agent", description="服务名称")
    service_version: str = Field(default="0.1.0", description="服务版本")
    environment: str = Field(description="运行环境 (development / production)")
    headers: dict[str, str] = Field(default_factory=dict, description="附加请求头")


class LLMConfig(BaseModel):
    """LLM 模型配置"""
    provider: str = Field(default="openai", description="模型提供商 (openai / dashscope / anthropic)")
    api_key: str = Field(description="API 密钥")
    base_url: str = Field(description="API 基础地址")
    model: str = Field(description="模型名称")
    stream: bool = Field(default=True, description="是否默认流式输出")
    context_size: int = Field(default=128000, description="上下文窗口大小 (token)")
    max_tokens: int = Field(default=4096, description="最大输出 token 数")
    temperature: float = Field(default=0.7, description="采样温度")


class ServerConfig(BaseModel):
    """HTTP 服务配置"""
    host: str = Field(default="0.0.0.0", description="监听地址")
    port: int = Field(default=8090, description="监听端口")
    workers: int = Field(default=1, description="Worker 进程数 (1=single, 0=auto CPU)")
    log_level: str = Field(default="INFO", description="日志级别 (DEBUG/INFO/WARNING/ERROR)")
    log_dir: str = Field(default="logs", description="日志文件目录（相对项目根）")
    log_backup_count: int = Field(default=30, description="日志保留天数")


class ToolGuardConfig(BaseModel):
    """工具黑白名单配置"""
    enabled: bool = Field(default=False, description="是否启用工具守卫")
    mode: str = Field(default="blocklist", description="模式: allowlist / blocklist")
    tools: list[str] = Field(default_factory=list, description="工具列表 (支持通配符, 如 'Bash:*')")


class CommandGuardConfig(BaseModel):
    """命令内容安全守卫配置"""
    enabled: bool = Field(default=False, description="是否启用命令守卫")
    mode: str = Field(default="blocklist", description="模式: allowlist / blocklist")
    rules: list[str] = Field(default_factory=list, description="命令匹配规则 (fnmatch 通配符)")


class BuiltinToolEntry(BaseModel):
    """单个内置工具配置"""
    enabled: bool = Field(default=True, description="是否启用")
    description: str = Field(default="", description="工具描述（覆盖默认）")


class CustomToolEntry(BaseModel):
    """自定义工具配置"""
    name: str = Field(description="工具名称")
    module: str = Field(description="Python 模块路径 (如 tools.web_search)")
    class_name: str | None = Field(default=None, description="ToolBase 子类名")
    func_name: str | None = Field(default=None, description="函数名（用 FunctionTool 包装）")
    enabled: bool = Field(default=True, description="是否启用")
    is_read_only: bool = Field(default=False, description="是否只读")
    kwargs: dict[str, Any] = Field(default_factory=dict, description="构造参数")


class ToolManagerConfig(BaseModel):
    """工具管理器配置"""
    config_path: str = Field(
        default="configs/tools.yaml",
        description="工具配置文件路径",
    )


class AgentConfig(BaseModel):
    """Agent 行为配置"""
    name: str = Field(default="platform_agent", description="Agent 名称")
    system_prompt: str = Field(default="你是一个有帮助的助手。", description="系统提示词")
    max_iters: int = Field(default=20, description="ReAct 最大迭代次数")
    context_trigger_ratio: float = Field(default=0.8, description="上下文压缩触发比例")
    context_reserve_ratio: float = Field(default=0.1, description="压缩后保留比例")
    permission_mode: str = Field(default="bypass", description="权限模式: auto / bypass")
    tool_guard: ToolGuardConfig = Field(default_factory=ToolGuardConfig, description="工具守卫配置")
    command_guard: CommandGuardConfig = Field(default_factory=CommandGuardConfig, description="命令内容守卫配置")
    tool_manager: ToolManagerConfig = Field(default_factory=ToolManagerConfig, description="工具管理器配置")


class MCPConfig(BaseModel):
    """单个 MCP 服务配置"""
    name: str = Field(description="MCP 服务名称")
    transport: str = Field(default="stdio", description="传输方式: stdio / http")
    # stdio 模式
    command: str | None = Field(default=None, description="stdio 命令")
    args: list[str] = Field(default_factory=list, description="stdio 参数")
    # http 模式
    url: str | None = Field(default=None, description="HTTP MCP 地址")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP 请求头")


class DatabaseConfig(BaseModel):
    """数据库配置（预留）"""
    url: str = Field(default="", description="数据库连接 URL")
    pool_size: int = Field(default=10, description="连接池大小")


class RedisConfig(BaseModel):
    """Redis 配置"""
    url: str = Field(default="redis://localhost:6379/0", description="Redis 连接 URL")
    key_prefix: str = Field(default="agentscope:session:", description="会话 Key 前缀")
    session_ttl: int = Field(default=1800, description="会话 TTL (秒)")


# ============================================================
# 根配置
# ============================================================

class AppConfig(BaseModel):
    """应用根配置"""
    otel: OTelConfig
    llm: LLMConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp_servers: list[MCPConfig] = Field(default_factory=list, description="MCP 服务列表")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
