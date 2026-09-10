# 预算控制

> 控制单次回复的 token 消耗，防止单次请求消耗过多资源

平台有两类 token 相关的控制机制，职责不同：

| 机制 | 配置项 | 作用 | 触发后果 |
|------|--------|------|----------|
| **回复预算** | `middleware.budget_control.token_budget` | 限制单次回复的加权 token 消耗 | 注入提示，让智能体尽快收尾 |
| **上下文压缩** | `agent.context_trigger_ratio` | 限制模型上下文窗口的使用比例 | 自动压缩较早消息为摘要 |

两者独立运行、互不干扰：回复预算管的是"这次回复花多少钱"，上下文压缩管的是"智能体能记住多少"。

## 回复预算

回复预算由 `ReplyBudgetControlMiddleware`（AgentScope 内置中间件）实现，在每次模型调用后累计 token 消耗，接近预算时向上下文注入一条提示，引导智能体收尾。

### 工作原理

```
用户消息
  │
  ▼
智能体推理 ──→ 工具调用 ──→ 智能体推理 ──→ ...
  │                │                │
  ▼                ▼                ▼
累计 input×1      累计 input×1     累计 input×1
+ output×2       + output×2      + output×2
  │                │                │
  └────────────────┴────────────────┘
                   │
                   ▼
         接近 token_budget ?
           ├─ 否 → 继续
           └─ 是 → 注入提示："token 预算即将耗尽，请收尾"
```

加权公式：`消耗 = input_tokens × input_weight + output_tokens × output_weight`

默认权重 `input_weight=1, output_weight=2` 意味着输出 token 的"成本"是输入的两倍——这反映了实际 API 定价中输出更贵的特点。

### 配置

在 `configs/dev.yaml`（或 `prod.yaml`）的 `middleware.budget_control` 下配置：

```yaml
middleware:
  budget_control:
    enabled: true              # 总开关
    token_budget: 80000        # 单次回复加权 token 预算
    input_weight: 1            # 输入 token 权重
    output_weight: 2           # 输出 token 权重
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用预算控制 |
| `token_budget` | `int` | `80000` | 单次回复的加权 token 预算上限 |
| `input_weight` | `int` | `1` | 输入 token 的权重系数 |
| `output_weight` | `int` | `2` | 输出 token 的权重系数 |

### 提示语

当加权消耗接近预算时，中间件向上下文注入以下提示（硬编码于 [factory.py:350-353](../core/agent/factory.py#L350-L353)）：

```
你本次回复的 token 预算即将耗尽。请尽快总结当前进展并给出结论，不要再调用工具。
```

智能体看到这条提示后会停止调用工具，转而总结当前进展。

### 调优建议

| 场景 | 建议 |
|------|------|
| 测试拦截守卫、调试工具链 | 临时关闭 `enabled: false` 或调大 `token_budget: 200000` |
| 生产环境、成本敏感 | 保持默认 80000，或降低至 50000 |
| 复杂分析任务（多轮工具调用） | 调大至 120000–150000 |

## 上下文压缩

上下文压缩由 AgentScope 的 `ContextConfig` 控制，管理的是模型上下文窗口的使用量。当历史消息累积到一定比例时，自动将较早的消息压缩为摘要。

### 与回复预算的区别

| 维度 | 回复预算 | 上下文压缩 |
|------|----------|------------|
| **控制对象** | 单次回复的 token 消耗量 | 模型上下文窗口的使用比例 |
| **触发条件** | 加权 token 累计接近 `token_budget` | 输入 token 数超过 `trigger_ratio × 模型上下文长度` |
| **触发后果** | 注入提示，引导智能体收尾 | 自动压缩较早消息为结构化摘要 |
| **感知范围** | 单次回复（从用户消息到智能体结束） | 整个会话的累积历史 |
| **是否可配置权重** | 是（`input_weight` / `output_weight`） | 否 |

简单来说：
- **回复预算** = "这次回复最多花多少钱" → 经济控制
- **上下文压缩** = "智能体最多能记住多少" → 记忆管理

### 配置

在 `configs/dev.yaml` 的 `agent` 下配置：

```yaml
agent:
  # 上下文压缩阈值 — 超过模型上下文的 60% 时触发压缩
  context_trigger_ratio: 0.6
  # 压缩后保留近期上下文比例 — 保留 ~15% 给最近消息
  context_reserve_ratio: 0.15
  # 单条工具结果 token 上限 — 防止单次工具调用占满上下文
  tool_result_limit: 15000
```

| 参数 | 说明 |
|------|------|
| `context_trigger_ratio` | 输入 token 超过该比例 × 模型上下文长度时触发压缩（上限 0.9） |
| `context_reserve_ratio` | 压缩后保留给最近消息的上下文比例 |
| `tool_result_limit` | 单条工具结果的最大 token 数，超出则截断 |

压缩触发后，AgentScope 会：
1. 将较早消息标记为待压缩
2. 调用模型生成结构化摘要（`task_overview`、`current_state`、`important_discoveries`、`next_steps`、`context_to_preserve`）
3. 用摘要替换被压缩的消息

详见 [上下文管理.md](上下文管理.md) 中的"压缩上下文"章节。

## 回填预算

回填预算控制从 PostgreSQL 恢复会话到 Redis 时加载的历史消息量，避免恢复时消耗过多 token。

```yaml
context:
  backfill_message_limit: 20        # 回填消息条数上限
  backfill_token_budget: 15000      # 回填 token 上限
```

| 参数 | 说明 |
|------|------|
| `backfill_message_limit` | 回填时最多加载的消息条数 |
| `backfill_token_budget` | 回填消息的 token 总量上限 |

回填预算只在会话恢复时生效一次，不影响后续的回复预算或上下文压缩。

## 三者的关系

```
会话生命周期
│
├─ 会话恢复（PG → Redis）
│   └─ 回填预算：最多加载 N 条 / M tokens 的历史消息
│
├─ 每次推理前
│   └─ 上下文压缩：输入 token 超阈值 → 压缩较早消息为摘要
│
└─ 单次回复过程中
    └─ 回复预算：加权 token 累计超限 → 提示智能体收尾
```

三者各司其职，互不冲突：
- **回填预算**控制"恢复时加载多少历史"
- **上下文压缩**控制"上下文窗口里留多少记忆"
- **回复预算**控制"这次回复最多消耗多少 token"
