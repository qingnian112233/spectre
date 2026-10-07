#!/usr/bin/env python3
"""TaskForge 自主任务锻造台 — 让本bot能把"大目标"锻成可持久化的任务链, 跨会话断点续跑
核心思路(对齐GPT-6 Astra的agentic闭环, 但走轻量自宿主路线):
  1. goal    : 收一个大目标, 自动存成 state 快照 (目标+上下文+时间)
  2. plan    : 把目标拆成多阶段pipelines (recon->ports->web->vuln->report), 每阶段可跑playbook
  3. track   : 每干一步调一次, 记录进度到快照, 跨会话不丢(主人说"继续"能接着来)
  4. status  : 汇总当前锻造台全貌 (跑了哪些阶段/还剩哪些/卡在哪)
目标是"自己蒸自己"的骨架: 减少主人一步步喂, 给个大目标就能自主推进并随时续跑。
用法(插件工具): taskforge '{"op":"goal|plan|track|status","goal":..,"project_id":..,"phase":..,"note":..,"state_dir":..}'
"""
import sys, json, time
from pathlib import Path

STATE_DIR = Path("/opt/deepseek-bot/knowledge/self_learned/taskforge")
STATE_DIR.mkdir(parents=True, exist_ok=True)

PHASES = ["recon", "ports", "web", "vuln", "report"]
STATUS_VALID = ("planned", "running", "done", "blocked")

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def stamp(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

def load(project_id):
    p = STATE_DIR / f"{project_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def save(project_id, d):
    stamp(STATE_DIR / f"{project_id}.json", d)

try:
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception:
    print("err: 参数受损")
    sys.exit(0)

op = a.get("op", "status")
goal = str(a.get("goal", "")).strip()
pid = str(a.get("project_id", "default")).strip() or "default"
phase = str(a.get("phase", "")).strip().lower()
note = str(a.get("note", ""))
state_dir = str(a.get("state_dir", "")).strip()
if state_dir:
    STATE_DIR = Path(state_dir)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

now_s = now()

if op == "goal":
    d = load(pid)
    d.update({
        "goal": goal or d.get("goal", ""),
        "project_id": pid,
        "created": d.get("created", now_s),
        "updated": now_s,
        "phases": d.get("phases", {p: "planned" for p in PHASES}),
        "history": d.get("history", []) + [f"[{now_s}] goal set: {goal or '(unchanged)'}"],
    })
    save(pid, d)
    print(json.dumps({"ok": True, "project_id": pid, "goal": d["goal"], "phases": d["phases"]}, ensure_ascii=False))
    sys.exit(0)

elif op == "plan":
    d = load(pid)
    phases = {p: "planned" for p in PHASES}
    # 按已存状态保序, 新项目从recon开始
    d.setdefault("goal", goal)
    d["phases"] = phases
    d["plan"] = [
        {"phase": "recon", "playbook_act": "recon", "desc": "信息收集 (子域/指纹/资产)"},
        {"phase": "ports", "playbook_act": "ports", "desc": "端口服务扫描"},
        {"phase": "web", "playbook_act": "web", "desc": "Web漏洞扫描"},
        {"phase": "vuln", "playbook_act": "vuln", "desc": "核验+利用(编排核调度)"},
        {"phase": "report", "playbook_act": "report", "desc": "汇总报告"},
    ]
    d["updated"] = now_s
    d.setdefault("history", []).append(f"[{now_s}] plan forged: {pid}")
    save(pid, d)
    print(json.dumps({"ok": True, "project_id": pid, "phases_ready": ["recon"], "plan": d["plan"]}, ensure_ascii=False))
    sys.exit(0)

elif op == "track":
    d = load(pid)
    d.setdefault("phases", {p: "planned" for p in PHASES})
    st = str(a.get("state", "running")).lower()
    if st in STATUS_VALID and phase in d["phases"]:
        d["phases"][phase] = st
    if note:
        d.setdefault("history", []).append(f"[{now_s}] {phase}: {st} - {note}")
    d["updated"] = now_s
    save(pid, d)
    print(json.dumps({"ok": True, "project_id": pid, "tracked": phase, "state": st,
                      "next_recommended": next((p for p in PHASES if d["phases"].get(p) in ("planned","running")), "report")},
                     ensure_ascii=False))
    sys.exit(0)

elif op == "status":
    d = load(pid) if (STATE_DIR / f"{pid}.json").exists() else {}
    if not d:
        print(json.dumps({"ok": False, "msg": f"no taskforge state for {pid}; run op=goal first"}, ensure_ascii=False))
    else:
        phases = d.get("phases", {})
        running = [p for p, s in phases.items() if s == "running"]
        todo = [p for p, s in phases.items() if s in ("planned", "blocked")]
        done_ = [p for p, s in phases.items() if s == "done"]
        out = {"project_id": pid, "goal": d.get("goal", ""),
               "progress": f"{len(done_)}/5 phases done",
               "running": running, "todo": todo, "done": done_,
               "last_updated": d.get("updated", d.get("created", "")),
               "history_tail": d.get("history", [])[-5:]}
        print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)

else:
    print(json.dumps({"err": f"unknown op {op}; try goal/plan/track/status"}, ensure_ascii=False))
    sys.exit(0)
