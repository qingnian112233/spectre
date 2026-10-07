# -*- coding: utf-8 -*-
"""
AtkMeta: 跨目标经验蒸馏/回灌(dedicated from StrikeAgent AtkBrain 机制, 重写实现)
- 制胜路径去特化: 剥离 IP/端口/具体路径, 只留 类型(战术) 骨架链
- 剧本存储/回灌: winning_chains.json 按 target_fp 分桶, confidence 强化
- 证据门槛: 无证据发现不得计入制胜路径(由调用方提示词强制)
"""
import json
import os
import re
import time

STORE_PATH = "/opt/deepseek-bot/knowledge/meta/winning_chains.json"

# 类型前缀归一
_TYPE_MAP = {
    "target": "entry", "host": "entry", "ip": "entry", "entry": "entry",
    "svc": "service", "service": "service", "port": "service",
    "vuln": "vuln", "weakness": "vuln",
    "cred": "cred", "credential": "cred", "creds": "cred",
    "foothold": "foothold", "shell": "foothold",
    "goal": "goal", "flag": "goal", "info": "info",
}

# 战术词表(去 IP/题面 slug 后能出现的战术标注)
_TACTIC_KWS = [
    ("command-injection", "command_injection"), ("cmdi", "command_injection"),
    ("cmd-inject", "command_injection"), ("os-command", "command_injection"),
    ("cmd", "command_injection"), ("injection", "command_injection"),
    ("deserial", "deserialization"), ("unserialize", "deserialization"),
    ("pickle", "deserialization"),
    ("remote-code", "rce"), ("code-exec", "rce"), ("rce", "rce"), ("exec", "rce"),
    ("ssti", "ssti"), ("template", "ssti"), ("jinja", "ssti"), ("tpl", "ssti"),
    ("sql-injection", "sqli"), ("sqlinjection", "sqli"), ("sqli", "sqli"),
    ("no-sql", "nosqli"), ("nosql", "nosqli"),
    ("path-traversal", "lfi"), ("traversal", "lfi"), ("file-read", "lfi"),
    ("file-include", "lfi"), ("lfi", "lfi"), ("arbitrary-file", "lfi"),
    ("remote-file", "rfi"), ("rfi", "rfi"),
    ("file-write", "upload"), ("arbitrary-upload", "upload"), ("upload", "upload"),
    ("server-side-request", "ssrf"), ("ssrf", "ssrf"),
    ("xml-external", "xxe"), ("xxe", "xxe"),
    ("xss", "xss"), ("csrf", "csrf"),
    ("insecure-direct", "idor"), ("idor", "idor"),
    ("auth", "auth_bypass"), ("login", "auth_bypass"), ("bypass", "auth_bypass"), ("noauth", "auth_bypass"),
    ("jwt", "jwt"),
    ("secret-key", "secret_key"), ("secret", "secret_key"),
    ("privilege-escalation", "privesc"), ("privesc", "privesc"), ("escalat", "privesc"),
    ("auth-bypass", "auth_bypass"), ("bypass-auth", "auth_bypass"), ("auth", "auth_bypass"),
    ("unauth", "unauth"), ("no-auth", "unauth"),
    ("info-disclosure", "info_disclosure"), ("disclosure", "info_disclosure"),
    ("info-leak", "info_disclosure"), ("infoleak", "info_disclosure"),
    ("weak-password", "credential"), ("default-pass", "credential"), ("password", "credential"),
    ("debug", "debug"),
]

_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_PORT_RE = re.compile(r":\d{2,5}\b")
_HOST_RE = re.compile(r"\b([a-z0-9-]+\.)+[a-z]{2,}\b", re.I)

SCOPE_RE = re.compile(
    r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d{2,5})?|"
    r"((?:\d{1,3}\.){3}\d{1,3})(?::\d{2,5})?",
    re.I,
)


def extract_scope(text: str) -> set[str]:
    """从任务描述提取目标 scope(域名/IP), 供防打歪锁定。"""
    out = set()
    for m in SCOPE_RE.finditer(text or ""):
        v = (m.group(1) or m.group(2) or "").strip().lower()
        if v:
            out.add(v)
    return out


def _match_tactic(ident: str) -> str | None:
    s = (ident or "").lower()
    for kw, tac in _TACTIC_KWS:
        if kw in s:
            return tac
    return None


_PROTOS = ("http", "https", "ftp", "ssh", "smb", "redis", "mysql", "mssql",
           "mongodb", "postgres", "ldap", "smtp", "rdp", "telnet", "dns",
           "graphql", "api", "ws", "wss", "grpc", "vnc")


def generalize_node_key(key: str) -> str:
    """节点key归一: `vuln:path-traversal` → `vuln(lfi)`, `svc:80/http` → `service(http)`。"""
    raw = _IP_RE.sub("", (key or "").strip().lower())
    if not raw:
        return ""
    if ":" in raw:
        typ, ident = raw.split(":", 1)
    else:
        typ, ident = "", raw
    # 支持规范化段形式 type(tactic) / type:tactic / 裸类型词
    _mb = re.match(r"^([a-z0-9_-]+)\(([^()]*)\)$", ident)
    if _mb and not typ:
        typ, ident = _mb.group(1), _mb.group(2)
    elif not typ:
        _bare = _TYPE_MAP.get(ident.strip())
        if _bare:
            typ, ident = ident.strip(), ""
    # 剥离 ident 里的端口前缀与路径脏字符
    ident = re.sub(r"^\d{1,5}(?=/|$)", "", ident).strip("/.")
    if not typ and not ident:
        return ""
    ctype = _TYPE_MAP.get(typ.strip(), (typ.strip() or "node"))
    if ctype == "entry":
        return "entry"
    if ctype == "goal":
        return "goal"
    if ctype == "service":
        for tok in re.split(r"[^a-z0-9]+", ident):
            if tok in _PROTOS:
                return f"service({tok})"
        return "service"
    tac = _match_tactic(f"{typ} {ident}")
    return f"{ctype}({tac})" if tac else ctype


def normalize_chain(raw: str, sep: str | None = None) -> list[str]:
    """原始制胜路径 → 去特化类型战术链(连续去重)。
    支持 `a -> b`、`a → b`、`a >> b`、逗号分隔、列表输入。
    """
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
    else:
        s = str(raw or "").strip()
        if sep:
            parts = [x.strip() for x in s.split(sep) if x.strip()]
        else:
            _arrow = re.split(r"\s*(?:->>|->|→)\s*", s)
            if len(_arrow) > 1:
                parts = _arrow
            elif "," in s:
                parts = [x.strip() for x in s.split(",") if x.strip()]
            else:
                parts = [s]
    chain = []
    for p in parts:
        g = generalize_node_key(p)
        if not g:
            continue
        if chain and chain[-1] == g:
            continue
        chain.append(g)
    # 至少要有 应用链价值(含 vuln/cred/foothold 战术标注) — 2026-09-11 放宽: 节点本身是 vuln/cred/foothold 也算
    # (原先要求节点必须带括号战术标注, 导致 `entry -> foothold`、`cred -> goal` 这类合法短链被整条丢弃)
    if not any(("(" in c) or c.split("(")[0].strip() in ("vuln", "cred", "foothold", "goal") for c in chain):
        return []
    if set(chain) <= {"entry", "service", "info", "goal"}:
        return []
    return chain


def load_store() -> dict:
    try:
        if os.path.exists(STORE_PATH):
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def save_lesson(raw_chain: str, target_fp: str = "*", evidence=("shell", "flag", "poc")) -> dict | None:
    """蒸馏一条制胜链进剧本库(去特化; 同类合并, confidence 强化)。"""
    chain = normalize_chain(raw_chain)
    if not chain:
        return None
    store = load_store()
    key = " → ".join(chain)
    fp = (target_fp or "*").strip().lower()
    bucket = store.setdefault(fp, [])
    for it in bucket:
        if it.get("chain") == key:
            it["wins"] = int(it.get("wins") or 0) + 1
            it["uses"] = int(it.get("uses") or 0) + 1
            it["conf"] = min(0.95, 0.4 + 0.12 * it["wins"] - 0.05 * int(it.get("fails") or 0))
            it["last"] = int(time.time())
            store["_updated"] = int(time.time())
            _write_store(store)
            return it
    item = {"chain": key, "wins": 1, "fails": 0, "uses": 1,
            "conf": 0.52, "last": int(time.time()), "fp": fp}
    bucket.append(item)
    store["_updated"] = int(time.time())
    _write_store(store)
    return item


def _write_store(store: dict):
    try:
        os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def save_fail_chain(raw_chain: str, target_fp: str = "*") -> dict | None:
    """蒸馏一条失败链进剧本库(losing 桶, 回灌时作 avoid 提示)。"""
    chain = normalize_chain(raw_chain)
    if not chain:
        return None
    store = load_store()
    key = " → ".join(chain)
    fp = (target_fp or "*").strip().lower()
    bucket = store.setdefault("losing:" + fp, [])
    for it in bucket:
        if it.get("chain") == key:
            it["fails"] = int(it.get("fails") or 0) + 1
            it["uses"] = int(it.get("uses") or 0) + 1
            it["last"] = int(time.time())
            _write_store(store)
            return it
    item = {"chain": key, "wins": 0, "fails": 1, "uses": 1, "last": int(time.time()), "fp": fp}
    bucket.append(item)
    _write_store(store)
    return item


def retrieve_avoid(signals: set[str], limit: int = 2) -> list[str]:
    """回灌 avoid: 失败链按信号匹配, 返回「避免: xxx」提示。"""
    store = load_store()
    scored = []
    for k, bucket in store.items():
        if not k.startswith("losing:") or not isinstance(bucket, list):
            continue
        for it in bucket:
            chain = str(it.get("chain") or "")
            if not chain:
                continue
            conf = float(it.get("conf") or 0.3) + 0.08 * int(it.get("fails") or 0)
            hit = sum(1 for s in signals if s.lower() in chain.lower())
            if hit:
                scored.append((conf + 0.1 * hit, chain, it.get("fails", 0)))
    scored.sort(reverse=True)
    return [f"避免: {c}(已失败{f}次)" for _, c, f in scored[:limit]]


def retrieve_lessons(signals: set[str], limit: int = 3) -> list[str]:
    """回灌: 按当次信号(目标scope/战术词)评分, 返回topN 链文本。"""
    store = load_store()
    scored = []
    for k, bucket in store.items():
        if k.startswith("_") or not isinstance(bucket, list):
            continue
        for it in bucket:
            chain = str(it.get("chain") or "")
            if not chain:
                continue
            conf = float(it.get("conf") or 0.3) * (1 + 0.15 * int(it.get("wins") or 0))
            # 信号匹配: 链中战术词命中 signals
            hit = sum(1 for s in signals if s.lower() in chain.lower())
            if hit:
                scored.append((conf + 0.1 * hit, conf, chain, it.get("wins", 0)))
    scored.sort(reverse=True)
    return [f"{c}(赢{w}次)" for _, c, ch, w in scored[:limit]]
