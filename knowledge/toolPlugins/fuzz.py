#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fuzz: 目录/文件/接口/登录/子域 爆破 —— 字典路径由本脚本决定, 调用方只需给 mode。
   (2026-10-06 老板实测: 模型自己写 ffuf 时把字典路径猜错两次, 白烧两轮。路径不该由模型记。)
   参数 JSON 由 argv[1] 传入。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

SEC = "/usr/share/seclists"
WL = {
    "dir":    SEC + "/Web-Content/raft-medium-directories.txt",
    "small":  SEC + "/Web-Content/raft-small-directories.txt",
    "common": SEC + "/Web-Content/common.txt",
    "big":    SEC + "/Web-Content/big.txt",
    "file":   SEC + "/Web-Content/raft-medium-files.txt",
    "api":    SEC + "/Web-Content/api/api-endpoints.txt",
    "login":  SEC + "/Web-Content/Logins.fuzz.txt",
    "sub":    SEC + "/DNS/subdomains-top1million-5000.txt",
}
WL_DESC = {
    "dir": "raft-medium-directories(3万词, 通用目录)", "small": "raft-small-directories(2万词, 快)",
    "common": "common(4.7千词, 最快)", "big": "big(1千词, 秒出)",
    "file": "raft-medium-files(文件名)", "api": "api-endpoints(接口路径)",
    "login": "Logins.fuzz(登录页)", "sub": "subdomains-top1million-5000(子域5000)",
}


def _die(m):
    print(m)
    sys.exit(0)


try:
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception as e:
    _die(f"fuzz: 参数不是合法 JSON ({e})")

mode = str(a.get("mode") or "dir").strip().lower()
target = str(a.get("target") or a.get("url") or a.get("host") or "").strip()
if not target:
    _die("fuzz: 需要 target(如 http://1.2.3.4:8080 或 example.com)")
if mode not in WL:
    _die(f"fuzz: mode 只认 {'/'.join(WL.keys())} (收到 {mode!r})")
wl = WL[mode]
if not os.path.exists(wl) or os.path.getsize(wl) < 500:
    _die(f"fuzz: 字典缺失 {wl} —— 让主人补装 SecLists")

if mode == "sub":
    if "://" in target:
        target = target.split("://", 1)[1]
    target = target.split("/")[0].split(":")[0]
    url_t = "http://FUZZ." + target
    tag = "子域枚举"
else:
    if "://" not in target:
        target = "http://" + target
    target = target.rstrip("/")
    url_t = target + "/FUZZ"
    tag = {"dir": "目录爆破", "small": "目录爆破(小字典)", "common": "目录爆破(common)",
           "big": "目录爆破(极速)", "file": "文件爆破", "api": "接口爆破",
           "login": "登录页爆破"}.get(mode, "爆破")

threads = int(a.get("threads") or 40)
threads = max(1, min(threads, 200))
rate = int(a.get("rate") or 0)
ext = str(a.get("ext") or "").strip().lstrip(".")
mc = str(a.get("match_code") or "200,204,301,302,307,401,403,405,500").strip()
timeout = int(a.get("timeout") or 600)
timeout = max(30, min(timeout, 1800))

_cmd = ["ffuf", "-u", url_t, "-w", wl, "-t", str(threads), "-mc", mc, "-s",
        "-timeout", "12", "-of", "csv", "-o", "/tmp/_ffuf_out.csv"]
# -ac = 自动校准(干掉"全场同一响应"的误报)。实测: 扫本机 nginx 时不加它出 4434 条假 401,
# 加了变 0 条 ✅。但它也可能把"统一 403/401 的真目录"一起吃掉 → 给 no_ac 开关让人能关。
# 子域模式不加: 它会对 *.随机子域 发校准请求, 刷一屏 DNS no such host 噪音。
if mode != "sub" and not a.get("no_ac"):
    _cmd += ["-ac"]
if rate:
    _cmd += ["-rate", str(rate)]
if ext:
    _cmd += ["-e", "." + ext]
if a.get("extra"):
    _cmd += [str(x) for x in str(a["extra"]).split()]
try:
    os.remove("/tmp/_ffuf_out.csv")
except Exception:
    pass

_t0 = time.time()
try:
    p = subprocess.run(_cmd, capture_output=True, text=True, timeout=timeout)
    _out, _err, _rc = p.stdout or "", p.stderr or "", p.returncode
except subprocess.TimeoutExpired:
    _out, _err, _rc = "", f"超时 {timeout}s(字典没跑完)", -1

_dt = time.time() - _t0
_rows = []
try:
    with open("/tmp/_ffuf_out.csv", encoding="utf-8", errors="replace") as f:
        for ln in f:
            _p = ln.rstrip("\n").split(",")
            if len(_p) >= 5 and _p[0] not in ("url", "FUZZ"):
                _rows.append((_p[4].strip(), _p[3].strip(), _p[0].strip(), _p[2].strip()))
except Exception:
    pass

_dirs = {}
for _st, _ln, _u, _c in _rows:
    _dirs[_st] = _dirs.get(_st, 0) + 1

_L = [f"【{tag}】 {url_t}",
      f"字典 {os.path.basename(wl)} · {WL_DESC.get(mode, '')} · 并发 {threads}"
      + (f" · ext={ext}" if ext else "") + f" · 用时 {_dt:.0f}s"]
if not _rows:
    _L.append(f"命中 0 个 (匹配码 {mc})")
    _L.append(f"注: 若你确定这个站点有东西, 可能是自动校准(-ac)把'全场统一响应'整批过滤了 —— "
              f"加 \"no_ac\":true 再跑一次确认; 也可以先 curl 看一眼根路径返回什么。")
    if _err.strip():
        _L.append("stderr: " + _err.strip()[:300])
    if _out.strip():
        _L.append("stdout: " + _out.strip()[:400])
else:
    _L.append(f"命中 {len(_rows)} 个  (状态码分布: "
              + " ".join(f"{k}×{v}" for k, v in sorted(_dirs.items())) + ")")
    # 2026-10-06: 全场同一响应 = 结果不可信(实测扫本机 nginx 出 4434 条 401, 全是误报)
    _sus = (len(_rows) > 300 and len(_dirs) == 1) or len(_rows) > 3000
    if _sus:
        _L.append(f"⚠️ 疑似全场同一响应(命中 {len(_rows)} 条但只有 {list(_dirs.keys())} 一种状态码) —— "
                  f"基本是误报: 该站点对所有路径都回同一个页面。换 target / 调 match_code / "
                  f"先用 curl 手工看一眼根路径长什么样, 别拿这批结果当发现。")
    _show = 15 if _sus else 60
    for _st, _ln, _u, _c in _rows[:_show]:
        _L.append(f"  {_st:<4} {_ln:>8}B  {_u}")
    if len(_rows) > _show:
        _L.append(f"  …还有 {len(_rows) - _show} 个(完整CSV: /tmp/_ffuf_out.csv)")
_L.append(f"提示: 结果里带 www/WAF 页面的先用 status 301/403 那批; 要换字典改 mode="
          f"{'/'.join([k for k in WL if k != mode][:5])}")
print("\n".join(_L))
