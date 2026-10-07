#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tgmsg / tgmsg2 — TG 双号工具的统一入口(走常驻 daemon socket, 不再起子进程抢 session 锁)
#
# ★2026-10-05 老板「tgmsg 还有获取init 怎么这么困难啊 优化这个插件」
#   根因: daemon(tg_daemon.py) 其实**早就会** webapp(打开小程序取 initData)/initdata(有目标token直接签)
#         /bulkwebapp(批量+落盘)/buttons/click/dialogs/search/members…,
#         但这个插件把 act 写死成 "msg", 模型根本用不到 → 只能"读一条消息", 剩下全靠人肉。
#   现在: act 直通 —— 上面这些动作全部从一个工具就能调; 不写 act 时仍然按老的"读单条消息"走。
#
# 用法(argv[1] 一个字符串, 三种写法都认):
#   1) 老写法(读单条消息): '现金红包:3570'  'naiwa:现金红包:3570:raw'  'acct=wang group=现金红包 id=3570'
#   2) act 写法:           'act=webapp bot=@xxx'   'act=initdata token=<bot_token>'
#                          'act=buttons group=现金红包 id=3570'   'act=bulkwebapp bots=/tmp/bots.txt'
#   3) JSON 写法:          '{"q":"现金红包:3570"}'  '{"act":"webapp","bot":"@xxx"}'
import json
import os
import re
import socket
import sys

SOCK_HOST = "127.0.0.1"
SOCK_PORT = 8791
BASE = "/opt/deepseek-bot"

# act -> 该 act 的位置参数按这个顺序从键值里取(取到第一个非空的)
ACT_ARGS = {
    "webapp":     ["bot", "target", "id"],
    "bulkwebapp": ["bots", "list", "arg", "target"],
    "initdata":   ["bot_token", "token"],
    "buttons":    ["group", "target", "id"],
    "click":      ["group", "target", "id", "row", "col"],
    "dialogs":    ["limit"],
    "messages":   ["target", "limit", "offset_id", "raw"],
    "search":     ["target", "keyword", "limit", "offset_id"],
    "members":    ["target", "limit"],
    "groupinfo":  ["target"],
    "user":       ["target"],
    "stats":      ["target", "limit", "offset_id"],
    "whois":      ["target", "limit", "offset_id"],
    "send":       ["to", "target", "text"],
    "react":      ["to", "target", "id", "emoji"],
    "join":       ["link"],
}

USAGE = (
    "TG 工具(双号)用法 —— 默认账号 wang, 加 acct=naiwa 换号。\n"
    "  拿小程序 initData : act=webapp bot=@某bot        (或 act=webapp target=@bot id=消息ID 从按钮进)\n"
    "  有目标 bot token  : act=initdata token=<bot_token>   (本地 HMAC 直接签一份, 不用开小程序)\n"
    "  批量取+落盘       : act=bulkwebapp bots=@a,@b  或  bots=/tmp/bots.txt\n"
    "  看消息按钮        : act=buttons group=群名 id=3570\n"
    "  点按钮            : act=click group=群名 id=3570 row=0 col=1\n"
    "  读单条            : q='群名:消息ID[:raw]'   (不写 act 也走这个)\n"
    "  其他              : act=dialogs limit=20 / act=search target=群 keyword=关键字 / act=members target=群\n"
    "H5 说明: 不是小程序、只是普通网页的目标, Telegram 侧**不会**给 initData(这是 TG 规则, 不是工具拿不到);\n"
    "         那种目标把 URL 和 query 参数拿全更有用, 真需要 initData 就用目标 token 走 act=initdata 自己签。"
)


def _parse_plain(s):
    """老写法: '群:ID[:raw]' / 'k=v k=v'"""
    acct, group, mid, raw_flag = "wang", None, None, False
    s = (s or "").strip()
    if "=" in s:
        kv = {}
        for tok in s.replace("&", " ").split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        acct = kv.get("acct", kv.get("account", "wang")).lower()
        group = kv.get("group")
        mid = kv.get("id", kv.get("msgid"))
        raw_flag = kv.get("raw", "").lower() in ("1", "true", "yes", "raw")
    else:
        parts = [p.strip() for p in s.split(":")]
        if parts and parts[0].lower() in ("wang", "naiwa", "api_wang"):
            acct = parts.pop(0).lower()
        if len(parts) >= 2:
            group = parts[0]
            mid = parts[1]
            if len(parts) >= 3 and parts[2].lower() in ("raw", "1", "true"):
                raw_flag = True
        elif len(parts) == 1:
            mid = parts[0]
    if acct == "api_wang":
        acct = "wang"
    return acct, group, mid, raw_flag


def _kv_of(s):
    """把 'act=webapp bot=@x acct=naiwa' 或 JSON 都解析成 dict"""
    s = (s or "").strip()
    if s.startswith("{"):
        d = {}
        try:
            d = json.loads(s)
        except Exception:
            # 模型经常给半截 JSON → 尽力抠
            for _k in ("act", "q", "bot", "target", "group", "id", "msgid", "raw", "acct",
                       "account", "bots", "token", "bot_token", "row", "col", "emoji",
                       "text", "limit", "keyword", "link", "to"):
                _m = re.search(r'"%s"\s*:\s*("[^"]*"|[^,}\s]+)' % _k, s)
                if _m:
                    d[_k] = _m.group(1).strip().strip('"')
        return {str(k).lower(): v for k, v in (d or {}).items()}
    kv = {}
    for tok in s.replace("&", " ").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return kv


def _bare_token(s):
    """取第一个不是 k=v 的裸 token(模型常写成 'acct=naiwa @bot:5' 这种混排)"""
    for tok in (s or "").replace("&", " ").split():
        if "=" not in tok and tok.strip():
            return tok.strip()
    return ""


def build(s):
    """→ (acct, act, args)

    ★2026-10-05 老板实测「tgmsg 彻底挂了, 所有调用都被当成整个 JSON 字符串传给 q」根因:
      模型爱把参数**套一层**给过来 —— `{"q": "act=dialogs limit=50"}` / `q=act=webapp bot=@x`。
      我只解析了外层, 里面那个 act 串没再拆一次 → 落到"读单条"分支 → 报"没解析出消息ID"。
      现在: 外层没有 act 时, 把 q 的值**再拆一层**(外层其余键补进去), 所以三种套法都认。
    """
    kv = _kv_of(s)
    # 只装了 JSON 的情况: {"q": "..."} → 先把 q 拆出来当整体串
    if list(kv.keys()) == ["q"] and isinstance(kv.get("q"), str):
        _inner_all = str(kv["q"])
    else:
        _inner_all = s
    acct = str(kv.get("acct") or kv.get("account") or "wang").lower()
    if acct == "api_wang":
        acct = "wang"
    # ★套层拆解: 外层没 act, 但 q 的值里有 act → 以 q 的值重新解析, 外层其余键并进去
    if not kv.get("act"):
        _q = kv.get("q")
        if isinstance(_q, str) and "act" in _q.lower() and "=" in _q:
            _inner_kv = _kv_of(_q)
            for _k, _v in kv.items():
                if _k != "q" and _k not in _inner_kv:
                    _inner_kv[_k] = _v
            kv = _inner_kv
    act = str(kv.get("act") or "").lower().strip()
    if not act:
        # 老写法: 读单条消息
        _src = _inner_all
        _a, _g, _i, _r = _parse_plain(_src)
        if not (_g or _i):
            # 混排兜底: 'acct=naiwa @bot:5' 这种, 拿裸 token 再试一次
            _bt = _bare_token(_src)
            if _bt:
                _a2, _g2, _i2, _r2 = _parse_plain(_bt)
                _a, _g, _i, _r = _a2, (_g2 or _g), (_i2 or _i), (_r2 or _r)
        args = ([_g] if _g else []) + ([str(_i)] if _i else []) + (["raw"] if _r else [])
        # 账号: 外层 kv 显式给了 acct 就用它(混排 'acct=naiwa @bot:5' 时 _parse_plain 会退回默认 wang)
        _acct_final = acct if str(kv.get("acct") or kv.get("account") or "") else _a
        return _acct_final, "msg", args
    # act 写法: 按表取位置参数
    keys = ACT_ARGS.get(act) or []
    args = []
    for _k in keys:
        _v = kv.get(_k)
        if _v in (None, "", []):
            continue
        if _k == "raw" and str(_v).lower() in ("1", "true", "yes"):
            args.append("raw")
        else:
            args.append(str(_v))
    return acct, act, args


def call_daemon(acct, act, args, timeout=180):
    try:
        s = socket.create_connection((SOCK_HOST, SOCK_PORT), timeout=timeout)
        s.settimeout(timeout)
        req = json.dumps({"id": 1, "account": acct, "act": act, "args": args}, ensure_ascii=False) + "\n"
        s.sendall(req.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        d = json.loads(buf.decode("utf-8", "replace").strip() or "{}")
        if d.get("ok"):
            return d.get("out") or "(空)"
        return "ERR(daemon): " + str(d.get("err"))
    except Exception as e:
        return (f"ERR(daemon不可用): {type(e).__name__}: {e}\n"
                f"提示: 让SPECTRE随便跑一次 tg 工具即可拉起 daemon; 或直接用内置 tg 工具。")


def main():
    if len(sys.argv) < 2:
        print("TGMSG\nUSAGE:\n" + USAGE)
        return
    raw = sys.argv[1]
    acct, act, args = build(raw)
    if act == "msg" and not args:
        print(f"TGMSG\nERR: 没解析出消息ID, 也没有能识别的 act(收到 {raw[:90]!r})\n" + USAGE)
        return
    print("TGMSG")
    print(call_daemon(acct, act, args))


if __name__ == "__main__":
    main()
