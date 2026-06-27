"""上下文压缩 —— 大 diff 的智能裁剪，harness 核心能力之一。

真实 PR 可能有上百个文件、几千行 diff，直接塞进上下文会爆。
这里的策略：按文件优先级排序 + 截断大 patch + 摘要替代细节。
"""

from __future__ import annotations

import re

MAX_PATCH_CHARS = 3000  # 单个文件 patch 的最大字符数
MAX_TOTAL_CHARS = 50_000  # 所有 diff 加起来的最大字符数

# 这些文件类型优先级低，大改也不太需要人工审
LOW_PRIORITY_PATTERNS = [
    ".lock", "package-lock", "yarn.lock", "pnpm-lock",
    ".min.js", ".min.css", ".map",
    "generated", "auto-generated", "pb.go", "_grpc.py",
]

# 这些文件类型值得重点审
HIGH_PRIORITY_PATTERNS = [
    ".py", ".ts", ".js", ".go", ".rs", ".java",
    "Dockerfile", "docker-compose", ".yaml", ".yml",
    "migration", ".sql",
]


def compress_diff(files: list[dict]) -> str:
    """将 PR 的文件 diff 列表压缩成适合 LLM 消费的文本。

    策略：
    1. 按优先级排序（高优先文件在前）
    2. 大 patch 截断，标注"[已截断]"
    3. 总量超限后，剩余文件只给摘要
    """
    sorted_files = sorted(files, key=_file_priority, reverse=True)

    parts: list[str] = []
    total_chars = 0

    for f in sorted_files:
        filename = f["filename"]
        status = f["status"]
        patch = f.get("patch", "")

        if not patch:
            parts.append(f"### {filename} ({status}) — 无 patch（二进制文件）")
            continue

        # 截断大 patch
        if len(patch) > MAX_PATCH_CHARS:
            patch = patch[:MAX_PATCH_CHARS] + f"\n... [截断，原始 {len(patch)} 字符]"

        # 给 patch 加行号标注，方便 agent 引用
        annotated_patch = _annotate_line_numbers(patch)
        section = f"### {filename} ({status}, +{f['additions']}/-{f['deletions']})\n```diff\n{annotated_patch}\n```"

        if total_chars + len(section) > MAX_TOTAL_CHARS:
            remaining = len(sorted_files) - sorted_files.index(f)
            parts.append(f"\n... 还有 {remaining} 个文件未展示（总量超限 {MAX_TOTAL_CHARS} 字符）")
            break

        parts.append(section)
        total_chars += len(section)

    return "\n\n".join(parts)


def _file_priority(f: dict) -> int:
    """文件优先级：高优先 → 2，普通 → 1，低优先 → 0。"""
    name = f["filename"].lower()
    for pat in LOW_PRIORITY_PATTERNS:
        if pat in name:
            return 0
    for pat in HIGH_PRIORITY_PATTERNS:
        if pat in name:
            return 2
    return 1


def _annotate_line_numbers(patch: str) -> str:
    """给 diff patch 加行号标注。

    解析 @@ -old,count +new,count @@ 标记，在每行前加上新文件行号。
    让 agent 能精准引用 "第 42 行"。
    """
    lines = patch.split("\n")
    result: list[str] = []
    current_line = 0

    for line in lines:
        # 解析 @@ hunk header
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            result.append(line)
            continue

        # 上下文行和新增行都标注行号
        if line.startswith("+") and not line.startswith("+++"):
            result.append(f"L{current_line}: {line}")
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            # 删除行不占新文件行号，用 - 标记
            result.append(f"    {line}")
        else:
            # 上下文行
            result.append(f"L{current_line}: {line}")
            current_line += 1

    return "\n".join(result)
