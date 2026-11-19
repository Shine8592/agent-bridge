#!/usr/bin/env python3
"""按项目生成 summary.md（决议 UK-013 第 6 条）。

结构固定四段：已决 / 未决·待回执 / 待验收 / 最近 10 条 + 怎么用。
owner 可手工在末尾追加「下一步」，脚本重跑不会覆盖手工段落（用标记包裹）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path.home() / ".hermes/collab/agent-bridge-private"
ME = "UK"
RECENT = 10
MANUAL_BEGIN = "<!-- BEGIN MANUAL -->"
MANUAL_END = "<!-- END MANUAL -->"


def all_messages(project: str) -> list[dict]:
    out = []
    for f in (REPO / "projects" / project).rglob("msg/*/*.json"):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    out.sort(key=lambda m: m.get("ts", ""))
    return out


def meta_of(project: str) -> dict:
    f = REPO / "projects" / project / "meta.json"
    m = {"id": project, "name": project, "brief": "", "status": "active"}
    if f.exists():
        try:
            m.update(json.loads(f.read_text()))
        except Exception:
            pass
    return m


def parse_decisions(project: str) -> list[tuple[str, str, str]]:
    """返回 [(编号, 标题, 状态)]"""
    f = REPO / "projects" / project / "DECISIONS.md"
    if not f.exists():
        return []
    txt = f.read_text()
    out = []
    for m in re.finditer(r"^## (D-\d+)\s+(.+)$", txt, re.M):
        seg = txt[m.end(): m.end() + 600]
        st = re.search(r"\*\*状态\*\*：\s*([a-z\-]+)", seg)
        out.append((m.group(1), m.group(2).strip(), st.group(1) if st else "?"))
    return out


def hours_since(ts: str) -> float:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return 0.0


def bj(ts: str) -> str:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return t.strftime("%m-%d %H:%M")
    except Exception:
        return ts[:16]


def build(project: str) -> str:
    meta = meta_of(project)
    msgs = all_messages(project)
    st = meta.get("status", "active")
    last = msgs[-1]["ts"] if msgs else ""
    L = []
    L.append(f"# {meta.get('name', project)} · 项目摘要")
    L.append("")
    L.append(f"> 自动生成（`render_summary.py`）· 协议 bridge/1.5 · **{st}** · "
             f"{len(msgs)} 条 · 最后活动 {bj(last) if last else '—'} 北京")
    if meta.get("brief"):
        L.append(f"> {meta['brief']}")
    L.append("")
    L.append("**入群包 = 本文件 + `DECISIONS.md` + 最近 10 条**（决议第 7 条，别读全量历史）")
    L.append("")

    # ---- 已决 ----
    L.append("## 一、已决")
    decs = parse_decisions(project)
    if decs:
        for did, title, dst in decs:
            mark = {"accepted": "✅", "proposed": "⏳", "superseded": "♻️"}.get(dst, "•")
            L.append(f"- {mark} **{did}** {title}（{dst}）")
        L.append(f"\n详见 [`DECISIONS.md`](DECISIONS.md)。")
    else:
        L.append("- （暂无决议台账）")
    L.append("")

    # ---- 未决 / 待回执 ----
    L.append("## 二、未决 / 待回执")
    pend = []
    for m in msgs:
        for who in m.get("await_reply") or []:
            replied = any(x.get("from", "").upper() == who.upper()
                          and x.get("ts", "") > m.get("ts", "") for x in msgs)
            if not replied:
                pend.append((m, who))
    if pend:
        for m, who in pend:
            h = hours_since(m.get("ts", ""))
            flag = "⏳ pending" if h > 24 else f"{h:.1f}h"
            L.append(f"- **{m['id']}** {m.get('subject','')[:52]} —— 待 **{who}**（{flag}）")
    else:
        L.append("- 无（所有点名都已回应）")
    L.append("")

    # ---- 待验收 ----
    L.append("## 三、待验收（决议第 2 条：非提案者验收 + 可执行证据）")
    decisions = [m for m in msgs if m.get("kind") == "decision"]
    reviews = {m.get("target_id") for m in msgs if m.get("kind") == "review"}
    todo = [m for m in decisions if m["id"] not in reviews]
    if todo:
        for m in todo[-5:]:
            L.append(f"- **{m['id']}** {m.get('subject','')[:52]} —— 尚无 review 指向它")
    else:
        L.append("- 无")
    L.append("")

    # ---- 最近 10 条 ----
    L.append(f"## 四、最近 {RECENT} 条")
    L.append("")
    L.append("| 时间(北京) | 谁 | 类型 | 标题 | stance |")
    L.append("|---|---|---|---|---|")
    for m in msgs[-RECENT:]:
        L.append(f"| {bj(m.get('ts',''))} | {m.get('from','?')} | {m.get('kind','note')} | "
                 f"{m.get('subject','')[:40]} | {m.get('stance','') or '—'} |")
    L.append("")

    # ---- 手工段落（保留）----
    f = REPO / "projects" / project / "summary.md"
    manual = ""
    if f.exists():
        old = f.read_text()
        i, j = old.find(MANUAL_BEGIN), old.find(MANUAL_END)
        if i >= 0 and j > i:
            manual = old[i + len(MANUAL_BEGIN): j].strip()
    if not manual:
        manual = "（负责人补充：下一步、风险、需要主人拍板的事）"
    L.append("## 五、下一步 / 风险 / 待主人拍板")
    L.append("")
    L.append(MANUAL_BEGIN)
    L.append(manual)
    L.append(MANUAL_END)
    L.append("")
    return "\n".join(L)


def main() -> int:
    ps = [d.name for d in sorted((REPO / "projects").glob("*/")) if (d / "meta.json").exists()]
    for pid in ps:
        out = REPO / "projects" / pid / "summary.md"
        out.write_text(build(pid))
        print(f"  ✅ {pid:22s} summary.md {len(out.read_text())} 字节")
    subprocess.run(["git", "add", "projects/*/summary.md"], cwd=REPO, check=False)
    r = subprocess.run(["git", "-c", "user.name=" + ME, "-c", "user.email=uk@agent.local",
                        "commit", "-q", "-m", "docs: 按决议第6条生成各项目 summary.md"],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode == 0:
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=REPO, check=False)
        print("  📤 已提交并推送")
    else:
        print("  (无变化，未提交)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
