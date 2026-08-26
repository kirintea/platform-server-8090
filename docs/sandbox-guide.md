# 沙箱隔离指南

## 概述

沙箱隔离确保 Agent 的工具执行（文件操作、命令执行等）在受限环境中进行，防止访问项目源码和敏感配置。

项目提供两种沙箱模式：

| 模式 | `sandbox.backend` | 执行位置 | 隔离级别 |
|------|-------------------|---------|---------|
| **本地模式** | `local`（默认） | app 容器/本机进程内 | 路径守卫（PathGuard） |
| **容器模式** | `docker` | 独立 sandbox 容器 | 容器级隔离 |

## 本地模式（默认）

工具在 app 容器内执行，通过中间件链保障安全：

```
Agent 调用工具
  → DockerSandboxProxy（backend=local 时跳过）
  → PathGuardMiddleware（限制路径在沙箱内）
  → CommandGuardMiddleware（拦截危险命令）
  → ToolGuardMiddleware（工具名黑白名单）
  → 工具执行
```

**安全保障**：
- `PathGuardMiddleware`：所有文件操作限制在 `sandbox_dir` 目录内
- `CommandGuardMiddleware`：拦截 `rm -rf /`、`curl|bash` 等危险命令
- 适合可信环境（开发、测试）

## 容器模式

工具在独立的 sandbox 容器中执行，通过 Docker SDK 通信：

```
Agent 调用工具
  → DockerSandboxProxy 拦截
  → Docker SDK (docker exec)
  → sandbox 容器内执行
  → 结果返回
```

**安全保障**：
- sandbox 容器只能看见 `workspaces/` 和 `skills/`
- 项目源码、`.env`、数据库凭据对 sandbox 不可见
- 即使 Agent 执行 `ls /` 也只能看到容器文件系统
- 适合生产环境或不可信场景

## 配置

### 配置文件

```yaml
# configs/dev.yaml 或 configs/prod.yaml
sandbox:
  backend: "local"               # "local" | "docker"
  container: "platform-sandbox"  # sandbox 容器名
  project_root: "/workspace"     # 容器内项目根路径
  extra_mounts:                  # 额外挂载目录
    - host: "skills"
      container: "/workspace/skills"
      readonly: true
```

### 环境变量覆盖

```bash
# 通过环境变量切换模式
SANDBOX_BACKEND=docker
```

### 文件系统隔离

| 路径 | app 容器 | sandbox 容器 |
|------|---------|-------------|
| 项目源码 | ✅ 可见（`/workspace/`） | ❌ 不可见 |
| `workspaces/` | ✅ 可见 | ✅ 可见（唯一工作目录） |
| `skills/` | ✅ 可见 | ✅ 只读挂载 |
| `configs/*.yaml` | ✅ 可见 | ❌ 不可见 |
| `.env` | ✅ 可见 | ❌ 不可见 |
| 数据库凭据 | ✅ 可用 | ❌ 不可用 |

## DockerSandboxProxy 中间件

### 拦截的工具

| 工具 | 说明 |
|------|------|
| `Bash` / `PowerShell` | 命令在 sandbox 容器执行 |
| `Read` / `Write` / `Edit` | 文件路径翻译后在 sandbox 执行 |
| `Glob` / `Grep` | 搜索路径翻译后在 sandbox 执行 |
| `TaskCreate` / `TaskList` / `TaskGet` / `TaskUpdate` | 任务管理在 sandbox 执行 |

### 路径翻译

工具调用中的宿主机路径会自动翻译为容器路径：

```
宿主机: D:\project\workspaces\user123\file.txt
  ↓ 翻译
容器:   /workspace/workspaces/user123/file.txt
```

### 降级机制

sandbox 容器不可用时，自动降级到本地执行并记录警告日志：

```
沙箱容器不可用，降级到本地执行: Bash
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `middleware/docker_sandbox_proxy.py` | Docker 沙箱代理中间件 |
| `middleware/path_guard.py` | 路径守卫（本地模式安全保障） |
| `middleware/command_guard.py` | 命令内容守卫 |
| `middleware/tool_guard.py` | 工具名黑白名单 |
| `core/config/schemas.py` | `SandboxConfig` / `SandboxMountConfig` 定义 |
| `core/agent/factory.py` | 中间件注入逻辑 |
| `Dockerfile.sandbox` | sandbox 镜像定义 |
| `docker-compose.sandbox.yml` | app + sandbox 编排 |
