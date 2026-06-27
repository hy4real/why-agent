"""CLI 入口 —— 用 click + rich 做交互式 PR 审查。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# 自动加载项目根目录的 .env
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

from .github_client import GitHubClient
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Confirm
from rich.table import Table

from .harness import Agent, AgentConfig
from .permissions import PermissionGuard, PermissionMode
from .pr_tools import build_tools
from .state import StateStore
from .styles import get_style, STYLES, DEFAULT_STYLE

console = Console()


def _confirm_action(action: str) -> bool:
    """权限确认回调。"""
    return Confirm.ask(f"[yellow]确认操作: {action}[/yellow]", default=False)


@click.group()
@click.option("--mode", type=click.Choice(["dry_run", "live"]), default="dry_run", help="运行模式")
@click.option("--model", envvar="PR_REVIEW_MODEL", default="gpt-4o-mini", help="LLM 模型")
@click.option("--base-url", envvar="OPENAI_BASE_URL", help="API base_url (DeepSeek/智谱等兼容接口)")
@click.option("--api-key", envvar="OPENAI_API_KEY", help="API key")
@click.option("--token", envvar="GITHUB_TOKEN", help="GitHub token")
@click.pass_context
def main(ctx, mode: str, model: str, base_url: str | None, api_key: str | None, token: str | None):
    """PR 代码审查 Agent —— 无框架，从零搭建。"""
    ctx.ensure_object(dict)
    ctx.obj["mode"] = PermissionMode(mode)
    ctx.obj["model"] = model
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key
    ctx.obj["token"] = token


@main.command("list")
@click.argument("owner")
@click.argument("repo")
@click.option("--limit", default=5, help="显示数量")
@click.pass_context
def list_prs(ctx, owner: str, repo: str, limit: int):
    """列出仓库的 open PR。"""
    gh = GitHubClient(ctx.obj["token"])
    store = StateStore()
    if not ctx.obj["token"]:
        console.print("[dim]提示: 未设置 GITHUB_TOKEN，无认证请求有严格的 rate limit (60次/小时)[/dim]")
    try:
        prs = gh.list_open_prs(owner, repo, limit)
    except Exception as e:
        if "403" in str(e) or "rate" in str(e).lower():
            console.print("[red]GitHub API rate limit。设置 GITHUB_TOKEN 环境变量可提升到 5000次/小时。[/red]")
        else:
            console.print(f"[red]GitHub API 错误: {e}[/red]")
        sys.exit(1)

    table = Table(title=f"{owner}/{repo} — Open PRs")
    table.add_column("#", style="cyan")
    table.add_column("标题")
    table.add_column("作者", style="green")
    table.add_column("创建时间")
    table.add_column("状态")

    for pr in prs:
        reviewed = "✅ 已审" if store.is_reviewed(owner, repo, pr["number"]) else "⬜ 待审"
        table.add_row(
            str(pr["number"]),
            pr["title"],
            pr["author"],
            pr["created_at"][:10],
            reviewed,
        )

    console.print(table)


@main.command()
@click.argument("owner")
@click.argument("repo")
@click.argument("number", type=int)
@click.option("--style", type=click.Choice(list(STYLES.keys())), default=DEFAULT_STYLE, help="审查风格")
@click.pass_context
def review(ctx, owner: str, repo: str, number: int, style: str):
    """审查指定 PR。"""
    token = ctx.obj["token"]
    if not token:
        console.print("[red]错误: 需要 GITHUB_TOKEN 环境变量[/red]")
        sys.exit(1)

    gh = GitHubClient(token)
    guard = PermissionGuard(ctx.obj["mode"])
    guard.set_confirm_callback(_confirm_action)
    store = StateStore()

    if store.is_reviewed(owner, repo, number):
        prev = store.get_review(owner, repo, number)
        console.print(f"[yellow]该 PR 已于 {prev['reviewed_at']} 审查过[/yellow]")
        if not Confirm.ask("是否重新审查?", default=True):
            return

    tools = build_tools(gh, guard, store)
    review_style = get_style(style)
    agent = Agent(config=AgentConfig(
        model=ctx.obj["model"],
        api_key=ctx.obj["api_key"],
        base_url=ctx.obj["base_url"],
        system_prompt=review_style.system_prompt,
    ))
    for t in tools:
        agent.tools.register(t)

    console.print(f"\n[bold]开始审查 {owner}/{repo}#{number}[/bold]  [dim]风格: {review_style.name} — {review_style.description}[/dim]\n")

    with console.status("[bold green]Agent 运行中..."):
        result = agent.run(f"请审查 {owner}/{repo} 的 PR #{number}。先获取 diff，然后给出详细的 review 意见。")

    console.print("\n" + "=" * 60)
    console.print(Markdown(result))
    console.print("=" * 60)

    # dry_run 模式下提示
    if ctx.obj["mode"] == PermissionMode.DRY_RUN:
        console.print("\n[dim]当前为 dry_run 模式，未发布评论。用 --mode live 启用发布。[/dim]")


@main.command()
@click.option("--limit", default=10, help="显示数量")
def history(limit: int):
    """查看审查历史。"""
    store = StateStore()
    items = store.list_reviewed(limit)

    if not items:
        console.print("[dim]暂无审查记录[/dim]")
        return

    table = Table(title="审查历史")
    table.add_column("PR")
    table.add_column("审查时间")
    table.add_column("摘要")

    for item in items:
        table.add_row(item["pr"], item["reviewed_at"], item.get("summary", ""))

    console.print(table)


@main.command()
@click.argument("owner")
@click.argument("repo")
@click.argument("number", type=int)
@click.option("--style", type=click.Choice(list(STYLES.keys())), default=DEFAULT_STYLE, help="审查风格")
@click.pass_context
def chat(ctx, owner: str, repo: str, number: int, style: str):
    """交互式审查 —— 跟 Agent 对话，追问细节。"""
    token = ctx.obj["token"]
    if not token:
        console.print("[red]错误: 需要 GITHUB_TOKEN 环境变量[/red]")
        sys.exit(1)

    gh = GitHubClient(token)
    guard = PermissionGuard(ctx.obj["mode"])
    guard.set_confirm_callback(_confirm_action)
    store = StateStore()

    tools = build_tools(gh, guard, store)
    review_style = get_style(style)
    agent = Agent(config=AgentConfig(
        model=ctx.obj["model"],
        api_key=ctx.obj["api_key"],
        base_url=ctx.obj["base_url"],
        system_prompt=review_style.system_prompt,
    ))
    for t in tools:
        agent.tools.register(t)

    # 先拉 diff
    console.print(f"\n[bold]正在加载 {owner}/{repo}#{number}...[/bold]\n")
    with console.status("获取 PR 信息..."):
        result = agent.run(f"请获取 {owner}/{repo} 的 PR #{number} 的 diff，做个初步审查，但先不要发评论。")

    console.print(Markdown(result))

    # 进入交互循环
    console.print("\n[dim]输入问题追问细节，输入 quit 退出[/dim]\n")
    while True:
        try:
            user_input = console.input("[bold cyan]> [/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ("quit", "exit", "q"):
            break
        if not user_input.strip():
            continue

        with console.status("[bold green]思考中..."):
            response = agent.run(user_input)
        console.print(Markdown(response))
        console.print()

    console.print("[dim]退出交互模式[/dim]")


@main.command()
@click.option("--port", default=8080, help="监听端口")
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--webhook-secret", envvar="WEBHOOK_SECRET", help="GitHub webhook 签名密钥")
@click.option("--style", type=click.Choice(list(STYLES.keys())), default=DEFAULT_STYLE, help="审查风格")
@click.pass_context
def serve(ctx, port: int, host: str, webhook_secret: str | None, style: str):
    """启动 webhook 服务器，监听 GitHub PR 事件自动审查。"""
    token = ctx.obj["token"]
    if not token:
        console.print("[red]错误: 需要 GITHUB_TOKEN 环境变量[/red]")
        sys.exit(1)

    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from .server import WebhookServer

    server = WebhookServer(
        host=host,
        port=port,
        webhook_secret=webhook_secret,
        github_token=token,
        agent_config=AgentConfig(
            model=ctx.obj["model"],
            api_key=ctx.obj["api_key"],
            base_url=ctx.obj["base_url"],
        ),
        style=style,
        mode=ctx.obj["mode"],
    )

    console.print(f"\n[bold green]PR Review Agent — Webhook Server[/bold green]")
    console.print(f"  监听地址: [cyan]http://{host}:{port}/webhook[/cyan]")
    console.print(f"  审查风格: [cyan]{style}[/cyan]")
    console.print(f"  运行模式: [cyan]{ctx.obj['mode'].value}[/cyan]")
    console.print(f"  签名验证: [cyan]{'启用' if webhook_secret else '未配置（建议设置 WEBHOOK_SECRET）'}[/cyan]")
    console.print(f"\n[dim]在 GitHub repo → Settings → Webhooks 中配置 Payload URL[/dim]")
    console.print(f"[dim]Content type: application/json，Events: Pull requests[/dim]\n")

    server.run()


if __name__ == "__main__":
    main()
