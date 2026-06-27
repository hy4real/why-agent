"""GitHub API 客户端 —— 封装 PR 相关操作。"""

from __future__ import annotations

import httpx


class GitHubClient:
    """轻量 GitHub REST API 客户端，只封装 PR 审查需要的接口。"""

    def __init__(self, token: str | None = None):
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url="https://api.github.com", headers=headers, timeout=30)

    def list_open_prs(self, owner: str, repo: str, limit: int = 10) -> list[dict]:
        """列出仓库的 open PR。注意：list 接口不返回 changed_files/additions/deletions。"""
        resp = self._http.get(f"/repos/{owner}/{repo}/pulls", params={"state": "open", "per_page": limit})
        resp.raise_for_status()
        return [
            {
                "number": pr["number"],
                "title": pr["title"],
                "author": pr["user"]["login"],
                "created_at": pr["created_at"],
                "url": pr["html_url"],
            }
            for pr in resp.json()
        ]

    def get_pr_diff(self, owner: str, repo: str, number: int) -> list[dict]:
        """获取 PR 的文件级 diff，返回结构化数据。"""
        resp = self._http.get(
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", ""),  # 二进制文件可能没有 patch
            }
            for f in resp.json()
        ]

    def get_pr_info(self, owner: str, repo: str, number: int) -> dict:
        """获取 PR 基本信息。"""
        resp = self._http.get(f"/repos/{owner}/{repo}/pulls/{number}")
        resp.raise_for_status()
        pr = resp.json()
        return {
            "number": pr["number"],
            "title": pr["title"],
            "body": pr["body"] or "",
            "author": pr["user"]["login"],
            "base": pr["base"]["ref"],
            "head": pr["head"]["ref"],
            "head_sha": pr["head"]["sha"],
            "changed_files": pr["changed_files"],
            "additions": pr["additions"],
            "deletions": pr["deletions"],
        }

    def post_review(self, owner: str, repo: str, number: int, body: str, event: str = "COMMENT") -> dict:
        """提交 PR review 评论。event: COMMENT / APPROVE / REQUEST_CHANGES"""
        resp = self._http.post(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"body": body, "event": event},
        )
        resp.raise_for_status()
        return resp.json()

    def post_inline_comment(self, owner: str, repo: str, number: int, commit_sha: str,
                            path: str, line: int, body: str) -> dict:
        """在 PR 的具体行上发 inline 评论。"""
        resp = self._http.post(
            f"/repos/{owner}/{repo}/pulls/{number}/comments",
            json={
                "body": body,
                "commit_id": commit_sha,
                "path": path,
                "line": line,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_file_content(self, owner: str, repo: str, path: str, ref: str = "main") -> dict:
        """获取仓库中指定文件的内容。ref 可以是分支名、tag 或 commit SHA。"""
        resp = self._http.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            import base64
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:
            content = data.get("content", "")
        return {
            "path": data["path"],
            "size": data["size"],
            "content": content,
            "sha": data["sha"],
        }
