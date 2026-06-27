# PR Review Agent — 开发计划

## 已完成

- [x] 核心 agent loop（while 循环 + 工具调度）
- [x] GitHub API 集成（list PRs, get diff, post review）
- [x] 上下文压缩（大 diff 按优先级裁剪）
- [x] 权限控制（dry_run/live 双模式）
- [x] 状态持久化（JSON 记住审过的 PR）
- [x] CLI 入口（click + rich）
- [x] 自定义 LLM provider（DeepSeek/智谱等兼容接口）
- [x] **行内评论** — agent 能在具体代码行上发 inline comment，精准定位问题
- [x] **文件级分析** — 大 PR 先逐文件分析，再给整体结论，提升审查质量

### 中优先级

- [x] **webhook 自动触发** — HTTP server 监听 GitHub webhook，PR 创建/更新时自动审查
- [x] **审查风格配置** — strict / lenient / security-only，system prompt 切换
- [x] **测试覆盖检查** — 读测试文件，判断"改了业务代码但没改测试"

### 低优先级

- [ ] **PR 风险评分**
- [ ] **审查历史统计分析**
