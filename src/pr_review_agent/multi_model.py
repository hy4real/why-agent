"""多模型协作 —— DeepSeek 初筛 + GPT-4o 精审。

设计思路：
1. 便宜模型（DeepSeek）快速扫描，给出初步 review + 复杂度评分
2. 如果复杂度超过阈值，升级到强模型（GPT-4o）做深度审查
3. 强模型拿到 diff + 初筛结果，做更精准的分析

这不是"跑两个模型取并集"，而是"让便宜模型决定要不要请贵模型出手"。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .harness import Agent, AgentConfig
from .pr_tools import build_tools
from .github_client import GitHubClient
from .permissions import PermissionGuard, PermissionMode
from .state import StateStore
from .styles import get_style

logger = logging.getLogger("pr-review-agent")

# 初筛模型的 prompt：要求输出结构化的复杂度评分
SCREENING_PROMPT = (
    "你是一个代码审查助手，负责快速扫描 PR 并评估复杂度。\n\n"
    "请完成以下任务：\n"
    "1. 调 get_pr_diff 获取 diff\n"
    "2. 快速分析变更内容\n"
    "3. 在回复的最后，用以下 JSON 格式给出复杂度评分：\n"
    "   {\"complexity\": 1-10, \"reasons\": [...], \"needs_deep_review\": true/false}\n\n"
    "评分标准：\n"
    "- 1-3：简单变更（typo fix、文档更新、小改动）\n"
    "- 4-6：中等变更（新功能、重构、多文件修改）\n"
    "- 7-10：复杂变更（安全相关、核心逻辑、架构改动、大量文件）\n\n"
    "needs_deep_review=true 当：复杂度>=6，或涉及安全/认证/加密/核心架构\n\n"
    "注意：不要调用 post_review，只做分析和评分。"
)

# 精审模型的 prompt
DEEP_REVIEW_PROMPT = (
    "你是一个资深代码审查专家。以下是初筛模型的分析结果，请在此基础上做深度审查。\n\n"
    "初筛结果：\n{screening_result}\n\n"
    "请：\n"
    "1. 验证初筛发现的问题是否准确\n"
    "2. 检查初筛是否遗漏了重要问题\n"
    "3. 对每个问题给出具体的修复建议\n"
    "4. 如果有安全问题，详细说明攻击场景和修复方案\n\n"
    "注意：不要调用 post_review，只在回复中给出审查意见。"
)


@dataclass
class MultiModelConfig:
    """多模型配置。"""
    screening_model: str = "deepseek-chat"
    screening_base_url: str = "https://api.deepseek.com/v1"
    screening_api_key: str = ""
    deep_model: str = "gpt-4o"
    deep_base_url: str = "https://api.openai.com/v1"
    deep_api_key: str = ""
    escalation_threshold: int = 6  # 复杂度 >= 此值时升级


def _parse_complexity(response: str) -> tuple[int, bool]:
    """从初筛结果中提取复杂度评分。"""
    # 尝试从回复末尾提取 JSON
    for line in reversed(response.split("\n")):
        line = line.strip()
        if line.startswith("{") and "complexity" in line:
            try:
                data = json.loads(line)
                return data.get("complexity", 0), data.get("needs_deep_review", False)
            except json.JSONDecodeError:
                continue

    # 回退：用关键词判断
    keywords_high = ["安全", "security", "漏洞", "vulnerability", "认证", "auth", "加密"]
    needs_deep = any(kw in response.lower() for kw in keywords_high)
    return 5 if needs_deep else 3, needs_deep


def run_multi_model_review(
    owner: str,
    repo: str,
    number: int,
    config: MultiModelConfig,
    github_token: str,
    style_name: str = "lenient",
) -> dict:
    """执行多模型协作审查。

    Returns:
        {
            "screening": str,       # 初筛结果
            "complexity": int,      # 复杂度评分
            "escalated": bool,      # 是否升级了
            "deep_review": str | None,  # 精审结果（如果升级了）
            "final": str,           # 最终输出
        }
    """
    gh = GitHubClient(github_token)
    guard = PermissionGuard(PermissionMode.DRY_RUN)
    store = StateStore()
    tools = build_tools(gh, guard, store)

    # ── 阶段一：DeepSeek 初筛 ─────────────────────────────
    logger.info(f"阶段一：DeepSeek 初筛 {owner}/{repo}#{number}")

    screening_agent = Agent(config=AgentConfig(
        model=config.screening_model,
        api_key=config.screening_api_key,
        base_url=config.screening_base_url,
        system_prompt=SCREENING_PROMPT,
    ))
    for t in tools:
        screening_agent.tools.register(t)

    screening_result = screening_agent.run(
        f"请快速扫描 {owner}/{repo} 的 PR #{number}，评估复杂度。"
    )

    complexity, needs_deep = _parse_complexity(screening_result)
    logger.info(f"初筛完成：complexity={complexity}, needs_deep={needs_deep}")

    # ── 判断是否升级 ─────────────────────────────────────
    escalated = needs_deep or complexity >= config.escalation_threshold

    if not escalated:
        logger.info("复杂度较低，跳过精审")
        return {
            "screening": screening_result,
            "complexity": complexity,
            "escalated": False,
            "deep_review": None,
            "final": screening_result,
        }

    # ── 阶段二：GPT-4o 精审 ─────────────────────────────
    logger.info(f"阶段二：{config.deep_model} 精审 {owner}/{repo}#{number}")

    # 重新创建工具（因为 Agent 的 messages 是有状态的）
    tools2 = build_tools(gh, guard, store)
    style = get_style(style_name)

    deep_agent = Agent(config=AgentConfig(
        model=config.deep_model,
        api_key=config.deep_api_key,
        base_url=config.deep_base_url,
        system_prompt=DEEP_REVIEW_PROMPT.format(screening_result=screening_result),
    ))
    for t in tools2:
        deep_agent.tools.register(t)

    deep_result = deep_agent.run(
        f"请对 {owner}/{repo} 的 PR #{number} 做深度审查。先获取 diff，然后结合初筛结果做详细分析。"
    )

    logger.info("精审完成")

    return {
        "screening": screening_result,
        "complexity": complexity,
        "escalated": True,
        "deep_review": deep_result,
        "final": deep_result,
    }
