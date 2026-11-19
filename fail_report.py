#!/usr/bin/env python3
"""聊天室故障上报器（RULE-017）。

主人 2026-11-19：「如果出现聊天室失败投递，要第一时间，通过各自跟我沟通的通道，告知我。
我才可能知道去帮助修复，不然，就卡在哪里了。」

任何脚本 / cron / agent 遇到失败，都调这个上报，**不要静默吞掉**。

用法：
    fail_report.py --source "bridge.py push" --error "rejected non-fast-forward"
    fail_report.py --source "巡检" --error "..." --impact "SG/CN 消息进不来" --action "手动 git pull --rebase"

约定：
- 通道：默认 `hermes send -t qqbot`（UK 的通道）。其他机器用环境变量覆盖：
      export BRIDGE_ALERT_TARGET="qqbot"        # SG 改成自己的
      export BRIDGE_ALERT_TAG="SG"              # 消息里标谁报的
- 去重：同一 (来源 + 错误指纹) **6 小时内只发一次**，但日志每次都记。
- 本脚本**永远 exit 0**，不会把调用方带崩；`--no-send` 可只记日志不发消息（自测用）。
- 关闭开关：BRIDGE_ALERT_DISABLE=1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.environ.get("BRIDGE_ALERTS_HOME", Path.home() / ".hermes" / "collab" / "alerts"))
STATE = HOME / "state.json"
COOLDOWN_H = 6


def bj_now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%m-%d %H:%M")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(st: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2))


def fingerprint(source: str, error: str) -> str:
    norm = " ".join(error.split())[:200]
    return hashlib.sha1(f"{source}|{norm}".encode()).hexdigest()[:12]


def log_line(text: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    f = HOME / f"{datetime.now().strftime('%Y%m%d')}.log"
    with f.open("a") as fh:
        fh.write(text + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="哪一环失败，如 bridge.py push / 巡检 / 面板")
    ap.add_argument("--error", required=True, help="错误原文（会截断 200 字）")
    ap.add_argument("--impact", default="", help="影响：谁的消息进不来 / 什么会卡住")
    ap.add_argument("--action", default="", help="建议动作")
    ap.add_argument("--no-send", action="store_true", help="只记日志，不发消息（自测用）")
    ap.add_argument("--force", action="store_true", help="忽略 6 小时去重")
    a = ap.parse_args()

    if os.environ.get("BRIDGE_ALERT_DISABLE") == "1":
        log_line(f"[{bj_now()}] (已关闭上报) {a.source}: {a.error}")
        return 0

    tag = os.environ.get("BRIDGE_ALERT_TAG", "UK")
    target = os.environ.get("BRIDGE_ALERT_TARGET", "qqbot")
    fp = fingerprint(a.source, a.error)
    st = load_state()
    now = datetime.now(timezone.utc)

    log_line(f"[{bj_now()}] [{tag}] {a.source} | {fp} | {a.error[:300]}")
    if a.no_send:
        print(f"[fail_report] 已记日志（未发送）：{fp} {a.source}")
        return 0

    prev = st.get(fp)
    if prev and not a.force:
        try:
            last = datetime.fromisoformat(prev)
            if now - last < timedelta(hours=COOLDOWN_H):
                print(f"[fail_report] 同类故障 {COOLDOWN_H}h 内已报过，跳过发送：{fp}")
                return 0
        except Exception:
            pass

    text = (f"【聊天室故障·{tag}】{a.source}\n"
            f"错误：{' '.join(a.error.split())[:200]}\n")
    if a.impact:
        text += f"影响：{a.impact}\n"
    if a.action:
        text += f"建议：{a.action}\n"
    text += f"时间：{bj_now()}　（同类故障 {COOLDOWN_H}h 内只报一次）"

    try:
        r = subprocess.run(["hermes", "send", "-q", "-t", target, text],
                           capture_output=True, text=True, timeout=90)
        ok = r.returncode == 0
        if not ok:
            log_line(f"  上报发送失败 exit={r.returncode}: {r.stderr[:200]}")
    except Exception as e:
        ok = False
        log_line(f"  上报发送异常: {e}")

    if ok:
        st[fp] = now.isoformat()
        # 只保留最近 200 条，避免无限增长
        if len(st) > 200:
            st = dict(sorted(st.items(), key=lambda kv: kv[1])[-200:])
        save_state(st)
        print(f"[fail_report] 已通过 {target} 报给主人：{fp}")
    else:
        print(f"[fail_report] 发送失败，仅记日志：{fp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
