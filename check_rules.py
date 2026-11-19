#!/usr/bin/env python3
"""规则冲突校验（RULE-018）。

职责：在规则分叉成两套说法之前把它找出来。
    1 编号唯一 / 格式正确
    2 状态合法（active|superseded）
    3 superseded 必须写「被取代：RULE-0xx」且指向存在的规则
    4 **同一主题下不许有两条 active**（这是最主要的分叉来源）
    5 标题高度相似（>0.8）的 active 规则 → 疑似重复
    6 其他文档**复制了规则正文**（连续 ≥25 字与某条 active 规则相同）→ 必须改为引用编号
    7 引用了不存在的编号 → 断链
    8 "被取代"链闭合：active 规则不应被标记为已取代

用法： python check_rules.py [--repo PATH] [--quiet]
退出码：0 全部通过；1 存在 ERROR（CI / 巡检任务据此告警）
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

RULE_RE = re.compile(r"^##\s+(RULE-\d{3})\s+(.+)$", re.M)
FIELD_RE = re.compile(r"^-\s*(状态|槽位|主题|自|正文|被取代|取代)\s*[：:]\s*(.+)$", re.M)
REF_RE = re.compile(r"RULE-\d{3}")
MIN_COPY = 25          # 连续相同字符达到这个长度就算"复制正文"
SIM_WARN = 0.80        # 标题相似度告警阈值

errors: list[str] = []
warns: list[str] = []
oks: list[str] = []


def parse_rules(text: str) -> dict[str, dict]:
    rules: dict[str, dict] = {}
    heads = list(RULE_RE.finditer(text))
    for i, m in enumerate(heads):
        rid, title = m.group(1), m.group(2).strip()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[m.end():end]
        fld = {k: v.strip() for k, v in FIELD_RE.findall(block)}
        fld["title"] = title
        fld["block"] = block
        if rid in rules:
            errors.append(f"编号重复：{rid}（出现两次）")
        rules[rid] = fld
    return rules


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    repo = Path(a.repo)
    rf = repo / "RULES.md"
    if not rf.exists():
        print(f"❌ 找不到 {rf}")
        return 1
    text = rf.read_text()
    rules = parse_rules(text)

    # 0 版本行
    if re.search(r"\*\*版本\*\*：", text):
        oks.append("版本行存在")
    else:
        warns.append("缺少「**版本**：vX.Y」行")

    # 1/2/3 逐条校验
    for rid, f in rules.items():
        st = f.get("状态", "")
        if st not in ("active", "superseded"):
            errors.append(f"{rid} 状态非法：「{st}」（只能是 active / superseded）")
        if not f.get("槽位"):
            errors.append(f"{rid} 缺少「槽位」字段 —— 槽位是冲突检测的依据，必填")
        if not f.get("主题"):
            warns.append(f"{rid} 缺少「主题」（分类标签，建议补）")
        if not f.get("正文"):
            errors.append(f"{rid} 缺少「正文」")
        if st == "superseded":
            rep = f.get("被取代", "")
            if not rep:
                errors.append(f"{rid} 标记 superseded 但没写「被取代：RULE-0xx」")
            elif rep not in rules:
                errors.append(f"{rid} 的被取代指向不存在的规则：{rep}")
        if st == "active" and f.get("被取代"):
            errors.append(f"{rid} 是 active 却写了「被取代」 —— 该改成 superseded")
    if rules:
        oks.append(f"条款解析 {len(rules)} 条")

    # 4 同槽位多条 active（槽位 = 这条规则管的事，同一件事只能有一种规定）
    by_slot: dict[str, list[str]] = {}
    for rid, f in rules.items():
        if f.get("状态") == "active":
            by_slot.setdefault(f.get("槽位", "").strip(), []).append(rid)
    for slot, ids in by_slot.items():
        if slot and len(ids) > 1:
            errors.append(f"槽位「{slot}」下有 {len(ids)} 条 active：{', '.join(ids)}"
                          f" —— 同一件事只许一种规定，改含义请 supersede 旧的")
    oks.append(f"槽位唯一性检查 {len(by_slot)} 个槽位")

    # 5 标题相似
    act = [(rid, f.get("title", "")) for rid, f in rules.items() if f.get("状态") == "active"]
    for i in range(len(act)):
        for j in range(i + 1, len(act)):
            r = difflib.SequenceMatcher(None, act[i][1], act[j][1]).ratio()
            if r >= SIM_WARN:
                warns.append(f"{act[i][0]} 与 {act[j][0]} 标题相似度 {r:.2f} —— 确认是否重复（{act[i][1]} / {act[j][1]}）")

    # 6 其他文档复制规则正文
    bodies = {rid: norm(f.get("正文", "")) for rid, f in rules.items()
              if f.get("状态") == "active" and len(norm(f.get("正文", ""))) >= MIN_COPY}
    # thread.md 是脚本生成的对话记录、DECISIONS.md 是 append-only 历史台账，天然会引用原文 → 不参与复制扫描
    SKIP = {"RULES.md", "thread.md", "DECISIONS.md", "summary.md"}
    docs = [p for p in repo.rglob("*.md")
            if p.name not in SKIP and ".git" not in p.parts
            and not any(seg in p.parts for seg in ("node_modules", ".venv"))]
    for p in docs:
        body = norm(p.read_text(errors="ignore"))
        for rid, b in bodies.items():
            for k in range(0, max(0, len(b) - MIN_COPY), 10):
                frag = b[k:k + MIN_COPY]
                if frag in body:
                    warns.append(f"{p.relative_to(repo)} 复制了 {rid} 的正文片段"
                                 f"「{frag[:18]}…」—— 规则只许引用编号，不许复制")
                    break
    oks.append(f"正文复制扫描 {len(docs)} 个 md")

    # 7 引用完整性
    missing: set[str] = set()
    for p in docs:
        for ref in REF_RE.findall(p.read_text(errors="ignore")):
            if ref not in rules:
                missing.add(f"{p.relative_to(repo)} → {ref}")
    for m in sorted(missing):
        errors.append(f"引用了不存在的规则：{m}")
    oks.append("引用完整性检查完成")

    # 输出
    if not a.quiet or errors:
        for o in oks:
            print(f"✅ {o}")
        for w in warns:
            print(f"⚠️  {w}")
        for e in errors:
            print(f"❌ {e}")
        print()
    if errors:
        print(f"结论：❌ {len(errors)} 个冲突 / 断链，{len(warns)} 个提醒 —— 规则不生效，先修")
        return 1
    print(f"结论：✅ 通过（{len(rules)} 条规则，{len(warns)} 个提醒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
