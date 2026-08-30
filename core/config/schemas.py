# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    timeout: int = Field(default=120, description="LLM 调用超时（秒），流式模式下为无事件空闲超时")


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
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="是否启用工具守卫")
    mode: str = Field(default="blocklist", description="模式: allowlist / blocklist")
    tools: list[str] = Field(default_factory=list, description="工具列表 (支持通配符, 如 'Bash:*')")


class CommandGuardConfig(BaseModel):
    """命令内容安全守卫配置"""
    model_config = ConfigDict(extra="forbid")

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


class InjectionConfigSchema(BaseModel):
    """运行时状态注入配置"""
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    time_interval: float = Field(default=0.5, description="时间注入间隔（小时）")
    context_buffer_ratio: float = Field(default=0.15, description="压缩阈值前预警比例")
    emit_hint_event: bool = Field(default=True, description="是否推送 HintBlockEvent 到前端")
    extra_fields: dict[str, str] = Field(default_factory=dict, description="自定义注入字段")


class PermissionRuleEntry(BaseModel):
    """单条权限规则"""
    tool_name: str = Field(description="工具名: Bash / Read / Write / Edit / 自定义工具名")
    rule_content: str = Field(description="匹配模式（Bash: 前缀通配, 文件工具: glob, 其他: JSON 精确匹配）")
    source: str = Field(default="platform-config", description="规则来源标识")


class PermissionConfig(BaseModel):
    """细粒度权限配置"""
    mode: str = Field(
        default="bypass",
        description="权限模式: default / accept_edits / explore / bypass / dont_ask",
    )
    deny_rules: list[PermissionRuleEntry] = Field(
        default_factory=list,
        description="拒绝规则列表（优先级最高，所有模式下生效）",
    )
    allow_rules: list[PermissionRuleEntry] = Field(
        default_factory=list,
        description="允许规则列表（在工具检查之后评估）",
    )


class RAGConfig(BaseModel):
    """RAG 检索增强生成配置

    当前为空壳预留，RAG 服务确定后填充具体参数。
    第三方向量存储通过 ThirdPartyVectorStore(VectorStoreBase) 对接。
    """
    enabled: bool = Field(default=False, description="是否启用 RAG")
    backend: str = Field(
        default="third_party",
        description="RAG 后端类型: third_party / qdrant / milvus / ...",
    )
    api_url: str = Field(default="", description="RAG 服务 API 地址")
    api_key: str = Field(default="", description="RAG 服务 API 密钥")
    collection: str = Field(default="default", description="默认 collection 名称")
    top_k: int = Field(default=5, description="检索返回最大结果数")
    score_threshold: float | None = Field(default=None, description="相似度阈值")
    mode: str = Field(
        default="agentic",
        description="集成模式: static（自动注入）/ agentic（Agent 自主调用）/ both",
    )
    emit_hint_event: bool = Field(default=True, description="static 模式下是否推送 HintBlockEvent")
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展参数（传递给 ThirdPartyVectorStore）",
    )


class AgentConfig(BaseModel):
    """Agent 行为配置"""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="platform_agent", description="Agent 名称")
    system_prompt: str = Field(default="你是一个有帮助的助手。", description="系统提示词")
    max_iters: int = Field(default=20, description="ReAct 最大迭代次数")
    context_trigger_ratio: float = Field(default=0.6, description="上下文压缩触发比例")
    context_reserve_ratio: float = Field(default=0.15, description="压缩后保留比例")
    tool_result_limit: int = Field(default=15000, description="单条工具结果 token 上限")
    reply_token_budget: int = Field(default=80000, description="单次回复加权 token 预算")
    injection: InjectionConfigSchema = Field(
        default_factory=InjectionConfigSchema,
        description="运行时注入配置",
    )
    permission_mode: str = Field(default="bypass", description="权限模式（旧字段，优先使用 permission.mode）")
    permission: PermissionConfig = Field(
        default_factory=PermissionConfig,
        description="细粒度权限配置",
    )
    sandbox_dir: str = Field(
        default="workspaces",
        description="Agent 沙箱根目录（相对项目根或绝对路径），所有工具操作限制在此目录内",
    )
    sandbox_skills: bool = Field(
        default=False,
        description="true 时 skills 从 sandbox_dir 下加载（skills/ 子目录）",
    )
    sandbox_mcp: bool = Field(
        default=False,
        description="true 时 MCP 配置从 sandbox_dir 下加载（mcp/ 子目录）",
    )
    sandbox_per_user: bool = Field(
        default=False,
        description="true 时沙箱路径追加 user_id 层级（workspaces/{user_id}/）",
    )
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


class ContextBackfillConfig(BaseModel):
    """PG 回填配置"""
    backfill_message_limit: int = Field(default=20, description="回填消息条数上限")
    backfill_token_budget: int = Field(default=15000, description="回填 token 预算")


class BudgetControlConfig(BaseModel):
    """回复预算控制中间件配置"""
    enabled: bool = Field(default=True, description="是否启用预算控制")
    token_budget: int = Field(default=80000, description="单次回复加权 token 预算")
    input_weight: int = Field(default=1, description="输入 token 权重")
    output_weight: int = Field(default=2, description="输出 token 权重")


class RateLimitConfig(BaseModel):
    """速率限制配置"""
    enabled: bool = Field(default=True, description="是否启用速率限制")
    requests_per_minute: int = Field(default=10, description="每分钟最大请求数（per-user）")


class MiddlewareConfig(BaseModel):
    """中间件配置"""
    budget_control: BudgetControlConfig = Field(
        default_factory=BudgetControlConfig,
        description="回复预算控制配置",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="速率限制配置",
    )


class SandboxMountConfig(BaseModel):
    """沙箱额外挂载配置"""
    host: str = Field(description="宿主机路径（相对于项目根）")
    container: str = Field(description="容器内挂载路径（绝对路径）")
    readonly: bool = Field(default=True, description="是否只读")


class AuthConfig(BaseModel):
    """API 认证配置"""
    required: bool = Field(
        default=False,
        description="是否启用 API Key 认证（生产环境必须为 true）",
    )
    api_key: str = Field(
        default="",
        description="API 密钥（通过环境变量 API_KEY 注入）",
    )


class SandboxConfig(BaseModel):
    """沙箱配置"""
    backend: str = Field(
        default="local",
        description="沙箱后端: local（直接本机执行）/ docker（转发到沙箱容器）",
    )
    container: str = Field(
        default="platform-sandbox",
        description="沙箱容器名称（backend=docker 时使用）",
    )
    project_root: str = Field(
        default="/workspace",
        description="容器内项目根路径（挂载点）",
    )
    extra_mounts: list[SandboxMountConfig] = Field(
        default_factory=list,
        description="额外挂载列表（如 skills/ 目录）",
    )
    fallback_to_local: bool = Field(
        default=True,
        description="沙箱不可用时是否降级到本地执行（生产环境建议 false）",
    )


# ============================================================
# 根配置
# ============================================================

class AppConfig(BaseModel):
    """应用根配置"""
    model_config = ConfigDict(extra="forbid")

    otel: OTelConfig
    llm: LLMConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig, description="API 认证配置")
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp_servers: list[MCPConfig] = Field(default_factory=list, description="MCP 服务列表")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    context: ContextBackfillConfig = Field(
        default_factory=ContextBackfillConfig,
        description="上下文回填配置",
    )
    middleware: MiddlewareConfig = Field(
        default_factory=MiddlewareConfig,
        description="中间件配置",
    )
    sandbox: SandboxConfig = Field(
        default_factory=SandboxConfig,
        description="沙箱配置",
    )
    rag: RAGConfig = Field(
        default_factory=RAGConfig,
        description="RAG 检索增强生成配置（预留）",
    )
