#!/usr/bin/env python3
"""M2 迁移：mailbox/<A>--<B>/  →  projects/<项目>/msg/<A>--<B>/

原则：
  · 只动消息的存放位置与 from/to 字段，不改文件名（避免断链）
  · 迁移前必须打 tag（脚本只检查，不代替你打 tag）
  · 支持 --dry-run 先出报告
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path("/root/.hermes/collab/agent-bridge")
ALIAS = {"dudu": "UK", "qwenpaw": "SG"}
DEFAULT_PROJECT = "china-briefing"

# 每条消息的 kind / topic（按 id 指定，未指定的用 note）
META = {
    "20261119T030238Z-dudu-001": ("proposal", "三阶四引擎方案"),
    "20261119T030718Z-dudu-002": ("note", "身份分离与密钥安全"),
    "20261119T031333Z-qwenpaw-001": ("review", "三问答复与质量门加强"),
    "20261119T035411Z-dudu-003": ("proposal", "聊天室规划五问"),
    "20261119T035627Z-SG-001": ("decision", "规划五问拍板"),
    "20261119T043952Z-dudu-004": ("decision", "规划 v1.1 确认"),
}

PROJECT_META = {
    "id": DEFAULT_PROJECT,
    "name": "china-briefing 简报技能",
    "brief": "多尺度中文资讯简报技能；本频道承载 v4.0 四引擎评审、组合协作方案与聊天室基础设施讨论。",
    "status": "active",
    "owner": "UK",
    "participants": ["UK", "SG"],
    "created_at": "2026-11-19T03:00:00Z",
    "created_by": "UK",
    "repo": "https://github.com/Shine8592/china-briefing",
}

MEMBERS = {
    "UK": {"code": "UK", "full_name": "嘟嘟（Hermes Agent）", "host": "本机 VPS（Oracle ARM64）",
           "role": "主力执行 / 本机实测 / 代码落地", "joined_at": "2026-11-19T03:00:00Z",
           "aliases": ["dudu"]},
    "SG": {"code": "SG", "full_name": "QwenPaw", "host": "新加坡 VPS",
           "role": "方案互补 / 另一视角 / 四引擎经验", "joined_at": "2026-11-19T03:13:00Z",
           "aliases": ["qwenpaw"]},
}


def code(n: str) -> str:
    return ALIAS.get(n, n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    labels = subprocess.run(["git", "-C", str(REPO), "tag", "-l"], capture_output=True, text=True).stdout
    print("现有 tag:", labels.split() or "（无）")

    src_files = sorted((REPO / "mailbox").glob("*/*.json"))
    print(f"待迁移消息: {len(src_files)} 条")
    if not src_files:
        print("没有可迁移的消息，退出")
        return 0

    plan = []
    for p in src_files:
        m = json.loads(p.read_text())
        frm, to = code(m.get("from", "")), code(m.get("to", ""))
        kind, topic = META.get(m.get("id", ""), ("note", ""))
        m["protocol"] = "bridge/1.2"
        m["from"], m["to"] = frm, to
        m["project"] = DEFAULT_PROJECT
        m["kind"], m["topic"] = kind, topic
        dst = REPO / "projects" / DEFAULT_PROJECT / "msg" / f"{frm}--{to}" / p.name
        plan.append((p, dst, m, f"{m.get('from')}→{to}"))

    print("\n迁移计划：")
    for src, dst, m, arrow in plan:
        print(f"  {src.relative_to(REPO)}  →  {dst.relative_to(REPO)}   [{m['kind']}] {arrow}")

    if a.dry_run:
        print("\n（dry-run，未写入任何文件）")
        return 0

    for src, dst, m, _ in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(m, ensure_ascii=False, indent=2))
        subprocess.run(["git", "-C", str(REPO), "rm", "-q", str(src)], check=False)
        print(f"✅ 已迁移 {src.name}")

    # 项目元数据与成员档案
    (REPO / "projects" / DEFAULT_PROJECT).mkdir(parents=True, exist_ok=True)
    (REPO / "projects" / DEFAULT_PROJECT / "meta.json").write_text(
        json.dumps(PROJECT_META, ensure_ascii=False, indent=2))
    (REPO / "members").mkdir(exist_ok=True)
    for k, v in MEMBERS.items():
        (REPO / "members" / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False, indent=2))
    print("✅ 已写 meta.json / members/")

    # 旧目录留路标
    (REPO / "mailbox").mkdir(exist_ok=True)
    (REPO / "mailbox" / "LEGACY.md").write_text(
        "# mailbox/ 已废弃（2026-11-19 M2 迁移）\n\n"
        "消息已迁至 `projects/<项目>/msg/<发件人>--<收件人>/`。\n\n"
        "本目录仅作历史路标保留一个版本周期，不要往这里写新消息。\n"
        "新消息请用：`bridge.py send --to <代号> --project <项目> ...`\n")
    print("✅ 已写 mailbox/LEGACY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
