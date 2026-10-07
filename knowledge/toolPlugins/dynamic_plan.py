#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DynamicPlan 动态规划层 — 让攻击DAG根据侦察结果"自重构", 不再一次建死
(L3工具编排 -> L4授权自主红队 的第②块认知轴承)

与已有 pentest_orchestrator 的区别:
  - orchestrator: 确定性调度(建DAG/算就绪/选波次) — 静态
  - dynamic_plan : 认知重构(读环境模型 -> 生成/改写/剪枝 DAG) — 动态

核心循环(闭环, 对齐主人给的路线):
  侦察 -> 环境建模(env_model) -> 动态规划(本工具) -> 执行 -> 闭环验证(verify_chain) -> 回灌模型 -> 再规划
每一步都可以根据"新情报"改写后续节点的: 技术选型/优先级/放弃剪枝.

用法: dynamic_plan '{"op":"...","target":"...",...}'
op:
  seed     : 给目标+已知信息, 生成初始攻击DAG(带技术选型和依赖)
  replan   : 喂入"新情报"(某技术失败/发现新攻击面/WAF拦某token), 重算DAG: 剪枝死路+插入新向量+调优先级
  next     : 从当前DAG里挑出"下一步该打哪个"(综合就绪+优先级+台账)
  tree     : 打印DAG全貌(含剪枝原因)
  reset    : 清空某目标DAG
"""
import sys, json, time
from pathlib import Path

PLAN_DIR = Path("/opt/deepseek-bot/knowledge/self_learned/dynamic_plan")
PLAN_DIR.mkdir(parents=True, exist_ok=True)
ENV_DIR = Path("/opt/deepseek-bot/knowledge/self_learned/env_model")

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def ppath(target):
    import re
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', target)[:120] or "unknown"
    return PLAN_DIR / (safe + ".json")

def load(target):
    p = ppath(target)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def save(target, d):
    ppath(target).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

def load_env(target):
    import re
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', target)[:120] or "unknown"
    p = ENV_DIR / (safe + ".json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

PRIO = {"critical": 0, "high": 1, "medium": 2, "low": 3}

def node(nid, desc, technique, prio="medium", deps=None, phase="recon", verify="", note=""):
    return {"id": nid, "description": desc, "technique": technique, "priority": prio,
            "dependsOn": deps or [], "phase": phase, "verify": verify, "status": "planned",
            "note": note, "updatedAt": now()}

def seed(target, env=None):
    """基于环境模型生成初始DAG"""
    env = env or load_env(target)
    d = {"target": target, "created": now(), "updated": now(), "nodes": {}, "ledger": {"resolved": [], "deadTech": []}, "rounds": []}
    # 基础侦察链
    d["nodes"] = {
        "R1": node("R1", "信息收集/指纹识别", "recon", "high", phase="recon", verify="确认框架/版本/中间件"),
        "R2": node("R2", "端口与服务探测", "ports", "high", ["R1"], phase="ports", verify="开放端口+服务指纹"),
        "R3": node("R3", "Web攻击面枚举(目录/接口/参数)", "web", "high", ["R1"], phase="web", verify="可用入口清单"),
        "V0": node("V0", "技术选型: 按框架版本匹配已知CVE", "cve-map", "medium", ["R2", "R3"], phase="vuln", verify="匹配到可用漏洞"),
    }
    # 若已知WAF信息, 插入WAF绕过节点并调优先级
    waf = env.get("waf", {})
    if waf.get("probed"):
        blk = waf.get("block_tokens", [])
        d["nodes"]["W1"] = node("W1", "WAF绕过后payload构造", "waf-bypass", "critical" if blk else "low",
                                ["R3"], phase="vuln", verify="payload通过WAF到达SQL/命令层",
                                note="拦截token: %s" % ",".join(blk) if blk else "未发现拦截token, 可直打")
    # 时间盲注可用性 -> 决定是否加时间向量
    resp = env.get("response", {})
    if resp.get("timing_reliable") is False:
        d["nodes"]["V1"] = node("V1", "盲注向量(SQLi)", "blind-sqli", "medium", ["R3"], phase="vuln",
                                verify="布尔差异/报错回显(时间盲注不可靠, 禁用以防误判)")
    else:
        d["nodes"]["V1"] = node("V1", "盲注向量(SQLi含时间)", "blind-sqli", "high", ["R3"], phase="vuln",
                                verify="时间/布尔差异")
    d["nodes"]["V2"] = node("V2", "注入/越权/逻辑漏洞验证", "vuln-verify", "high", ["V1"], phase="vuln", verify="硬证据(真实SQL/回显)")
    d["nodes"]["F1"] = node("F1", "成果固化/报告", "report", "medium", ["V2"], phase="report")
    save(target, d)
    return d

def replan(target, intel):
    """喂新情报, 重算DAG: 剪枝 + 插新向量 + 调优先级"""
    d = load(target) or seed(target)
    nodes = d["nodes"]
    changes = []
    intel = intel or {}

    # 1) 某技术失败 -> 标记死路, 剪依赖它的节点
    for fail in intel.get("failedTech", []):
        tid = fail if isinstance(fail, str) else fail.get("id")
        if tid in nodes:
            nodes[tid]["status"] = "abandoned"
            nodes[tid]["note"] = (nodes[tid].get("note", "") + " | 失败: " + str(fail.get("reason", "") if isinstance(fail, dict) else ""))[:300]
            d["ledger"]["deadTech"].append({"id": tid, "reason": str(fail), "at": now()})
            changes.append("abandoned %s" % tid)
            for nid, n in nodes.items():
                if tid in n.get("dependsOn", []) and n["status"] == "planned":
                    n["status"] = "blocked"; changes.append("blocked %s(依赖%s死)" % (nid, tid))

    # 2) 新攻击面 -> 动态插入新节点(技术选型可扩展)
    for sf in intel.get("newSurface", []):
        nid = sf.get("id") or ("S%d" % (len([k for k in nodes if k.startswith("S")]) + 1))
        nodes[nid] = node(nid, sf.get("desc", "新攻击面"), sf.get("technique", "custom"),
                          sf.get("priority", "high"), sf.get("dependsOn", []), phase=sf.get("phase", "vuln"),
                          verify=sf.get("verify", "确认可利用"))
        changes.append("added %s" % nid)

    # 3) WAF情报更新 -> 调W1优先级 + 记录绕过手法
    waf = intel.get("waf", {})
    if waf.get("block_tokens") is not None:
        if "W1" not in nodes:
            nodes["W1"] = node("W1", "WAF绕过", "waf-bypass", "critical", ["R3"], phase="vuln")
        nodes["W1"]["priority"] = "critical" if waf.get("block_tokens") else "low"
        if waf.get("bypass"): d["ledger"].setdefault("bypass", []); d["ledger"]["bypass"] = list(set(d["ledger"].get("bypass", []) + waf["bypass"]))
        changes.append("waf updated")

    # 4) 最高优先级未完成节点置顶
    d["updated"] = now()
    d["rounds"].append({"at": now(), "changes": changes, "intel_keys": list(intel.keys())})
    d["rounds"] = d["rounds"][-30:]
    save(target, d)
    return {"target": target, "changes": changes, "next": next_step(target, d)}

def next_step(target, d=None):
    d = d or load(target)
    nodes = d.get("nodes", {})
    cands = []
    for nid, n in nodes.items():
        if n.get("status") in ("completed", "abandoned", "blocked"): continue
        deps = n.get("dependsOn", [])
        if all(nodes.get(x, {}).get("status") in ("completed", "skipped") for x in deps):
            cands.append((PRIO.get(n.get("priority"), 9), nid, n))
    if not cands:
        pend = [nid for nid, n in nodes.items() if n.get("status") not in ("completed", "abandoned")]
        if not pend: return {"done": True, "hint": "DAG已收敛(全部完成/剪枝)"}
        return {"blocked": True, "pending": pend, "hint": "所有就绪节点的依赖未完成, 检查DAG"}
    cands.sort(key=lambda x: (x[0], x[1]))
    p, nid, n = cands[0]
    return {"next_id": nid, "technique": n.get("technique"), "priority": n.get("priority"),
            "description": n.get("description"), "verify": n.get("verify"), "note": n.get("note"),
            "ready_all": [c[1] for c in cands]}

try:
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception:
    print(json.dumps({"error": "参数受损"}, ensure_ascii=False)); sys.exit(0)

op = a.get("op", "next")
target = str(a.get("target", "")).strip()

if op == "seed":
    d = seed(target)
    print(json.dumps({"ok": True, "target": target, "nodes": len(d["nodes"]), "next": next_step(target, d)}, ensure_ascii=False, indent=1))
elif op == "replan":
    print(json.dumps(replan(target, a.get("intel", {})), ensure_ascii=False, indent=1))
elif op == "next":
    print(json.dumps(next_step(target), ensure_ascii=False, indent=1))
elif op == "tree":
    d = load(target)
    if not d: print(json.dumps({"error": "no plan"}, ensure_ascii=False)); sys.exit(0)
    rows = [{"id": k, "desc": v["description"], "tech": v["technique"], "prio": v["priority"],
             "status": v["status"], "deps": v["dependsOn"]} for k, v in d["nodes"].items()]
    print(json.dumps({"target": target, "nodes": rows, "ledger": d.get("ledger"), "rounds": len(d.get("rounds", []))}, ensure_ascii=False, indent=1))
elif op == "reset":
    p = ppath(target)
    if p.exists(): p.unlink()
    print(json.dumps({"ok": True, "reset": target}, ensure_ascii=False))
else:
    print(json.dumps({"error": "unknown op", "op": op}, ensure_ascii=False))
