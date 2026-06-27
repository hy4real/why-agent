"""工具注册与调度 —— 工具的定义、注册、执行。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """工具执行结果，统一包装。"""

    data: Any = None
    error: str | None = None

    def to_text(self) -> str:
        if self.error:
            return json.dumps({"error": self.error}, ensure_ascii=False)
        return json.dumps(self.data, ensure_ascii=False, default=str)


@dataclass
class Tool:
    """一个工具的完整定义。"""

    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Any]

    def execute(self, **kwargs) -> ToolResult:
        try:
            result = self.handler(**kwargs)
            return ToolResult(data=result)
        except Exception as e:
            return ToolResult(error=f"{type(e).__name__}: {e}")


@dataclass
class ToolRegistry:
    """工具注册表 —— 管理所有可用工具。"""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def execute(self, name: str, args: dict) -> ToolResult:
        if name not in self._tools:
            return ToolResult(error=f"Unknown tool: {name}")
        return self._tools[name].execute(**args)

    def openai_schemas(self) -> list[dict]:
        """转成 OpenAI function calling 格式。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]
