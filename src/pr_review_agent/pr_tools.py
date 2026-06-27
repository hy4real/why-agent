"""PR 审查专用工具定义 —— 注册到 Agent 的 ToolRegistry。"""

from __future__ import annotations

from .context import compress_diff
from .github_client import GitHubClient
from .permissions import PermissionGuard
from .state import StateStore
from .tools import Tool


def build_tools(gh: GitHubClient, guard: PermissionGuard, store: StateStore) -> list[Tool]:
    """构建 PR 审查所需的全部工具。"""

    def list_prs(owner: str, repo: str, limit: int = 10) -> dict:
        """列出仓库的 open PR。"""
        guard.check_read()
        prs = gh.list_open_prs(owner, repo, limit)
        # 标记哪些已审过
        for pr in prs:
            pr["reviewed"] = store.is_reviewed(owner, repo, pr["number"])
        return {"prs": prs, "count": len(prs)}

    def get_pr_diff(owner: str, repo: str, number: int) -> dict:
        """获取 PR 的压缩 diff，用于 review。"""
        guard.check_read()
        files = gh.get_pr_diff(owner, repo, number)
        compressed = compress_diff(files)
        info = gh.get_pr_info(owner, repo, number)
        return {
            "pr_info": info,
            "diff": compressed,
            "file_count": len(files),
        }

    def post_review(owner: str, repo: str, number: int, body: str, event: str = "COMMENT") -> dict:
        """提交 PR review 评论。event: COMMENT / APPROVE / REQUEST_CHANGES"""
        guard.check_write(f"在 {owner}/{repo}#{number} 发表 review")
        result = gh.post_review(owner, repo, number, body, event)
        store.mark_reviewed(owner, repo, number, body[:200])
        return {"posted": True, "review_id": result.get("id")}

    def post_inline_comment(owner: str, repo: str, number: int, commit_sha: str,
                            path: str, line: int, body: str) -> dict:
        """在 PR 的具体代码行上发 inline 评论。用于精准定位问题。"""
        guard.check_write(f"在 {owner}/{repo}#{number} 的 {path}:{line} 发表行内评论")
        result = gh.post_inline_comment(owner, repo, number, commit_sha, path, line, body)
        return {"posted": True, "comment_id": result.get("id"), "path": path, "line": line}

    def get_file_content(owner: str, repo: str, path: str, ref: str = "main") -> dict:
        """获取仓库中指定文件的内容。用于检查测试覆盖：读取相关测试文件，判断是否有对应测试。"""
        guard.check_read()
        result = gh.get_file_content(owner, repo, path, ref)
        # 截断过大的文件，避免撑爆上下文
        content = result["content"]
        if len(content) > 15_000:
            content = content[:15_000] + f"\n\n... [截断，文件总大小 {result['size']} 字节]"
        return {"path": result["path"], "size": result["size"], "content": content}

    return [
        Tool(
            name="list_prs",
            description="列出指定仓库的 open PR，包含是否已审查的标记",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "GitHub 用户名或组织名"},
                    "repo": {"type": "string", "description": "仓库名"},
                    "limit": {"type": "integer", "description": "返回数量，默认 10"},
                },
                "required": ["owner", "repo"],
            },
            handler=list_prs,
        ),
        Tool(
            name="get_pr_diff",
            description="获取指定 PR 的 diff（已压缩），用于代码审查。返回内容包含 head_sha，供行内评论使用。",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "number": {"type": "integer", "description": "PR 编号"},
                },
                "required": ["owner", "repo", "number"],
            },
            handler=get_pr_diff,
        ),
        Tool(
            name="post_review",
            description="在 PR 上发表整体 review 评论。用于总结性意见。谨慎使用，需要确认。",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "number": {"type": "integer"},
                    "body": {"type": "string", "description": "review 内容"},
                    "event": {
                        "type": "string",
                        "enum": ["COMMENT", "APPROVE", "REQUEST_CHANGES"],
                        "description": "review 类型，默认 COMMENT",
                    },
                },
                "required": ["owner", "repo", "number", "body"],
            },
            handler=post_review,
        ),
        Tool(
            name="post_inline_comment",
            description="在 PR 的具体代码行上发表 inline 评论。用于指出具体问题位置。需要先调 get_pr_diff 获取 head_sha。",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "number": {"type": "integer"},
                    "commit_sha": {"type": "string", "description": "PR 的 head commit SHA，从 get_pr_diff 返回的 pr_info.head_sha 获取"},
                    "path": {"type": "string", "description": "文件路径，如 src/main.py"},
                    "line": {"type": "integer", "description": "要评论的行号"},
                    "body": {"type": "string", "description": "评论内容"},
                },
                "required": ["owner", "repo", "number", "commit_sha", "path", "line", "body"],
            },
            handler=post_inline_comment,
        ),
        Tool(
            name="get_file_content",
            description="获取仓库中指定文件的内容。用于检查测试覆盖：当 diff 中修改了业务代码，读取对应的测试文件判断是否有覆盖。也可用于读取配置文件、文档等。",
            parameters={
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "path": {"type": "string", "description": "文件路径，如 tests/test_api.py"},
                    "ref": {"type": "string", "description": "分支名、tag 或 commit SHA，默认 main"},
                },
                "required": ["owner", "repo", "path"],
            },
            handler=get_file_content,
        ),
    ]
