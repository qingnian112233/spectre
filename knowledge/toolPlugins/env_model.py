#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EnvModel 环境建模层 — 把"打一步试一步"升级成"先建目标世界模型再决策"
(L3工具编排 -> L4授权自主红队 的第①块认知轴承)

核心思想:
  任何目标在开打前/打的过程中, 持续维护一份"世界模型"(world model):
    - 指纹: 框架/CMS/版本/中间件
    - WAF行为画像: 哪些token被拦(403/msg), 哪些放行, 绕过pattern
    - 响应特征: 基线延迟/抖动(时间盲注能不能用)/状态码分布/回显形态
    - 攻击面台账: 已探明的入口/参数/技术向量
  模型持久化到磁盘, 跨会话可复用 -> 换目标不用从零猜, 说"继续"能接上.

用法(插件工具): env_model '{"op":"...","target":"host",...}'
op:
  probe   : 对目标做一轮"行为探测", 自动记录WAF敏感性token表/延迟抖动/状态码, 写入模型
  merge   : 把一次侦察结果(指纹/端口/技术/攻击面)合并进目标世界模型
  get     : 读目标世界模型 (给LLM决策用)
  list    : 列出所有已建模目标
  forget  : 删除某目标模型
  wafmap  : 返回某目标的WAF"放行/拦截"token字典(直接指导payload构造)
"""
import sys, json, time, os, re
from pathlib import Path

MODEL_DIR = Path("/opt/deepseek-bot/knowledge/self_learned/env_model")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def mpath(target):
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', target)[:120] or "unknown"
    return MODEL_DIR / (safe + ".json")

def load(target):
    p = mpath(target)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def save(target, d):
    mpath(target).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

def blank(target):
    return {
        "target": target, "created": now(), "updated": now(),
        "fingerprint": {}, "ports": {}, "waf": {"probed": False, "block_tokens": [], "pass_tokens": [], "bypass": [], "raw": {}},
        "response": {"baseline_latency_s": None, "jitter_s": None, "timing_reliable": None, "status_dist": {}, "echo_shape": ""},
        "surface": [], "tech_vectors": [], "notes": [], "history": []
    }

# 探测用的"WAF敏感性token"表: 覆盖SQLi/XSS/命令注入/LFI/SSRF/模板 的典型特征
SENSITIVE_TOKENS = [
    ("quote_single", "'"),
    ("quote_double", '"'),
    ("paren", "( )"),
    ("comma", ","),
    ("or_word", "or"),
    ("and_word", "and"),
    ("union", "union"),
    ("select", "select"),
    ("sleep_paren", "sleep(3)"),
    ("sleep_obf", "sleep/**/(3)"),
    ("sleep_enc", "%73%6c%65%65%70"),
    ("benchmark", "benchmark"),
    ("pipe", "||"),
    ("ampamp", "&&"),
    ("comment_hash", "#"),
    ("comment_dash", "--"),
    ("comment_block", "/*x*/"),
    ("semicolon", ";"),
    ("angle", "<>"),
    ("script", "<script>"),
    ("dotdot", "../"),
    ("backslash", "\\"),
    ("pct", "%"),
    ("equals", "="),
]

def probe(target, method="GET", path="/", param="q", timeout=20):
    """对目标做一轮WAF/行为探测, 自动建世界模型"""
    import urllib.request, urllib.error, ssl, statistics
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    schemes = []
    if target.startswith("http"): schemes = [target]
    else: schemes = ["https://" + target, "http://" + target]

    base = None
    for s in schemes:
        try:
            req = urllib.request.Request(s + path, headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
            t0 = time.time(); r = urllib.request.urlopen(req, timeout=timeout, context=ctx); r.read(); dt = time.time() - t0
            base = s; break
        except urllib.error.HTTPError as e:
            base = s; break
        except Exception:
            continue
    if not base:
        return {"error": "target unreachable", "target": target}

    d = load(target) or blank(target)

    # 1) 基线延迟 + 抖动(时间盲注可用性)
    lat = []
    for _ in range(5):
        try:
            req = urllib.request.Request(base + path, headers={"User-Agent": "Mozilla/5.0"})
            t0 = time.time()
            try: urllib.request.urlopen(req, timeout=timeout, context=ctx).read()
            except urllib.error.HTTPError: pass
            lat.append(time.time() - t0)
        except Exception: pass
        time.sleep(0.4)
    if lat:
        bl = round(statistics.median(lat), 3)
        jit = round(max(lat) - min(lat), 3)
        d["response"]["baseline_latency_s"] = bl
        d["response"]["jitter_s"] = jit
        # 抖动 < 1s 且基线 < 8s => 时间盲注可靠; 抖动大 => 不可靠
        d["response"]["timing_reliable"] = (jit < 1.0 and bl < 8.0)

    # 2) WAF敏感性token表
    block, pass_, raw = [], [], {}
    for name, tok in SENSITIVE_TOKENS:
        url = base + path + ("?%s=%s" % (param, tok.replace(" ", "").replace("(", "%28").replace(")", "%29")))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
            t0 = time.time()
            try:
                r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
                body = r.read(2000).decode("utf-8", "ignore"); sc = r.status
            except urllib.error.HTTPError as e:
                body = e.read(2000).decode("utf-8", "ignore"); sc = e.code
            dt = time.time() - t0
            hit = (sc in (403, 406, 429, 501) or "防火墙" in body or "not secure" in body
                   or "blocked" in body.lower() or "waf" in body.lower()[:200] or sc == 400)
            rec = {"status": sc, "latency": round(dt, 2), "blocked": hit}
            raw[name] = rec
            (block if hit else pass_).append(name)
        except Exception as e:
            raw[name] = {"error": str(e)[:60]}; pass_.append(name)
        time.sleep(0.25)

    d["waf"] = {"probed": True, "block_tokens": block, "pass_tokens": pass_, "bypass": d["waf"].get("bypass", []), "raw": raw}
    d["fingerprint"].setdefault("last_tested_url", base)
    d["updated"] = now()
    d["history"] = d.get("history", [])[-50:] + ["[%s] probe %s waf_block=%d" % (now(), base, len(block))]
    save(target, d)
    return {
        "target": target, "base": base,
        "baseline_latency_s": d["response"]["baseline_latency_s"],
        "jitter_s": d["response"]["jitter_s"],
        "timing_reliable": d["response"]["timing_reliable"],
        "waf_blocked": block, "waf_passed": pass_,
        "hint": "timing不可靠时勿用时间盲注, 改用布尔差异/报错回显" if not d["response"]["timing_reliable"] else "时间盲注可用"
    }

try:
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception:
    print(json.dumps({"error": "参数受损" }, ensure_ascii=False)); sys.exit(0)

op = a.get("op", "get")
target = str(a.get("target", "")).strip()

if op == "probe":
    print(json.dumps(probe(target, method=a.get("method", "GET"), path=a.get("path", "/"),
                           param=a.get("param", "q"), timeout=int(a.get("timeout", 20))), ensure_ascii=False, indent=1))

elif op == "merge":
    d = load(target) or blank(target)
    for k in ("fingerprint", "ports"):
        if isinstance(a.get(k), dict): d[k].update(a[k])
    for k in ("surface", "tech_vectors", "notes"):
        if a.get(k): d[k] = (d.get(k) or []) + (a[k] if isinstance(a[k], list) else [a[k]])
    if a.get("bypass"): d["waf"]["bypass"] = list(set(d["waf"].get("bypass", []) + (a["bypass"] if isinstance(a["bypass"], list) else [a["bypass"]])))
    if a.get("echo_shape"): d["response"]["echo_shape"] = str(a["echo_shape"])
    d["updated"] = now(); d["history"] = d.get("history", [])[-50:] + ["[%s] merge" % now()]
    save(target, d)
    print(json.dumps({"ok": True, "target": target, "model": d}, ensure_ascii=False, indent=1))

elif op == "get":
    d = load(target)
    print(json.dumps(d if d else {"error": "no model for %s" % target}, ensure_ascii=False, indent=1))

elif op == "wafmap":
    d = load(target)
    w = d.get("waf", {})
    print(json.dumps({"target": target, "probed": w.get("probed", False),
                      "block_tokens": w.get("block_tokens", []), "pass_tokens": w.get("pass_tokens", []),
                      "bypass": w.get("bypass", []),
                      "guidance": "构造payload时避开block_tokens, 优先用pass_tokens; bypass为已验证绕过手法"},
                     ensure_ascii=False, indent=1))

elif op == "list":
    items = []
    for p in sorted(MODEL_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            items.append({"target": d.get("target"), "updated": d.get("updated"),
                          "waf_blocked": len(d.get("waf", {}).get("block_tokens", [])),
                          "timing_reliable": d.get("response", {}).get("timing_reliable")})
        except Exception: pass
    print(json.dumps({"count": len(items), "models": items}, ensure_ascii=False, indent=1))

elif op == "forget":
    p = mpath(target)
    if p.exists(): p.unlink(); print(json.dumps({"ok": True, "deleted": str(p)}, ensure_ascii=False))
    else: print(json.dumps({"error": "not found"}, ensure_ascii=False))

else:
    print(json.dumps({"error": "unknown op", "op": op}, ensure_ascii=False))
