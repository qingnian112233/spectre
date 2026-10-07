#!/usr/bin/env python3
"""PentestOrchestrator 编排核 — 移植PentestCode的确定性DAG调度思想(LLM做战略,此模块只算调度)
用法: planner.py '{"op":"graph","tasks":[...],"agents":{...},"ledger":{...}}'
op:
  graph    : 建/更新任务DAG并计算就绪状态, LLM给任务+依赖+委托人, 返回每波的ready清单
  wave     : 给定ready任务+并发上限, 按优先级选一波可派发的(带resolved台账防重复)
  ledger   : 把某向量标记为resolved/dead, 返回更新后的台账
  status   : 汇总DAG当前全貌
"""
import sys, json, time

STATUS = ["planned", "ready", "dispatched", "running", "completed", "failed", "blocked", "abandoned"]
PRIO = {"critical": 0, "high": 1, "medium": 2, "low": 3}

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def compute_readiness(tasks):
    updated = dict(tasks)
    for tid, t in updated.items():
        if t.get("status") not in ("planned", "blocked"):
            continue
        deps = t.get("dependsOn", [])
        any_failed = any(updated.get(d, {}).get("status") in ("failed", "abandoned") for d in deps)
        all_done = all(updated.get(d, {}).get("status") == "completed" for d in deps)
        if any_failed:
            updated[tid] = dict(t, status="blocked", updatedAt=now())
        elif all_done:
            updated[tid] = dict(t, status="ready", updatedAt=now())
    return updated

def get_ready(tasks):
    return [t for t in tasks.values() if t.get("status") == "ready"]

def resolved_key(ledger, target, technique):
    """是否为已settled的死路向量。保守策略: 只有resolved才拦住, 且target+technique都撞上。"""
    for r in ledger.get("resolved", []):
        if r.get("target") == target and r.get("technique") == technique:
            return True
    return False

def select_wave(ready_tasks, ledger, concurrency=3):
    # 过滤已settled死路
    cand = [t for t in ready_tasks if not resolved_key(ledger, t.get("target"), t.get("technique"))]
    # 按优先级+创建时间排序
    cand.sort(key=lambda t: (PRIO.get(t.get("priority", "medium"), 2), t.get("createdAt", "")))
    picked = cand[:concurrency]
    for t in picked:
        t["status"] = "dispatched"
        t["updatedAt"] = now()
    return picked, cand

def do_graph(tasks, ledger=None):
    upd = compute_readiness(tasks)
    ready = get_ready(upd)
    return {"status": "ok", "tasks": upd, "ready_count": len(ready), "ready_ids": [t["id"] for t in ready], "ledger": ledger or {}}

def do_wave(tasks, ledger, concurrency):
    upd = compute_readiness(tasks)
    ready = get_ready(upd)
    picked, remaining = select_wave(ready, ledger, concurrency)
    return {"status": "ok", "dispatched": [t["id"] for t in picked], "wave_size": len(picked), "remaining_ready": len(remaining), "tasks": upd, "ledger": ledger}

def do_ledger(ledger, target, technique, verdict):
    resolved = list(ledger.get("resolved", []))
    if verdict in ("resolved", "dead", "deadend"):
        resolved.append({"target": target, "technique": technique, "at": now()})
    return {"status": "ok", "resolved_count": len(resolved), "ledger": {"resolved": resolved}}

def do_status(tasks, ledger):
    by_status = {}
    for t in tasks.values():
        by_status.setdefault(t.get("status"), []).append(t["id"])
    blocked = [tid for tid, t in tasks.items() if t.get("status") == "blocked" and t.get("dependsOn")]
    return {"status": "ok", "counts": {s: len(by_status.get(s, [])) for s in STATUS},
            "blocked_waiting": blocked, "resolved_vectors": len(ledger.get("resolved", []))}

def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    op = args.get("op", "graph")
    tasks = args.get("tasks", {})
    ledger = args.get("ledger", {}).get("resolved", {}) or args.get("ledger", {})
    if isinstance(ledger, list):
        ledger = {"resolved": ledger}
    try:
        if op == "graph":
            out = do_graph(tasks, ledger)
        elif op == "wave":
            out = do_wave(tasks, ledger, int(args.get("concurrency", 3)))
        elif op == "ledger":
            out = do_ledger(ledger, args.get("target"), args.get("technique"), args.get("verdict"))
        elif op == "status":
            out = do_status(tasks, ledger)
        else:
            out = {"status": "error", "msg": f"unknown op {op}"}
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "msg": str(e)}))

if __name__ == "__main__":
    main()
