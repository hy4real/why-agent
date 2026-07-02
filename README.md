# PR Review Agent

无框架 Agent：从零搭建的 PR 代码审查系统。

不用 LangGraph、LangChain、AgentScope——手写 agent loop + 工具调度 + 权限控制 + 上下文压缩，演示 harness 原理的工程落地。

## 架构

```
┌─────────────────────────────────────────────────┐
│              CLI (click)  /  Webhook Server      │
│  list / review / chat / history  /  serve        │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│              Agent (harness.py)                  │
│  while loop: 消息 → 工具调用 → 追加 → 重复        │
└──┬───────────┬──────────────┬───────────────────┘
   │           │              │
┌──▼──┐  ┌────▼────┐  ┌──────▼──────┐
│tools│  │context  │  │permissions  │
│注册表│  │diff压缩 │  │dry_run/live │
└──┬──┘  └─────────┘  └─────────────┘
   │
┌──▼──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│GitHub API   │  │StateStore│  │OpenAI API│  │  styles  │
│PR/diff/评论  │  │审查历史   │  │LLM 调用  │  │风格切换   │
│文件内容读取   │  └──────────┘  └──────────┘  └──────────┘
└─────────────┘
```

## 核心设计

| 组件 | 对应 learn-claude-code 课程 | 作用 |
|------|---------------------------|------|
| harness.py (agent loop) | s01 基础循环 | 发消息→工具调用→追加→重复 |
| tools.py (注册表) | s02 工具系统 | 统一的工具定义、注册、调度 |
| permissions.py | s03 权限控制 | dry_run/live 双模式，写操作需确认 |
| context.py | s08 上下文压缩 | 大 diff 智能裁剪，按文件优先级排序 |
| state.py | s10 持久化 | JSON 文件记住审过的 PR |
| pr_tools.py | s06 工具组合 | 业务层工具定义，注册到 registry |
| styles.py | — | 审查风格切换 (strict / lenient / security-only) |
| server.py | — | Webhook HTTP 服务，标准库实现 |

## 功能

- **真实 GitHub API** — 列 PR、获取 diff、发表 review、行内评论
- **审查风格切换** — strict（严格）、lenient（务实）、security-only（安全专项）
- **测试覆盖检查** — 自动读取测试文件，判断"改了代码但没改测试"
- **上下文压缩** — 大 PR 按文件优先级裁剪，标注行号
- **权限控制** — dry_run 模式不发评论，live 模式需确认
- **Webhook 服务** — 监听 GitHub PR 事件，自动触发审查
- **自定义 LLM** — 支持 DeepSeek、智谱等 OpenAI 兼容接口
- **多模型协作** — DeepSeek 初筛 + GPT-4o 精审，便宜模型决定要不要请贵模型出手

## 使用

```bash
# 安装
cd projects/pr-review-agent
pip install -e .

# 配置 .env（复制模板后填入你的 key）
cp .env.example .env
# 编辑 .env 填入 GITHUB_TOKEN + OPENAI_API_KEY（或 DeepSeek/智谱的 key）

# 列出 open PR
pr-review list fastapi fastapi --limit 5

# 审查指定 PR (dry_run 模式，不发评论)
pr-review review fastapi fastapi 12345

# 用 strict 风格审查
pr-review review fastapi fastapi 12345 --style strict

# 只检查安全问题
pr-review review fastapi fastapi 12345 --style security-only

# 交互式审查，可以追问细节
pr-review chat fastapi fastapi 12345

# live 模式（会真的发评论到 GitHub）
pr-review --mode live review fastapi fastapi 12345

# 用 DeepSeek
pr-review --base-url https://api.deepseek.com/v1 --api-key sk-xxx --model deepseek-chat review fastapi fastapi 12345

# 查看审查历史
pr-review history

# 启动 webhook 服务（监听 GitHub PR 事件自动审查）
pr-review serve --port 8080 --style lenient

# 带签名验证的 webhook
pr-review serve --port 8080 --webhook-secret your-secret

# 多模型协作审查（DeepSeek 初筛 + GPT-4o 精审）
DEEP_API_KEY=sk-xxx pr-review multi-review fastapi fastapi 12345

# 自定义精审模型和升级阈值
DEEP_API_KEY=sk-xxx pr-review multi-review fastapi fastapi 12345 --deep-model gpt-4o --threshold 8
```

### Webhook 配置

在 GitHub 仓库 → Settings → Webhooks 中：

| 配置项 | 值 |
|--------|-----|
| Payload URL | `http://your-server:8080/webhook` |
| Content type | `application/json` |
| Events | `Pull requests` |
| Secret | 与 `--webhook-secret` 一致 |

## 为什么不用框架

| 需求 | 框架做法 | 本项目做法 |
|------|---------|-----------|
| Agent 循环 | StateGraph / @entrypoint | 一个 while loop |
| 工具注册 | @tool 装饰器 + 自动 schema | 手写 Tool dataclass + JSON Schema |
| 权限控制 | 需自己接 | PermissionGuard，dry_run/live 双模式 |
| 上下文压缩 | 需自己接 | context.py，按优先级裁剪大 diff |
| 状态持久化 | Checkpoint + 数据库 | JSON 文件，够用就行 |
| 审查风格 | prompt 模板系统 | 直接替换 system prompt 字符串 |
| 测试覆盖检查 | 需要条件边 + 分支节点 | 一句 prompt + get_file_content 工具 |
| 部署 | FastAPI + 序列化 graph | 标准库 http.server，零依赖 |
| 多模型协作 | 需要自定义 node + 条件边 | 两阶段 pipeline，模型自判断升级 |

框架帮你做的 80% 是胶水工作。理解原理后，胶水自己写并不难，而且更灵活。

<!-- webhook test @ 2026-07-02T03:43:01Z -->
