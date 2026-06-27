"""权限控制 —— harness 的安全层。

两种模式：
- dry_run: 只读，不发任何评论到 GitHub
- live: 可以发评论，但需要确认
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(Enum):
    DRY_RUN = "dry_run"  # 只读，不发评论
    LIVE = "live"  # 可发评论，需确认


class PermissionError(Exception):
    pass


class PermissionGuard:
    """权限守卫 —— 拦截危险操作，模拟真实 harness 的权限治理。"""

    def __init__(self, mode: PermissionMode = PermissionMode.DRY_RUN):
        self.mode = mode
        self._confirm_callback = None

    def set_confirm_callback(self, callback):
        """设置确认回调（CLI 里用 rich.prompt 确认）。"""
        self._confirm_callback = callback

    def check_write(self, action: str) -> bool:
        """检查是否允许写操作（发评论等）。

        Returns True if allowed, raises PermissionError if denied.
        """
        if self.mode == PermissionMode.DRY_RUN:
            raise PermissionError(
                f"[DRY_RUN] 拒绝执行写操作: {action}\n"
                f"切换到 live 模式以启用写操作。"
            )

        # live 模式下需要确认
        if self._confirm_callback:
            if not self._confirm_callback(action):
                raise PermissionError(f"用户拒绝操作: {action}")

        return True

    def check_read(self) -> bool:
        """读操作始终允许。"""
        return True
