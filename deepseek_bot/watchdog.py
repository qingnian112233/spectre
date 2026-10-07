# -*- coding: utf-8 -*-
"""值守模式(Watchdog) —— 2026-09-11 新增

能力: 定期检查一个目标, **只在发生变化时**通知, 可选变化后自动深挖。
- kind=http   : 抓 URL(GET/POST), 可配 grep 正则只取关键片段; 变化判据=内容哈希
- kind=cmd    : 跑 shell 命令, 比较输出哈希(如 `nmap -p 80 host | grep open`)
- kind=port   : 检查 TCP 端口状态(open/closed), 变化即通知
- kind=file   : 文件哈希(如源码/配置被改)
- kind=keyword: 搜索关键词, 比较结果集(如 CVE/PoC 新帖)

存储: /opt/deepseek-bot/watches.json
调度: bot 侧后台线程每 60s 调 run_due(); 通知走回调(由 bot 注入, 用 Bot API 发消息)
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import time

STORE = "/opt/deepseek-bot/watches.json"
_LOCK = None
_cfg = {"notify": None}   # bot 注入: notify(chat, text, buttons=None)
_FAILQ = []               # 2026-10-01 连续失败告警队列(bot 线程 drain_fails() 取走)
_FAIL_ALERT_AT = 3        # 连续失败几次开始告警


def set_notifier(fn):
    _cfg["notify"] = fn


def load():
    try:
        if os.path.exists(STORE):
            d = json.load(open(STORE, encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def save(d):
    try:
        tmp = STORE + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, STORE)
    except Exception as e:
        print(f"[watch] 保存失败: {e}", flush=True)


def add(name, kind, target, interval=600, grep="", notify="change", then="", chat=0, timeout=30):
    d = load()
    wid = "w" + str(int(time.time()))[-6:] + str(len(d) % 90)
    d[wid] = {"id": wid, "name": name or target[:40], "kind": (kind or "http").lower(), "target": target,
              "interval": max(60, int(interval or 600)), "grep": grep or "", "notify": notify or "change",
              "then": then or "", "chat": int(chat or 0), "timeout": int(timeout or 30),
              "enabled": True, "created": int(time.time()), "last_ts": 0,
              "last_hash": "", "last_val": "", "runs": 0, "changes": 0}
    save(d)
    return wid


def delete(wid):
    d = load()
    if wid in d:
        del d[wid]
        save(d)
        return True
    return False


def toggle(wid, on):
    d = load()
    if wid in d:
        d[wid]["enabled"] = bool(on)
        save(d)
        return True
    return False


def _check(w):
    """执行一次检查 → (值文本, 错误)"""
    k = w.get("kind")
    tgt = w.get("target") or ""
    to = int(w.get("timeout") or 30)
    try:
        if k == "http":
            import urllib.request
            req = urllib.request.Request(tgt, headers={"User-Agent": "Mozilla/5.0 (watchdog)"})
            with urllib.request.urlopen(req, timeout=to) as r:
                body = r.read().decode("utf-8", "replace")
                code = r.status
            val = f"HTTP {code} len={len(body)}"
            g = w.get("grep") or ""
            if g:
                try:
                    hits = re.findall(g, body)
                    val += " | 命中: " + " ;; ".join(str(x)[:120] for x in hits[:8])
                except Exception as e:
                    val += f" | grep 正则错误: {e}"
            else:
                val += " | " + re.sub(r'\s+', ' ', body)[:600]
            return val, ""
        if k == "cmd":
            p = subprocess.run(tgt, shell=True, capture_output=True, text=True, timeout=to)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            g = w.get("grep") or ""
            if g:
                try:
                    out = "\n".join(l for l in out.split("\n") if re.search(g, l))
                except Exception:
                    pass
            return f"rc={p.returncode} | {out[:800]}", ""
        if k == "port":
            host, _, port = tgt.rpartition(":")
            if not host:
                host, port = tgt, "80"
            try:
                s = socket.create_connection((host, int(port)), timeout=min(to, 10))
                s.close()
                return f"{host}:{port} open", ""
            except Exception:
                return f"{host}:{port} closed/filtered", ""
        if k == "file":
            if not os.path.exists(tgt):
                return "文件不存在", ""
            st = os.stat(tgt)
            h = hashlib.sha256(open(tgt, "rb").read(2 * 1024 * 1024)).hexdigest()[:16]
            return f"size={st.st_size} mtime={int(st.st_mtime)} sha={h}", ""
        if k == "keyword":
            import urllib.request, urllib.parse
            q = urllib.parse.quote(tgt)
            req = urllib.request.Request(f"https://html.duckduckgo.com/html/?q={q}",
                                         headers={"User-Agent": "Mozilla/5.0 (watchdog)"})
            with urllib.request.urlopen(req, timeout=to) as r:
                body = r.read().decode("utf-8", "replace")
            links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
            items = [re.sub(r'<[^>]+>', '', t).strip()[:80] for _, t in links[:10]]
            return " | ".join(items) if items else "(无结果)", ""
        return "", f"未知 kind: {k}"
    except Exception as e:
        return "", f"{type(e).__name__}: {str(e)[:150]}"


def _hash(v):
    return hashlib.sha256(str(v).encode("utf-8", "ignore")).hexdigest()[:16]


def run_one(wid, force=False):
    """跑一个值守并比对; 返回 dict(结果) 供工具/命令复用"""
    d = load()
    w = d.get(wid)
    if not w:
        return {"ok": False, "err": "没这个值守任务"}
    val, err = _check(w)
    if err:
        # 2026-10-01: 连续失败要能被看见 —— 原来 run_due 只挑"有变化"的项, 失败项被静默丢弃,
        #   目标挂了/超时永远不通知, 值守就那么烂着。现在记 streak, 到阈值丢进告警队列(每个监控项只告警一轮)。
        _st = int(d[wid].get("fail_streak") or 0) + 1
        d[wid]["fail_streak"] = _st
        d[wid]["last_err"] = str(err)[:200]
        if force:
            return {"ok": False, "err": err, "name": w.get("name")}
        d[wid]["last_ts"] = int(time.time())
        d[wid]["runs"] = int(d[wid].get("runs") or 0) + 1
        if _st >= _FAIL_ALERT_AT and not d[wid].get("fail_alerted"):
            d[wid]["fail_alerted"] = True
            _FAILQ.append({"wid": wid, "name": w.get("name"), "chat": w.get("chat"),
                           "streak": _st, "err": str(err)[:200]})
        save(d)
        return {"ok": False, "err": err, "name": w.get("name")}
    d[wid]["fail_streak"] = 0        # 成功一次 → 失败计数与告警标记复位
    d[wid]["fail_alerted"] = False
    h = _hash(val)
    old = w.get("last_hash") or ""
    changed = bool(old) and old != h
    d[wid]["last_hash"] = h
    d[wid]["last_val"] = val[:1500]
    d[wid]["last_ts"] = int(time.time())
    d[wid]["runs"] = int(d[wid].get("runs") or 0) + 1
    if changed:
        d[wid]["changes"] = int(d[wid].get("changes") or 0) + 1
    save(d)
    return {"ok": True, "changed": changed, "first": not old, "val": val[:1500],
            "prev": (w.get("last_val") or "")[:800], "name": w.get("name"), "chat": w.get("chat"),
            "then": w.get("then") or "", "wid": wid}


def drain_fails():
    """取走待告警的"连续失败"记录(bot 线程用)"""
    global _FAILQ
    _out, _FAILQ = _FAILQ, []
    return _out


def deep_allowed(wid, cooldown=600):
    """2026-10-01: 自动深挖冷却 —— 同一监控项 cooldown 秒内只允许触发一次。
    原来没有任何节流: 目标内容只要一直抖, 每 60s 就会起一整轮 LLM+工具深挖, 烧 token 且刷屏。"""
    d = load()
    w = d.get(wid)
    if not w:
        return False
    _now = int(time.time())
    _last = int(w.get("last_deep_ts") or 0)
    if _now - _last < int(cooldown):
        return False
    w["last_deep_ts"] = _now
    d[wid] = w
    save(d)
    return True


def run_due(max_n=3):
    """跑所有到点的值守(供 bot 后台线程调用); 返回发生变化的列表"""
    d = load()
    now = time.time()
    due = [k for k, w in d.items() if w.get("enabled") and now - float(w.get("last_ts") or 0) >= float(w.get("interval") or 600)]
    out = []
    for wid in due[:max_n]:
        try:
            r = run_one(wid)
            if r.get("ok") and r.get("changed"):
                out.append(r)
        except Exception as e:
            print(f"[watch] {wid} 异常: {str(e)[:120]}", flush=True)
    return out


def fmt_list():
    d = load()
    if not d:
        return "（还没有值守任务）用 watch op=add 建一个"
    lines = []
    for k, w in sorted(d.items()):
        lt = w.get("last_ts") or 0
        ago = f"{int((time.time()-lt)//60)}分钟前" if lt else "从未跑"
        lines.append(f"· {k} {'✅' if w.get('enabled') else '⏸'} {w.get('name')} | {w.get('kind')} → {str(w.get('target'))[:60]} | "
                     f"每{int(w.get('interval') or 600)//60}分 | 跑{int(w.get('runs') or 0)}次/变{int(w.get('changes') or 0)}次 | 上次{ago}"
                     + (f" | then={w.get('then')}" if w.get('then') else ""))
        if w.get("last_val"):
            lines.append(f"    最近值: {str(w['last_val'])[:160]}")
    return "\n".join(lines)
