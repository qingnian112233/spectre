#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本鱼事实库 (factdb) —— 抄自 Jueze-2019/dsh-redteam-mode 的 SQLite 事实库层。

思路（原项目 core.js / schema.js / score-rules.js 的移植）:
  红队最怕的不是打不动，是"重复打、记不住、报告没证据"。
  所以把每一次发现都落进本机 SQLite 事实库，跨会话/跨子代理共享，
  报告直接从库里回放，不靠模型记忆转述。

作者: SPECTRE (青念 @eexse)
用法: python3 factdb.py '<JSON参数>'
"""
import sys, json, os, sqlite3, time, hashlib, re
from datetime import datetime, timezone, timedelta

HOME = os.environ.get("DSH_HOME") or "/opt/deepseek-bot"
ROOT = os.path.join(HOME, "knowledge", "redteam-work")
DB = os.path.join(ROOT, "factdb.sqlite")
CST = timezone(timedelta(hours=8))


def now():
    return datetime.now(CST).isoformat(timespec="seconds")


DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS engagement(
  id TEXT PRIMARY KEY, target TEXT, scope TEXT, started_at TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS asset(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, ip TEXT NOT NULL, port INTEGER, service TEXT, product TEXT, version TEXT,
  url TEXT, title TEXT, names TEXT, fingerprint TEXT,
  state TEXT DEFAULT 'unknown', priority TEXT, potential TEXT, assess_reason TEXT,
  test_status TEXT DEFAULT 'untested', test_notes TEXT, test_surface TEXT, blocked_count INTEGER DEFAULT 0,
  provenance TEXT, tool TEXT, agent TEXT,
  first_seen TEXT, last_seen TEXT, discovered_at TEXT,
  UNIQUE(eng, ip, port, service));
CREATE TABLE IF NOT EXISTS vuln(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, asset_id INTEGER, target TEXT, name TEXT NOT NULL, cve TEXT, component TEXT,
  versions TEXT, severity TEXT, status TEXT DEFAULT 'candidate',
  evidence TEXT, poc TEXT, command TEXT, agent TEXT, found_at TEXT, verified_by TEXT);
CREATE TABLE IF NOT EXISTS credential(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, target TEXT, kind TEXT, username TEXT, secret TEXT, source TEXT,
  used INTEGER DEFAULT 0, agent TEXT, found_at TEXT);
CREATE TABLE IF NOT EXISTS session_tunnel(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, kind TEXT, target TEXT, entry_kind TEXT, entry TEXT, command TEXT,
  status TEXT DEFAULT 'up', agent TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS step(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, asset_id INTEGER, target TEXT, agent TEXT, tool TEXT, command TEXT,
  output TEXT, result TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS finding(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  eng TEXT, stage TEXT, target TEXT, point_code TEXT, tier TEXT,
  points INTEGER, evidence TEXT, confirm TEXT DEFAULT 'pending',
  agent TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS repchain(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id INTEGER, step_id INTEGER, command TEXT, output TEXT,
  repro_status TEXT DEFAULT 'unknown', note TEXT);
CREATE INDEX IF NOT EXISTS ix_asset_eng ON asset(eng, test_status);
CREATE INDEX IF NOT EXISTS ix_vuln_target ON vuln(target, status);
CREATE INDEX IF NOT EXISTS ix_finding_eng ON finding(eng, point_code);
CREATE INDEX IF NOT EXISTS ix_step_target ON step(target);
"""

# ── 计分口径（抄自 docs/得分规则-合并版.md，G1-G8 通用规则）──────────────────
GEN_RULES = [
    ("G1", "权限取高、只计一次", "同一系统/主机/数据库取得多种权限只按最高权限计一次分"),
    ("G2", "数据成果另行计分", "邮件/业务/数据资产等成果按重要程度单独计分，不与权限分混算"),
    ("G3", "上限口径", "各项上限针对单个防守单位及其所有下属机构，达到上限不再累计"),
    ("G4", "设备按台(个)计分", "打印机/wifi 5分每台；PC/Pad/手机/摄像头/大屏 10分每台；云主机/容器/物联网连接点 10分每个"),
    ("G5", "数据规模翻倍", "重要数据超1亿条或10TB得分翻倍；控制知识库相关系统翻倍；不含普通日志"),
    ("G6", "IPv6倍数", "IPv6成果分×3，但上限仍按原上限"),
    ("G7", "证明材料", "突破边界须有隔离设备控制截图/能访问内网截图；网络设备需路由表或连接量证据"),
    ("G8", "兜底条款", "其他系统/服务器/设备权限不预设分值，由专项组研判给分"),
]

# sp(rule, tier, code, name, group, points, cap, dedup_scope, note)
def sp(rule, tier, code, name, group, points, cap, scope, note):
    return dict(rule=rule, tier=tier, code=code, name=name, group=group,
                points=points, cap=cap, dedup_scope=scope, note=note)

GROUPS = {
    "GENERAL": "控制一般系统", "WEB": "控制Web应用系统", "CENTRAL": "控制集权系统",
    "BIGDATA": "控制大数据系统", "NETINFRA": "控制网络基础设施", "STORAGE": "文件存储类系统",
    "MODEL": "控制模型相关系统", "BOUNDARY": "突破网络边界",
}

POINTS = [
    sp(1, "一级50/二级20分每个", "domain-control", "域名控制权限", "GENERAL", 50, 400, "system", "一级域名50分/个，二级20分/个"),
    sp(2, "5/10分每台", "terminal-access", "终端权限(手机/Pad/PC/打印机/摄像头/wifi)", "GENERAL", 10, 600, "none", "打印机/wifi 5分每台，其余10分每台(G4)"),
    sp(3, "普通10/管理员50分每个", "server-host", "服务器主机权限(含WebShell)", "GENERAL", 10, 600, "system", "管理员权限50分/个，普通10分/个"),
    sp(4, "普通10/管理员50分每个", "db-credential", "数据库连接账号密码(含SQL注入)", "GENERAL", 10, 400, "service", "redis空密码/未授权统一10分/个；同库同等权限只计一次"),
    sp(5, "普通50/管理员100分每个", "web-app", "邮箱系统 / OA与业务生产系统权限", "WEB", 50, 2000, "system", "管理员100分/个，普通50分/个；同系统只计最高一次"),
    sp(7, "管理员500/普通50分每个", "central-system", "集权系统(堡垒机/域控/SSO/4A/IAM/终端管理后台)", "CENTRAL", 500, 4000, "system", "批量控制需证明登录托管节点20%以上(最多20台)"),
    sp(8, "管理员1000/普通100分每个", "bigdata-system", "大数据系统权限", "BIGDATA", 1000, 4000, "system", "有效数据<5亿条或1TB按普通数据库计"),
    sp(9, "普通100/管理员200分", "netdev", "网络设备权限(防火墙/路由器/交换机/网闸/VPN)", "NETINFRA", 200, 2000, "system", "重定向或劫持+200；植入远控后成功后续攻击1000"),
    sp(10, "管理员200分", "iiot", "工业互联网系统权限", "NETINFRA", 200, 2000, "system", "托管互联设备10分/个"),
    sp(11, "管理员500分", "cloud-platform", "云管理平台控制权(含K8S/OpenShift等PaaS)", "NETINFRA", 500, 2000, "system", "云主机/容器10分每台；节点<100按普通Web应用计分"),
    sp(12, "平台200分", "iot-platform", "物联网设备管控平台权限", "NETINFRA", 200, 2000, "system", "连接点10分/台；端点打入核心网控业务系统另加5000"),
    sp(13, "管理员200分", "secdev", "安全设备权限(IPS/IDS/审计/WAF等非集权类)", "NETINFRA", 200, 1000, "system", ""),
    sp(14, "管理员50分每个", "file-storage", "文件存储类系统后台权限(FTP/对象存储/NAS/网盘)", "STORAGE", 50, 500, "system", "普通权限或仅测试数据10分/个"),
    sp(15, "100-500分每个", "ai-agent", "控制模型智能体/skill等agent工具并操作其攻击", "MODEL", 100, 4000, "system", "区间分，按控制与操作深度研判"),
    sp(16, "管理员500分每个", "model-compute", "算力管理平台 / 训练数据与知识库系统", "MODEL", 500, 4000, "system", "超大数据规模或知识库相关系统翻倍(G5)"),
    sp(18, "管理员500分每个", "model-data", "模型相关数据系统(权重/训练数据篡改)", "MODEL", 500, 4000, "system", "超1亿条或10TB翻倍(G5)；数据资产另按G2计"),
    sp(19, "管理员500分每个 + 10分每卡", "computepower", "算力基础设施(管理员/算力卡/池化平台)", "MODEL", 500, 2000, "system", "三项可分别得分但受本条总上限约束"),
    sp(22, "1000分", "boundary-logical", "进入逻辑隔离业务内网", "BOUNDARY", 1000, 1000, "target", "整单位同类边界只计一次"),
    sp(23, "10000分", "boundary-strong", "进入逻辑强隔离业务内网", "BOUNDARY", 10000, 10000, "target", "需网闸类隔离设备控制截图"),
    sp(24, "30000分", "boundary-physical", "进入物理隔离内网/核心生产网", "BOUNDARY", 30000, 30000, "target", "需防火墙/VPN/多网卡/网闸控制截图"),
    sp(25, "1000分每家", "boundary-supply", "借供应链运维通道或云服务进入主防内网", "BOUNDARY", 1000, 3000, "target", "多家供应链进一家不同区域每区1000最多3000"),
]
PBY = {p["code"]: p for p in POINTS}
DEPRECATED = {  # 旧 code 自动改派（原项目同款）
    "webshell": "server-host", "rce": "server-host", "server-shell": "server-host",
    "db-access": "db-credential", "sensitive-data": "model-data",
    "web-account-user": "web-app", "web-account-admin": "web-app",
    "core-system": "central-system", "internal-pivot": "boundary-logical",
    "boundary": "boundary-logical",
}


def conn():
    os.makedirs(ROOT, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    return c


def out(o):
    print(json.dumps(o, ensure_ascii=False, indent=1))


# ── 报告复现（移植 report-replay.js：curl 解析 → 合成可重放报文）────────────
# 两条底线抄原项目：
#   ① 合成的东西必须标注来源（synthesized=True）—— 推断的请求与真实抓包不是一回事，
#      报告里要能一眼分辨，不能让验收人把推断当实证；
#   ② 只在"信息足够"时合成（有 URL 或有 curl 命令），否则宁可留空给补录指引，不编造。
_SCANNERS = "nuclei|ffuf|feroxbuster|gobuster|dirsearch|sqlmap|hydra|nmap|masscan|fscan|gogo"


def command_kind(tool):
    t = (tool or "").strip()
    if t == "":
        return None
    if re.match(r"^curl\b", t, re.I):
        return "curl"
    if re.match(r"^http(ie| x)?\b|^http\s", t, re.I):
        return "httpie"
    if re.match(r"^(%s)\b" % _SCANNERS, t, re.I):
        return "scanner"
    if re.match(r"^(msfconsole|use\s|set\s)", t, re.I):
        return "msf"
    if re.match(r"^(python|python3|java|go run|node)\b", t, re.I):
        return "script"
    return "other"


def parse_target(target):
    """解析 scheme/host/port/path。IPv6 方括号写法必须显式处理：
    [2001:db8::1]:8080 里"地址内部冒号"与"端口冒号"混在一起，一条正则一起抓会错位
    （原项目实测把 host 抓成 2001:db8:、path 变成 :8080/x）。先取方括号 authority，再剥最后一个冒号。"""
    t = (target or "").strip()
    if t == "":
        return None
    m = re.match(r"^([a-z][a-z0-9+.-]*)://([^/?#\s]+)([^?#\s]*)?", t, re.I)
    if m:
        scheme, authority = m.group(1).lower(), m.group(2)
        path = m.group(3) or "/"
        b = re.match(r"^\[([^\]]+)\](?::(\d{1,5}))?$", authority)
        if b:
            return dict(scheme=scheme, host=b.group(1), path=path, authority=authority, ipv6=True,
                        port=int(b.group(2)) if b.group(2) else (443 if scheme == "https" else 80))
        pm = re.search(r":(\d{1,5})$", authority)
        host = authority[:pm.start()] if pm else authority
        return dict(scheme=scheme, host=host, path=path, authority=authority, ipv6=False,
                    port=int(pm.group(1)) if pm else (443 if scheme == "https" else 80))
    b = re.match(r"^\[([^\]]+)\](?::(\d{1,5}))?$", t)
    if b:
        return dict(scheme=None, host=b.group(1), port=int(b.group(2)) if b.group(2) else None,
                    path=None, authority=t, ipv6=True)
    bare = re.match(r"^([^\s/?#:]+)(?::(\d{1,5}))?$", t)
    if bare:
        return dict(scheme=None, host=bare.group(1), port=int(bare.group(2)) if bare.group(2) else None,
                    path=None, authority=t, ipv6=False)
    head = re.match(r"^([^\s/?#]+)", t)
    if not head:
        return None
    return dict(scheme=None, host=head.group(1), port=None, path=None, authority=t, ipv6=False)


def parse_curl(tool):
    """从 curl 命令里抽 URL/方法/数据/头/cookie。只做保守解析，抽不出就 None，不猜。"""
    t = (tool or "").strip()
    if not re.match(r"^curl\b", t, re.I):
        return None
    um = re.search(r"(https?://[^\s'\"]+)", t, re.I)
    if not um:
        return None
    mm = re.search(r"(?:-X|--request)\s+([A-Z]+)", t, re.I)
    dm = re.search(r"""(?:-d|--data(?:-raw|-binary|-urlencode)?)\s+(?:'([^']*)'|"([^"]*)"|(\S+))""", t)
    data = (dm.group(1) or dm.group(2) or dm.group(3)) if dm else None
    headers = [m.group(1) if m.group(1) is not None else m.group(2)
               for m in re.finditer(r"""(?:-H|--header)\s+(?:'([^']*)'|"([^"]*)")""", t, re.I)]
    headers = [h for h in headers if h and ":" in h]
    cm = re.search(r"""(?:-b|--cookie)\s+(?:'([^']*)'|"([^"]*)"|(\S+))""", t)
    cookie = (cm.group(1) or cm.group(2) or cm.group(3)) if cm else None
    method = mm.group(1).upper() if mm else ("GET" if data is None else "POST")
    return dict(url=um.group(1), method=method, data=data, headers=headers, cookie=cookie,
                insecure=bool(re.search(r"(^|\s)-k(\s|$)|--insecure", t)),
                follow_redirect=bool(re.search(r"(^|\s)-L(\s|$)|--location", t)))


def build_http_request(o):
    """合成一条可粘进 Yakit Repeater 的 HTTP 报文。信息不足返回 None。"""
    tg = parse_target(o.get("url"))
    if tg is None:
        return None
    scheme = tg["scheme"] or "http"
    host_text = "[%s]" % tg["host"] if tg["ipv6"] else tg["host"]   # IPv6 的 Host 头必须带方括号
    default_port = 443 if scheme == "https" else 80
    host_header = "%s:%d" % (host_text, tg["port"]) if (tg["port"] and tg["port"] != default_port) else host_text
    path = o.get("path") or tg["path"] or "/"
    data = o.get("data")
    method = (o.get("method") or ("GET" if data is None else "POST")).upper()
    headers = list(o.get("headers") or [])
    low = lambda n: any(str(h).lower().startswith(n.lower() + ":") for h in headers)
    if not low("User-Agent"):
        headers.append("User-Agent: Mozilla/5.0")
    if not low("Accept"):
        headers.append("Accept: */*")
    if o.get("cookie") and not low("Cookie"):
        headers.append("Cookie: " + o["cookie"])
    body = "" if data is None else str(data)
    if body and not low("Content-Type"):
        headers.append("Content-Type: application/json" if re.match(r"^\{.*\}$", body.strip())
                       else "Content-Type: application/x-www-form-urlencoded")
    if body and not low("Content-Length"):
        headers.append("Content-Length: %d" % len(body.encode("utf-8")))
    if not low("Connection"):
        headers.append("Connection: close")
    return "%s %s HTTP/1.1\r\n%s\r\n\r\n%s" % (method, path, "\r\n".join(["Host: " + host_header] + headers), body)


def build_curl_command(o):
    """合成一条终端可直接跑的复现命令。真实 curl 命令原样保留（最可信）。"""
    raw = (o.get("tool") or "").strip()
    if command_kind(raw) == "curl":
        return raw
    tg = parse_target(o.get("url"))
    if tg is None:
        return None
    scheme = tg["scheme"] or "http"
    host_text = "[%s]" % tg["host"] if tg["ipv6"] else tg["host"]
    default_port = 443 if scheme == "https" else 80
    host_part = "%s:%d" % (host_text, tg["port"]) if (tg["port"] and tg["port"] != default_port) else host_text
    url = "%s://%s%s" % (scheme, host_part, o.get("path") or tg["path"] or "/")
    parts = ["curl -i -s" + (" -k" if o.get("insecure") else "") + (" -L" if o.get("follow_redirect") else "")]
    if o.get("method") and str(o["method"]).upper() != "GET":
        parts.append("-X " + str(o["method"]).upper())
    if o.get("cookie"):
        parts.append("-b '%s'" % o["cookie"])
    if o.get("data"):
        parts.append("-d '%s'" % str(o["data"]).replace("'", "'\\''"))
    parts.append("'%s'" % url)
    return " ".join(parts)


# ── 计分（applyScoreCaps 移植：取高只计一次 + 规则上限）────────────────────
def evaluate(findings):
    """findings: [{code|point_code, target, points?, evidence, confirm?}] → 计分明细 + 总分

    code 与 point_code 两种键名都收：finding_add 入参用 point_code（与工具 schema 一致），
    库里读出来的行是 point_code 列名，历史调用可能写 code —— 任一都能喂进来。
    """
    rows = []
    for f in findings:
        code = f.get("code") or f.get("point_code") or ""
        warn = None
        if code in DEPRECATED:
            warn = f"旧code {code} 已废弃，自动改派为 {DEPRECATED[code]}"
            code = DEPRECATED[code]
        p = PBY.get(code)
        if p is None:
            rows.append(dict(code=code, counted=False, warn=f"未知得分点 code={code}"))
            continue
        pts = f.get("points") if f.get("points") is not None else p["points"]
        rows.append(dict(code=code, name=p["name"], rule=p["rule"], target=f.get("target"),
                         points=pts, cap=p["cap"], dedup_scope=p["dedup_scope"],
                         evidence=f.get("evidence", ""), confirm=f.get("confirm", "pending"),
                         counted=True, warn=warn))
    # G2: 同一 rule 下 同 dedup_scope 键 只按最高分计一次
    best = {}      # (rule, scope_key) -> index
    for i, r in enumerate(rows):
        if not r.get("counted"):
            continue
        sc = r["dedup_scope"]
        if sc == "none":
            continue
        key = (r["rule"], sc, str(r["target"]))
        j = best.get(key)
        if j is None:
            best[key] = i
        elif r["points"] > rows[j]["points"]:
            rows[j]["counted"] = False
            rows[j]["dedup"] = f"被更高权限顶掉(同{sc}只计最高)"
            best[key] = i
        else:
            r["counted"] = False
            r["dedup"] = f"同{sc}已计更高分，本条不重复计分(G1)"
    # 规则上限
    used = {}
    total = 0
    for r in rows:
        if not r.get("counted"):
            continue
        cap = r["cap"] or 0
        if cap > 0:
            u = used.get(r["rule"], 0)
            if u + r["points"] > cap:
                r["points"] = max(0, cap - u)
                r["capped"] = f"规则{r['rule']}已达上限{cap}，本条只计{max(0, cap - u)}分"
                if r["points"] == 0:
                    r["counted"] = False
            used[r["rule"]] = u + r["points"]
        total += r["points"]
    return dict(total=total, items=rows,
                by_group={g: sum(r["points"] for r in rows if r.get("counted") and PBY.get(r["code"], {}).get("group") == g)
                          for g in GROUPS})


# ── actions ────────────────────────────────────────────────────────────────
def main():
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    op = a.get("op", "stats")
    c = conn()

    if op == "init":
        c.execute("INSERT OR REPLACE INTO engagement(id,target,scope,started_at,notes) VALUES(?,?,?,?,?)",
                  (a["eng"], a.get("target", ""), a.get("scope", ""), now(), a.get("notes", "")))
        c.commit()
        eng = dict(c.execute("SELECT * FROM engagement WHERE id=?", (a["eng"],)).fetchone())
        out({"ok": True, "db": DB, "engagement": eng})

    elif op == "asset_add":
        c.execute("""INSERT INTO asset(eng,ip,port,service,product,version,url,title,names,fingerprint,
                     state,priority,potential,assess_reason,test_status,provenance,tool,agent,first_seen,last_seen,discovered_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(eng,ip,port,service) DO UPDATE SET
                       product=excluded.product, version=excluded.version, url=excluded.url,
                       title=excluded.title, fingerprint=excluded.fingerprint, last_seen=excluded.last_seen""",
                  (a.get("eng"), a["ip"], a.get("port"), a.get("service", ""), a.get("product", ""),
                   a.get("version", ""), a.get("url", ""), a.get("title", ""), a.get("names", ""),
                   a.get("fingerprint", ""), a.get("state", "unknown"), a.get("priority", ""),
                   a.get("potential", ""), a.get("assess_reason", ""), a.get("test_status", "untested"),
                   a.get("provenance", ""), a.get("tool", ""), a.get("agent", ""), now(), now(), now()))
        c.commit()
        out({"ok": True, "asset_id": c.execute("SELECT id FROM asset WHERE eng=? AND ip=? AND IFNULL(port,-1)=IFNULL(?,-1) AND service=?",
                                               (a.get("eng"), a["ip"], a.get("port"), a.get("service", ""))).fetchone()["id"]})

    elif op == "asset_test":
        c.execute("""UPDATE asset SET test_status=?, test_notes=COALESCE(test_notes,'')||char(10)||?,
                     test_surface=COALESCE(?,test_surface), blocked_count=blocked_count+(CASE WHEN ?='blocked' THEN 1 ELSE 0 END),
                     last_seen=? WHERE id=?""",
                  (a.get("status", "tested"), a.get("test", ""), a.get("surface"), a.get("status", ""), now(), a["asset_id"]))
        c.commit()
        out({"ok": True})

    elif op == "asset_query":
        q = "SELECT * FROM asset WHERE eng=? "
        p = [a.get("eng")]
        if a.get("sort") == "todo":
            q += "AND (priority IS NULL OR priority='') "
        if a.get("status"):
            q += "AND test_status=? "
            p.append(a["status"])
        q += "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, ip LIMIT ?"
        p.append(int(a.get("limit", 50)))
        rows = [dict(r) for r in c.execute(q, p).fetchall()]
        out({"count": len(rows), "rows": rows})

    elif op == "asset_get":
        out(dict(c.execute("SELECT * FROM asset WHERE id=?", (a["asset_id"],)).fetchone() or {}))

    elif op == "asset_stats":
        r = c.execute("""SELECT COUNT(*) n, SUM(test_status='untested') untested,
                         SUM(test_status='tested') tested, SUM(test_status='blocked') blocked,
                         SUM(priority='high') high FROM asset WHERE eng=?""", (a.get("eng"),)).fetchone()
        out(dict(r))

    elif op == "vuln_add":
        c.execute("""INSERT INTO vuln(eng,asset_id,target,name,cve,component,versions,severity,status,evidence,poc,command,agent,found_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (a.get("eng"), a.get("asset_id"), a.get("target"), a["name"], a.get("cve", ""),
                   a.get("component", ""), a.get("versions", ""), a.get("severity", "medium"),
                   a.get("status", "candidate"), a.get("evidence", ""), a.get("poc", ""),
                   a.get("command", ""), a.get("agent", ""), now()))
        c.commit()
        out({"ok": True, "vuln_id": c.execute("SELECT last_insert_rowid() id").fetchone()["id"]})

    elif op == "vuln_query":
        q = "SELECT * FROM vuln WHERE 1=1"
        p = []
        if a.get("target"):
            q += " AND target LIKE ?"; p.append(f"%{a['target']}%")
        if a.get("eng"):
            q += " AND eng=?"; p.append(a["eng"])
        if a.get("status"):
            q += " AND status=?"; p.append(a["status"])
        q += " ORDER BY id DESC LIMIT ?"; p.append(int(a.get("limit", 50)))
        out({"rows": [dict(r) for r in c.execute(q, p).fetchall()]})

    elif op in ("step_add",):
        c.execute("""INSERT INTO step(eng,asset_id,target,agent,tool,command,output,result,at)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (a.get("eng"), a.get("asset_id"), a.get("target"), a.get("agent", ""), a.get("tool", ""),
                   a.get("command", ""), a.get("output", ""), a.get("result", ""), now()))
        c.commit()
        out({"ok": True, "step_id": c.execute("SELECT last_insert_rowid() id").fetchone()["id"]})

    elif op == "credential_add":
        c.execute("""INSERT INTO credential(eng,target,kind,username,secret,source,agent,found_at)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (a.get("eng"), a.get("target"), a.get("kind", "password"), a.get("username", ""),
                   a.get("secret", ""), a.get("source", ""), a.get("agent", ""), now()))
        c.commit()
        out({"ok": True})

    elif op == "credential_list":
        q = "SELECT * FROM credential WHERE 1=1"; p = []
        if a.get("target"):
            q += " AND target LIKE ?"; p.append(f"%{a['target']}%")
        q += " ORDER BY id DESC LIMIT ?"; p.append(int(a.get("limit", 50)))
        out({"rows": [dict(r) for r in c.execute(q, p).fetchall()]})

    elif op == "session_add":
        c.execute("""INSERT INTO session_tunnel(eng,kind,target,entry_kind,entry,command,status,agent,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (a.get("eng"), a.get("kind", "tunnel"), a.get("target"), a.get("entry_kind", ""),
                   a.get("entry", ""), a.get("command", ""), a.get("status", "up"), a.get("agent", ""), now()))
        c.commit()
        out({"ok": True, "legit": a.get("entry_kind") != "self-only"})

    elif op == "sessions":
        out({"rows": [dict(r) for r in c.execute("SELECT * FROM session_tunnel ORDER BY id DESC LIMIT ?",
                                                 (int(a.get("limit", 50)),)).fetchall()]})

    elif op == "finding_add":
        r = evaluate([a])
        it = r["items"][0]
        if not it.get("counted") and not it.get("warn"):
            out({"ok": False, "reason": it.get("dedup", "未计分")})
            return
        c.execute("""INSERT INTO finding(eng,stage,target,point_code,tier,points,evidence,confirm,agent,at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (a.get("eng"), a.get("stage", ""), it.get("target"), it.get("code"), it.get("tier", ""),
                   it.get("points") or 0, a.get("evidence", ""), a.get("confirm", "pending"),
                   a.get("agent", ""), now()))
        c.commit()
        out({"ok": True, "counted": it.get("counted", False), "points": it.get("points"),
             "warn": it.get("warn") or it.get("dedup") or it.get("capped")})

    elif op == "score":
        rows = [dict(r) for r in c.execute("SELECT * FROM finding WHERE eng=?", (a.get("eng"),)).fetchall()]
        out(evaluate(rows))

    elif op == "score_rules":
        out({"general": [dict(code=c_, name=n, detail=d) for c_, n, d in GEN_RULES],
             "groups": GROUPS,
             "points": [dict(p, group_name=GROUPS[p["group"]]) for p in POINTS]})

    elif op == "recon":
        """复现链：报告里每条成果都要有可照做的命令与回显（抄 report-replay.js 的思路）
        优先用 finding 自带的 command/output；没有则回落到该靶标最近一条 step。"""
        rows = [dict(r) for r in c.execute(
            "SELECT id, target, point_code, points, evidence, confirm, agent FROM finding WHERE eng=? ORDER BY id",
            (a.get("eng"),)).fetchall()]
        for r in rows:
            s = c.execute("SELECT command, output FROM step WHERE eng=? ORDER BY id DESC LIMIT 1",
                          (a.get("eng"),)).fetchone()
            r["command"] = (s["command"] if s else "") or ""
            r["output"] = (s["output"] if s else "") or ""
            r["repro_status"] = "complete" if r["command"].strip() else "复现链不完整"
            if a.get("link_step"):  # 把最近一步与该 finding 显式关联，固化证据
                sid = s and c.execute("SELECT id FROM step WHERE eng=? ORDER BY id DESC LIMIT 1",
                                      (a.get("eng"),)).fetchone()
                if sid:
                    c.execute("INSERT INTO repchain(finding_id,step_id,command,output,repro_status) VALUES(?,?,?,?,?)",
                              (r["id"], sid["id"], r["command"], r["output"], r["repro_status"]))
        if a.get("link_step"):
            c.commit()
        ok = sum(1 for r in rows if r["repro_status"] == "complete")
        out({"total_findings": len(rows), "reproducible": ok,
             "incomplete": len(rows) - ok, "rows": rows})

    elif op == "replay":
        """报告复现：给一条 command（或 url），产出可重放的 HTTP 报文 + 可跑的 curl 命令。
        合成的东西一律标 synthesized=True；解析不出就返回 ok=False + 补录指引，不编造。"""
        cmd = a.get("command") or ""
        url = a.get("url") or ""
        headers = a.get("headers", [])
        if isinstance(headers, str):
            headers = [x.strip() for x in headers.split("|") if x.strip()]
        parsed = parse_curl(cmd) if cmd else None
        kind = command_kind(cmd) if cmd else None
        base = dict(parsed) if parsed else {}
        if url:
            base["url"] = url
        if a.get("method"):
            base["method"] = a["method"]
        if headers:
            base["headers"] = list(base.get("headers", [])) + headers
        if a.get("data"):
            base["data"] = a["data"]
        if a.get("cookie"):
            base["cookie"] = a["cookie"]
        base.setdefault("tool", cmd)
        if not base.get("url"):
            out({"ok": False, "command_kind": kind,
                 "reason": "命令里没有可解析的 URL（或被识别为 %s 类工具），无法合成可重放请求" % (kind or "未知"),
                 "hint": "给 url 参数补目标地址，或把 step 的 command 补成完整 curl 原文",
                 "synthesized": False})
            return
        out({"ok": True, "command_kind": kind, "synthesized": True,
             "source": "curl命令解析" if parsed else "URL+参数合成",
             "original_command": cmd or None,
             "parsed": {k: base.get(k) for k in ("url", "method", "data", "cookie")},
             "http_request": build_http_request(base),
             "curl": build_curl_command(base),
             "yakit_note": "http_request 可直接粘进 Yakit Repeater；synthesized=True 表示报文含推断成分，报告里须标来源"})

    elif op == "stats":
        g = lambda q: c.execute(q).fetchone()[0]
        out({"db": DB, "engagements": g("SELECT COUNT(*) FROM engagement"),
             "assets": g("SELECT COUNT(*) FROM asset"), "vulns": g("SELECT COUNT(*) FROM vuln"),
             "credentials": g("SELECT COUNT(*) FROM credential"),
             "sessions": g("SELECT COUNT(*) FROM session_tunnel"),
             "steps": g("SELECT COUNT(*) FROM step"), "findings": g("SELECT COUNT(*) FROM finding")})

    elif op == "sql":  # 只读兜底
        if not str(a.get("q", "")).strip().lower().startswith("select"):
            out({"error": "只允许 SELECT"}); return
        out({"rows": [dict(r) for r in c.execute(a["q"]).fetchall()]})
    else:
        out({"error": f"未知 op: {op}"})
    c.close()


if __name__ == "__main__":
    main()
