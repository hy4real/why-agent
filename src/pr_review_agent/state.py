"""状态持久化 —— 记住审过的 PR，避免重复。

用 JSON 文件做最简持久化，不引入数据库。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".pr-review-agent"


class StateStore:
    """PR 审查状态存储。"""

    def __init__(self, state_dir: Path = DEFAULT_STATE_DIR):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = state_dir / "reviewed.json"
        self._index: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text())
        return {}

    def _save(self):
        self._index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    def mark_reviewed(self, owner: str, repo: str, number: int, summary: str):
        """标记一个 PR 已审查。"""
        key = f"{owner}/{repo}#{number}"
        self._index[key] = {
            "reviewed_at": datetime.now().isoformat(),
            "summary": summary[:200],
        }
        self._save()

    def is_reviewed(self, owner: str, repo: str, number: int) -> bool:
        """检查 PR 是否已审查。"""
        return f"{owner}/{repo}#{number}" in self._index

    def get_review(self, owner: str, repo: str, number: int) -> dict | None:
        """获取之前的审查记录。"""
        return self._index.get(f"{owner}/{repo}#{number}")

    def list_reviewed(self, limit: int = 20) -> list[dict]:
        """列出最近审查的 PR。"""
        items = [
            {"pr": k, **v}
            for k, v in sorted(self._index.items(), key=lambda x: x[1].get("reviewed_at", ""), reverse=True)
        ]
        return items[:limit]
