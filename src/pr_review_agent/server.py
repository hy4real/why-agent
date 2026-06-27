"""Webhook HTTP Server —— 监听 GitHub webhook，自动触发 PR 审查。

用标准库 http.server 实现，不引入额外依赖，符合"无框架"理念。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from .github_client import GitHubClient
from .harness import Agent, AgentConfig
from .permissions import PermissionGuard, PermissionMode
from .pr_tools import build_tools
from .state import StateStore
from .styles import get_style, DEFAULT_STYLE

logger = logging.getLogger("pr-review-agent")


class WebhookHandler(BaseHTTPRequestHandler):
    """处理 GitHub webhook POST 请求。"""

    # 这些属性由 WebhookServer 注入
    webhook_secret: str | None = None
    gh: GitHubClient = None  # type: ignore
    agent_config: AgentConfig = None  # type: ignore
    review_style_name: str = DEFAULT_STYLE
    auto_mode: PermissionMode = PermissionMode.DRY_RUN

    def do_POST(self):
        if self.path != "/webhook":
            self._respond(404, {"error": "not found"})
            return

        # 读 body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # 验证签名
        if self.webhook_secret:
            sig = self.headers.get("X-Hub-Signature-256", "")
            if not self._verify_signature(body, sig):
                self._respond(401, {"error": "invalid signature"})
                return

        # 解析 payload
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        event = self.headers.get("X-GitHub-Event", "")
        action = payload.get("action", "")

        # 只处理 PR 事件
        if event != "pull_request":
            self._respond(200, {"status": "ignored", "reason": f"event={event}"})
            return

        # 只处理 opened / synchronize（更新推送）
        if action not in ("opened", "synchronize"):
            self._respond(200, {"status": "ignored", "reason": f"action={action}"})
            return

        pr = payload.get("pull_request", {})
        repo_info = payload.get("repository", {})
        owner = repo_info.get("owner", {}).get("login", "")
        repo = repo_info.get("name", "")
        number = pr.get("number", 0)

        if not all([owner, repo, number]):
            self._respond(400, {"error": "missing pr info"})
            return

        logger.info(f"收到 PR 事件: {owner}/{repo}#{number} action={action}")

        # 异步触发审查，不阻塞 webhook 响应
        thread = threading.Thread(
            target=self._run_review,
            args=(owner, repo, number),
            daemon=True,
        )
        thread.start()

        self._respond(200, {
            "status": "accepted",
            "pr": f"{owner}/{repo}#{number}",
            "action": action,
        })

    def _run_review(self, owner: str, repo: str, number: int):
        """在后台线程中执行审查。"""
        try:
            guard = PermissionGuard(self.auto_mode)
            store = StateStore()
            tools = build_tools(self.gh, guard, store)

            style = get_style(self.review_style_name)
            agent = Agent(config=AgentConfig(
                model=self.agent_config.model,
                api_key=self.agent_config.api_key,
                base_url=self.agent_config.base_url,
                system_prompt=style.system_prompt,
            ))
            for t in tools:
                agent.tools.register(t)

            logger.info(f"开始审查 {owner}/{repo}#{number} (style={style.name})")
            result = agent.run(
                f"请审查 {owner}/{repo} 的 PR #{number}。先获取 diff，然后给出详细的 review 意见。"
            )
            logger.info(f"审查完成 {owner}/{repo}#{number}: {result[:200]}...")

        except Exception:
            logger.exception(f"审查失败: {owner}/{repo}#{number}")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """验证 GitHub webhook 签名。"""
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(
            self.webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    def _respond(self, code: int, data: dict):
        """发送 JSON 响应。"""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """覆盖默认日志，用 logging 模块。"""
        logger.info(format % args)


class WebhookServer:
    """Webhook 服务器封装。"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        webhook_secret: str | None = None,
        github_token: str | None = None,
        agent_config: AgentConfig | None = None,
        style: str = DEFAULT_STYLE,
        mode: PermissionMode = PermissionMode.DRY_RUN,
    ):
        self.host = host
        self.port = port

        # 配置 handler 类属性
        WebhookHandler.webhook_secret = webhook_secret
        WebhookHandler.gh = GitHubClient(github_token)
        WebhookHandler.agent_config = agent_config or AgentConfig()
        WebhookHandler.review_style_name = style
        WebhookHandler.auto_mode = mode

    def run(self):
        server = HTTPServer((self.host, self.port), WebhookHandler)
        logger.info(f"Webhook server 启动: http://{self.host}:{self.port}/webhook")
        logger.info(f"审查风格: {WebhookHandler.review_style_name}")
        logger.info(f"运行模式: {WebhookHandler.auto_mode.value}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Webhook server 停止")
            server.server_close()
