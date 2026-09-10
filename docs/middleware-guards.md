# 中间件守卫

> 平台的多层安全防线：从工具注册到命令执行，层层拦截

## 概览

平台有六层中间件守卫，各司其职：

| 守卫 | 拦截层级 | 作用 | 配置位置 |
|------|----------|------|----------|
| **工具管理器** | 注册时 | 决定工具是否存在（YAML 开关） | `configs/tools.yaml` |
| **工具守卫** | 运行时·工具名 | 按工具名黑白名单决定是否执行 | `agent.tool_guard` |
| **命令守卫** | 运行时·命令内容 | 解析命令内容，拦截危险操作 | `agent.command_guard` |
| **路径守卫** | 运行时·文件路径 | 限制文件操作在沙箱目录内 | `agent.sandbox_dir` |
| **压缩守卫** | 运行时·上下文压缩 | 确保内容先卸载再丢弃 | 自动启用 |
| **速率限制** | 请求层 | per-user 滑动窗口限流 | `middleware.rate_limit` |

另外还有 **回复预算控制**（[budget-control.md](budget-control.md)）管理单次回复的 token 消耗，属于资源控制而非安全守卫。

## 执行顺序

```
用户请求
  │
  ▼
速率限制 ──→ 超限 → 429 Too Many Requests
  │
  ▼
智能体推理 → 工具调用
  │
  ▼
工具管理器 ──→ 未启用 → 工具不存在（注册阶段，非运行时）
  │
  ▼
工具守卫 ──→ 命中名单 → ToolResponse(DENIED)
  │
  ▼
命令守卫 ──→ 命中规则/高风险 → ToolResponse(DENIED)
  │
  ▼
路径守卫 ──→ 路径越界 → ToolResponse(DENIED)
  │
  ▼
工具实际执行
  │
  ▼
上下文压缩 ──→ 未挂载 Offloader → RuntimeError 拒绝压缩
```

守卫之间是"且"关系——必须全部通过才能执行。命令守卫和路径守卫是并列的，分别检查命令内容和文件路径。

## 工具管理器（注册阶段）

工具管理器在 Agent 初始化时根据 `configs/tools.yaml` 决定注册哪些工具。这是**注册阶段**的控制，被禁用的工具根本不会出现在 Agent 的工具列表中。

```yaml
# configs/tools.yaml
tools:
  - name: "Bash"
    enabled: true
  - name: "Read"
    enabled: true
  - name: "Write"
    enabled: false   # 该工具不会被注册
```

与工具守卫的区别：
- **工具管理器** → 注册时：工具存不存在
- **工具守卫** → 运行时：工具能不能执行

## 工具守卫（tool_guard）

在工具执行前按工具名做黑白名单匹配。仅检查工具名，不解析命令内容。

### 配置

```yaml
agent:
  tool_guard:
    enabled: true
    mode: "blocklist"      # allowlist | blocklist
    tools:
      - "Bash"             # 按工具名匹配
      - "PowerShell"
      - "Write"            # 支持通配符
```

### 模式说明

| 模式 | 行为 |
|------|------|
| `blocklist` | 命中名单的工具被拦截，其余放行 |
| `allowlist` | 未命中名单的工具被拦截，仅放行命中的 |

### 匹配规则

使用 `fnmatch` 通配符匹配：
- `Bash` — 精确匹配
- `Read*` — 匹配 Read、ReadFile 等
- `*` — 匹配所有工具

### 拦截行为

被拦截时返回 `ToolResponse(state=DENIED)`，智能体会收到提示：
```
[ToolGuard] 工具被拦截 (blocklist): Bash
```

### 典型用法

```yaml
# 开发环境：放行所有工具
tool_guard:
  enabled: false

# 生产环境：仅放行文件操作类工具
tool_guard:
  enabled: true
  mode: "allowlist"
  tools:
    - "Read"
    - "Glob"
    - "Grep"
```

## 命令守卫（command_guard）

在工具执行前解析 `tool_call.input` 中的实际命令内容，按黑白名单和高风险管理规则拦截危险操作。

### 配置

```yaml
agent:
  command_guard:
    enabled: true
    mode: "blocklist"           # allowlist | blocklist
    rules:
      - "rm -rf /"              # 精确匹配
      - "rm -rf *"              # 通配符匹配
      - "curl *|*bash*"         # 管道执行
      - "*/dev/tcp/*"           # Bash 反弹 shell
      - "Invoke-Expression*"    # PowerShell 远程执行
      - "certutil*"             # Windows 下载
```

### 两层检测机制

命令守卫有两层检测，互相独立：

**第一层：规则匹配（可配置）**

按 `rules` 列表用 `fnmatch` 通配符匹配命令全文。规则的匹配行为取决于 `mode`。

**第二层：高风险管理（内置，不可关闭）**

`_HIGH_RISK_PATTERNS` 中的正则表达式在**两种模式下都会拦截**，不受 `mode` 影响。这些是公认的高危操作：

| 类别 | 模式 | 示例 |
|------|------|------|
| 文件毁灭 | `rm -rf /`, `rm -rf ~`, `rm -rf *` | 删除根目录、主目录 |
| 下载执行 | `curl\|bash`, `wget\|sh` | 远程下载并执行 |
| 反弹 Shell | `/dev/tcp/`, `nc -e` | 建立反向连接 |
| 权限提升 | `sudo`, `chmod -R`, `chown` | 提权操作 |
| 进程控制 | `kill -9 1`, `systemctl stop` | 杀 init 进程、停服务 |
| 命令替换 | `$(`, `` ` `` | 命令注入 |
| 管道执行 | `\| python`, `\| perl` | 管道到解释器 |

### 结构化高风险检测

除了静态正则，还有三个结构化检测器防止混淆绕过：

| 检测器 | 防御目标 |
|--------|----------|
| `_high_risk_rm` | `rm` 参数重排/拆分/引号混淆（如 `-fr`, `-r -f`, `'-rf'`） |
| `_check_inline_interpreter_rce` | 内联解释器中的 RCE 令牌（如 `python -c "os.system('id')"`） |
| `_check_script_wrap_rce` | 下载+执行脚本封装（如 `curl ... -o x.sh && bash x.sh`） |

### 命令提取

命令守卫从 `tool_call.input` 中自动提取实际命令：

| 工具类型 | 提取字段 |
|----------|----------|
| Bash/PowerShell | `command` |
| Write | `content` |
| Read | `file_path` |
| MCP | `command` / `content` / `path` |

### 混淆防御

高风险检测前会做归一化处理：
- 剥离一层外围引号
- Unicode 全角字符转半角（`／` → `/`）
- 展开 `--` 选项终止符
- 移除 shell 转义反斜杠
- 折叠空引号对（`r''m` → `rm`）

### 典型用法

```yaml
# 开发环境：blocklist 只拦致命
command_guard:
  enabled: true
  mode: "blocklist"
  rules:
    - "rm -rf /"
    - "rm -rf ~"
    - "rm -rf /*"
    - "*|*bash*"
    - "*|*sh*"
    - "*/dev/tcp/*"

# 生产环境：allowlist 只放行安全命令
command_guard:
  enabled: true
  mode: "allowlist"
  rules:
    - "ls *"
    - "pwd"
    - "cat *"
    - "head *"
    - "tail *"
    - "grep *"
    - "find *"
    - "echo *"
```

完整的危险命令参考见 [dangerous-commands.md](dangerous-commands.md)。

## 路径守卫（path_guard）

限制文件操作类工具的路径参数在沙箱目录内，防止 Agent 访问沙箱外的文件。

### 配置

```yaml
agent:
  sandbox_dir: "workspaces"   # 沙箱根目录
```

路径守卫在 Agent 初始化时自动启用，无需额外开关。

### 拦截范围

**直接路径工具**（检查路径参数字段）：

| 工具 | 检查字段 |
|------|----------|
| Read | `file_path` |
| Write | `file_path` |
| Edit | `file_path` |
| Glob | `path` |
| Grep | `path` |

**Bash/PowerShell**（解析命令中的文件路径）：

- 识别文件操作子命令（`cat`, `cp`, `mv`, `rm`, `grep`, `sed` 等 40+ 种）
- 提取子命令后的路径参数
- 检查重定向目标路径（`> /path`, `>> /path`）
- 扫描 `python -c` 代码字符串中的路径字面量
- 未识别命令采用 fail-closed 策略，扫描所有路径型参数

### 路径校验逻辑

```
路径输入
  │
  ├─ @ 前缀 → 去除 @（curl -d @/etc/passwd → /etc/passwd）
  │
  ├─ shell 展开 → ~ 和 $HOME 等环境变量
  │
  ├─ 相对路径 → 基于沙箱根目录解析
  │
  ├─ 绝对路径 → 直接解析
  │
  └─ Path.resolve() 消除 .. 和符号链接
      │
      └─ 是否以沙箱根目录开头？
          ├─ 是 → 放行
          └─ 否 → 拦截
```

### 系统提示注入

路径守卫会在系统提示词末尾追加沙箱路径信息，让 Agent 从第一步就知道文件操作的边界：

```markdown
## 沙箱路径（必读）
所有文件操作必须在以下沙箱目录内：
```
/workspaces/user123
```
创建、读取、编辑文件时，必须使用此目录的绝对路径。禁止访问此路径之外的任何文件。
```

### 典型用法

```yaml
# 用户隔离沙箱
agent:
  sandbox_per_user: true    # workspaces/{user_id}/
  sandbox_dir: "workspaces"

# 共享沙箱（所有用户同一目录）
agent:
  sandbox_per_user: false   # workspaces/
  sandbox_dir: "workspaces"
```

## 压缩守卫（context_guard）

在上下文压缩发生前检查 Offloader 是否已挂载。如果未挂载 Offloader 就执行压缩，被压缩的内容会永久丢失。

### 工作原理

```
上下文压缩触发
  │
  ▼
压缩守卫检查
  │
  ├─ Agent 已挂载 Offloader → 继续压缩（框架自动卸载内容）
  │
  └─ Agent 未挂载 Offloader → RuntimeError 拒绝压缩
      │
      └─ 报错信息：
          "ContextGuard: 拒绝上下文压缩 — Agent 未挂载 Offloader。
           在执行上下文压缩前必须先挂载 Offloader，否则被压缩丢弃的
           内容将无法恢复。"
```

### 为什么需要它

上下文压缩会把较早的消息替换为摘要。如果没有 Offloader 持久化被压缩的内容，那些消息就永远消失了。压缩守卫确保这个安全网始终存在。

### 配置

压缩守卫自动启用，无需配置。只要 Agent 挂载了 Offloader（如 `LocalWorkspace`），压缩就能正常进行。

## 速率限制（rate_limit）

对 LLM 调用端点做 per-user 滑动窗口限流。超限时返回 `429 Too Many Requests`。

### 配置

```yaml
middleware:
  rate_limit:
    enabled: true
    requests_per_minute: 10   # 每用户每分钟最大请求数
```

### 受保护端点

默认限流以下路径：
- `/chat/stream` — 流式对话
- `/chat/` — Fire-and-Forget 对话

### 用户标识识别

按以下优先级提取用户标识：
1. `X-API-Key` 请求头（认证场景，取前 16 字符）
2. `user_id` 查询参数
3. `anonymous`（兜底）

### 实现细节

- 使用内存滑动窗口，适合单 Worker 部署
- 多 Worker 场景需切换为 Redis-based 实现
- 窗口内超过 `requests_per_minute` 次请求即触发限流
- 响应包含 `Retry-After` header，告知客户端何时可重试

## 各守卫与工具管理器的关系

```
configs/tools.yaml          注册阶段
  │
  └─ 工具管理器 ──→ enabled: false → 工具不注册
      │
      └─ enabled: true → 工具注册到 Agent
          │
          ▼
      运行时守卫链
          │
          ├─ 工具守卫 ──→ 工具名命中名单 → DENIED
          │
          ├─ 命令守卫 ──→ 命令内容命中规则/高风险 → DENIED
          │
          └─ 路径守卫 ──→ 路径越界 → DENIED
              │
              └─ 全部通过 → 工具执行
```

| 层级 | 组件 | 控制维度 | 何时生效 |
|------|------|----------|----------|
| L0 | 工具管理器 | 工具是否存在 | Agent 初始化时 |
| L1 | 工具守卫 | 工具名 | 每次工具调用前 |
| L2 | 命令守卫 | 命令内容 | 每次工具调用前 |
| L3 | 路径守卫 | 文件路径 | 每次工具调用前 |
| L4 | 压缩守卫 | Offloader 状态 | 上下文压缩触发时 |
| L5 | 速率限制 | 请求频率 | 每次 HTTP 请求时 |
