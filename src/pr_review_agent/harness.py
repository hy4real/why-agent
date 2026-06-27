"""核心 Agent Loop —— learn-claude-code 的 harness 原理实现。

生命周期：发消息 → 检查工具调用 → 执行工具 → 追加结果 → 重复，直到纯文本返回。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from .tools import ToolRegistry, ToolResult


@dataclass
class AgentConfig:
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = None
    max_turns: int = 15
    max_context_tokens: int = 120_000
    system_prompt: str | None = None  # 外部传入，None 则用默认


@dataclass
class Agent:
    """最小可用 Agent —— 一个 while loop，没有框架。"""

    config: AgentConfig = field(default_factory=AgentConfig)
    client: OpenAI = field(default=None)
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    messages: list[ChatCompletionMessageParam] = field(default_factory=list)
    system_prompt: str = (
        "你是一个专业的代码审查助手。审查 PR diff，给出有价值的 review 意见。\n\n"
        "审查流程：\n"
        "1. 先调 get_pr_diff 获取 diff 和 head_sha\n"
        "2. 逐文件分析，如果有具体问题（bug、安全风险、逻辑错误），用 post_inline_comment 精准定位到具体行\n"
        "3. 检查测试覆盖：如果 diff 中修改了业务代码（非测试、非文档、非配置），"
        "用 get_file_content 读取对应的测试文件，判断是否同步更新了测试。"
        "常见模式：src/foo.py → tests/test_foo.py 或 test_foo.py。"
        "如果找不到测试文件或测试未覆盖变更逻辑，在 review 中指出\n"
        "4. 最后用 post_review 给出整体总结\n\n"
        "行内评论规则：\n"
        "- 只对真正有问题的地方发 inline comment，不要每行都评论\n"
        "- commit_sha 从 get_pr_diff 返回的 pr_info.head_sha 获取\n"
        "- line 是 diff 中新增/修改的行号（@@ ... +行号 @@ 格式）\n"
        "- 纯文档改动、typo fix 等低风险变更不需要 inline comment，给整体总结即可"
    )

    def __post_init__(self):
        if self.client is None:
            kwargs = {}
            if self.config.api_key:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self.client = OpenAI(**kwargs)
        # 外部传入的 system_prompt 覆盖默认值
        if self.config.system_prompt is not None:
            self.system_prompt = self.config.system_prompt

    def run(self, user_input: str) -> str:
        """主循环：发消息 → 工具调用 → 追加 → 重复。"""
        self.messages.append({"role": "user", "content": user_input})

        for turn in range(self.config.max_turns):
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "system", "content": self.system_prompt}, *self.messages],
                tools=self.tools.openai_schemas(),
            )

            msg = response.choices[0].message
            self.messages.append(msg)

            # 纯文本 → 结束
            if not msg.tool_calls:
                return msg.content or ""

            # 有工具调用 → 逐个执行，结果追加到上下文
            for tc in msg.tool_calls:
                result = self.tools.execute(tc.function.name, json.loads(tc.function.arguments))
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result.to_text(),
                })

        return "[达到最大轮次，停止]"

    def reset(self):
        self.messages.clear()
