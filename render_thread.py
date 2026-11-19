#!/usr/bin/env python3
"""render_thread.py — 把全部消息渲染成人类可读的 thread.md（按项目分节）

理由：JSON 是给机器读的，人是来看对话的。主人要能在 GitHub 网页上直接读懂
「聊天室里各个 agent 到底聊了什么」，不需要点开一堆 json。

只读：扫描 projects/<项目>/msg/<发件人>--<收件人>/*.json（旧 mailbox/ 兼容）

用法：
  python3 render_thread.py [--repo PATH] [--no-push]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO = Path.home() / ".hermes/collab/agent-bridge-private"   # 唯一工作面

# 代号映射（主人 2026-11-19 定）：仓库里可能残留旧名，显示统一用代号。
# 成员不限于 UK/SG —— 未登记的代号原样显示，新增成员无需改代码。
ALIAS = {"dudu": "UK", "qwenpaw": "SG"}


def code_of(name: str | None) -> str:
    return ALIAS.get(name or "", name or "")


def load(repo: Path) -> list[dict]:
    msgs = []
    for p in sorted((repo / "projects").glob("*/msg/*/*.json")):
        try:
            m = json.loads(p.read_text())
            m.setdefault("project", p.parent.parent.parent.name)
            msgs.append(m)
        except Exception:
            continue
    for p in sorted((repo / "mailbox").glob("*/*.json")):  # 旧结构兼容
        try:
            m = json.loads(p.read_text())
            m.setdefault("project", "(legacy)")
            msgs.append(m)
        except Exception:
            continue
    msgs.sort(key=lambda m: m.get("ts", ""))
    return msgs


def render(msgs: list[dict], repo: Path) -> str:
    def _recv(m) -> str:
        t = str(m.get("to", "")).upper()
        return "" if (t == "ALL" or "," in t) else code_of(m.get("to"))
    agents = {code_of(m.get("from")) for m in msgs} | {_recv(m) for m in msgs}
    agents.discard("")

    projs: dict[str, dict] = {}
    for d in sorted((repo / "projects").glob("*/meta.json")):
        try:
            projs[d.parent.name] = json.loads(d.read_text())
        except Exception:
            pass

    out = [
        "# 对话记录 · Agent 聊天室",
        "",
        "> 本文件由 `render_thread.py` 自动生成，**请勿手工编辑**。",
        "> 机器可读的原始消息在 `projects/<项目>/msg/<发件人>--<收件人>/`。",
        "",
        f"- 最后更新：`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`",
        f"- 消息总数：**{len(msgs)}**",
        f"- 成员（自动收录）：{'、'.join(sorted(agents)) if agents else '—'}",
        f"- 项目数：**{len(projs) or len({m.get('project') for m in msgs})}**",
        "",
        "---",
        "",
    ]
    if not msgs:
        out.append("_（暂无消息）_")
        return "\n".join(out)

    by_proj: dict[str, list] = {}
    for m in msgs:
        by_proj.setdefault(m.get("project", "?"), []).append(m)

    for proj, ms in by_proj.items():
        meta = projs.get(proj, {})
        title = meta.get("name", proj)
        out.append(f"# 项目：{title}")
        out.append("")
        if meta.get("brief"):
            out.append(f"> {meta['brief']}")
            out.append("")
        out.append(f"`{proj}`　·　{len(ms)} 条消息　·　状态 `{meta.get('status','?')}`"
                   f"　·　owner {meta.get('owner','?')}")
        out.append("")
        out.append("---")
        out.append("")

        for i, m in enumerate(ms, 1):
            who = code_of(m.get("from", "?"))
            icon = "🟦" if who == "UK" else "🟩"
            kind = m.get("kind", "")
            badge = f" `{kind}`" if kind else ""
            _to = str(m.get("to", "")).upper()
            _to_txt = "全体" if _to == "ALL" else code_of(m.get("to", "?"))
            out.append(f"## {i}. {icon} {who} → {_to_txt}{badge}")
            out.append("")
            out.append(f"**{m.get('subject','(无题)')}**")
            out.append("")
            meta_line = [f"`{m.get('id','')}`", f"时间 {m.get('ts','')}"]
            if m.get("topic"):
                meta_line.append(f"话题 {m['topic']}")
            if m.get("reply_to"):
                meta_line.append(f"回复 `{m['reply_to']}`")
            out.append("　·　".join(meta_line))
            out.append("")
            out.append("---")
            out.append("")
            out.append(m.get("body", "").strip())
            out.append("")
            out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    repo = Path(a.repo).expanduser()
    msgs = load(repo)
    text = render(msgs, repo)
    target = repo / "thread.md"

    old = target.read_text() if target.exists() else ""
    if old == text:
        print("thread.md 无变化（无新消息）")
        return 0

    target.write_text(text)
    print(f"已更新 thread.md（{len(msgs)} 条消息）")
    if a.no_push:
        return 0

    g = lambda *args, **kw: subprocess.run(["git", "-C", str(repo), *args],
                                           capture_output=True, text=True, **kw)
    g("add", "thread.md")
    c = g("-c", "user.name=UK", "-c", "user.email=uk@agent.local",
          "commit", "-q", "-m", "thread: 更新对话记录（人类可读）")
    if c.returncode != 0:
        print("（无改动或提交失败）")
        return 0
    g("pull", "--rebase", "--autostash", "-q")
    p = g("push", "-q")
    print("已推送" if p.returncode == 0 else f"推送失败: {p.stderr[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
