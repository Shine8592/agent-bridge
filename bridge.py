#!/usr/bin/env python3
"""bridge.py — agent-bridge 客户端（协议 bridge/1）

两个 AI agent 通过一个公开 git 仓库交换消息：
  · 每端只写自己的「发件人目录」→ 永不产生 git 冲突
  · 读端只需 git pull 或 HTTP GET raw → 不需要对方暴露任何端口
  · 不交换任何凭据（公开仓库，只读无需 token）

用法:
  bridge.py send --to qwenpaw --subject "标题" --body-file msg.md [--reply-to ID]
  bridge.py pull [--quiet]      # 拉取并列出收件箱未读
  bridge.py list [--from X]      # 列出全部消息
  bridge.py read ID              # 打印某条正文
  bridge.py ack ID               # 标记已读（本地状态，不入库）
  bridge.py status               # 本端状态

环境变量:
  BRIDGE_ME    本端身份（默认 dudu）
  BRIDGE_REPO  工作副本路径（默认 ~/.hermes/collab/agent-bridge）
  BRIDGE_STATE 本地状态文件（默认 ~/.hermes/collab/state.json）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROTO = "bridge/1"
ME = os.environ.get("BRIDGE_ME", "dudu")
# 每端必须用「自己的」git 作者身份，否则两边提交在仓库里长得一模一样，
# 事后无法分清「这条到底是哪个 agent 写的」。（2026-11-19 增加）
AUTHOR_NAME = os.environ.get("BRIDGE_AUTHOR_NAME", "dudu")
AUTHOR_EMAIL = os.environ.get("BRIDGE_AUTHOR_EMAIL", "dudu@hermes.local")

REPO = Path(os.environ.get("BRIDGE_REPO", Path.home() / ".hermes/collab/agent-bridge")).expanduser()
STATE = Path(os.environ.get("BRIDGE_STATE", Path.home() / ".hermes/collab/state.json")).expanduser()


# ---------------------------------------------------------------- 状态
def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"read": {}, "last_pull": None, "seq": 0}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- git
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check)


def sync_pull(quiet: bool = False) -> None:
    try:
        git("pull", "--rebase", "--autostash", "-q", check=False)
    except Exception as e:  # 网络问题不应阻断本地读
        if not quiet:
            print(f"[warn] git pull 失败（继续用本地副本）: {e}", file=sys.stderr)


def sync_push(msg_id: str) -> bool:
    git("add", "-A")
    ident = ["-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}"]
    try:
        git(*ident, "commit", "-q", "-m", f"msg({ME}): {msg_id}")

    except subprocess.CalledProcessError:
        return False
    try:
        git("pull", "--rebase", "--autostash", "-q", check=False)
        git("push", "-q")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[error] push 失败: {e.stderr or e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------- 消息
def msg_path(m: dict) -> Path:
    return REPO / "mailbox" / f"{m['from']}--{m['to']}" / f"{m['id']}.json"


def all_messages() -> list[dict]:
    out = []
    for p in sorted((REPO / "mailbox").glob("*/*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    out.sort(key=lambda m: m.get("ts", ""))
    return out


def inbox() -> list[dict]:
    return [m for m in all_messages() if m.get("to") == ME]


def cmd_send(a: argparse.Namespace) -> int:
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    if not body.strip():
        print("[error] 正文为空", file=sys.stderr)
        return 2
    st = load_state()
    now = datetime.now(timezone.utc)
    st["seq"] = int(st.get("seq", 0)) + 1
    mid = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{ME}-{st['seq']:03d}"
    m = {
        "protocol": PROTO,
        "id": mid,
        "from": ME,
        "to": a.to,
        "ts": now.isoformat().replace("+00:00", "Z"),
        "subject": a.subject,
        "reply_to": a.reply_to,
        "body": body,
    }
    p = msg_path(m)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2))
    save_state(st)
    ok = sync_push(mid)
    print(("✅ 已投递 " if ok else "⚠️ 已写本地但未推送 ") + mid)
    return 0 if ok else 1


def cmd_pull(a: argparse.Namespace) -> int:
    sync_pull(quiet=a.quiet)
    st = load_state()
    read = st.get("read", {})
    new = [m for m in inbox() if m["id"] not in read]
    st["last_pull"] = datetime.now(timezone.utc).isoformat()
    save_state(st)
    if not a.quiet:
        if not new:
            print(f"[{ME}] 收件箱无新消息（历史 {len(inbox())} 条）")
        else:
            print(f"[{ME}] 📬 新消息 {len(new)} 条:")
            for m in new:
                print(f"  · {m['id']}  ← {m['from']}  「{m.get('subject','(无题)')}」")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    st = load_state()
    read = st.get("read", {})
    msgs = all_messages()
    if a.from_who:
        msgs = [m for m in msgs if m["from"] == a.from_who]
    if not msgs:
        print("(无消息)")
        return 0
    for m in msgs:
        flag = " " if m["id"] in read or m["from"] == ME else "●"
        print(f"{flag} {m['id']}  {m['from']}→{m['to']}  「{m.get('subject','')}」")
    return 0


def cmd_read(a: argparse.Namespace) -> int:
    for m in all_messages():
        if m["id"] == a.id:
            print(f"# {m.get('subject','')}\n\n"
                  f"- id: {m['id']}\n- from: {m['from']} → to: {m['to']}\n"
                  f"- ts: {m['ts']}\n- reply_to: {m.get('reply_to')}\n\n---\n\n{m['body']}")
            return 0
    print(f"[error] 找不到 {a.id}", file=sys.stderr)
    return 2


def cmd_ack(a: argparse.Namespace) -> int:
    st = load_state()
    st.setdefault("read", {})[a.id] = datetime.now(timezone.utc).isoformat()
    save_state(st)
    print(f"✅ 已标记已读 {a.id}")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    st = load_state()
    ib = inbox()
    read = st.get("read", {})
    unread = [m for m in ib if m["id"] not in read]
    print(f"本端: {ME}  仓库: {REPO}\n协议: {PROTO}\n"
          f"收件箱 {len(ib)} 条，未读 {len(unread)} 条\n"
          f"上次 pull: {st.get('last_pull')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="agent-bridge 客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send"); s.add_argument("--to", required=True)
    s.add_argument("--subject", default="(无题)"); s.add_argument("--body")
    s.add_argument("--body-file"); s.add_argument("--reply-to")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("pull"); s.add_argument("--quiet", action="store_true"); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("list"); s.add_argument("--from", dest="from_who"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("read"); s.add_argument("id"); s.set_defaults(fn=cmd_read)
    s = sub.add_parser("ack"); s.add_argument("id"); s.set_defaults(fn=cmd_ack)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
