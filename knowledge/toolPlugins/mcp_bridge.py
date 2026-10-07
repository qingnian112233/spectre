#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 客户端桥: 让本机 AI 能直接调用 MCP server 的工具。
用法: python3 mcp_bridge.py '{"op":"list"}'
op:
  add    {name,command,args,env}  注册一个 MCP server
  remove {server}                 删除注册
  list                            列出已注册 server + 运行状态
  start  {server}                 拉起常驻守护进程
  stop   {server}                 停掉守护进程
  tools  {server}                 列出该 server 暴露的工具 (tools/list)
  call   {server,tool,arguments}  调用工具 (tools/call)
  sync   {server}                 把该 server 的工具自动写成本地插件(热加载即可用)
  raw    {server,method,params}   直接打任意 JSON-RPC 方法
"""
import sys, os, json, socket, subprocess, time, re

BASE = "/opt/deepseek-bot/knowledge"
PLUG = os.path.join(BASE, "toolPlugins")
CONF = os.path.join(BASE, "mcp_servers.json")
DAEMON = os.path.join(PLUG, "mcp_daemon.py")


def load_conf():
    try:
        return json.load(open(CONF))
    except Exception:
        return {"servers": {}}


def save_conf(c):
    json.dump(c, open(CONF, "w"), ensure_ascii=False, indent=1)


def sock_path(name):
    return "/tmp/mcp_%s.sock" % name


def alive(name):
    p = sock_path(name)
    if not os.path.exists(p):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(p)
        s.sendall((json.dumps({"op": "ping"}) + "\n").encode())
        r = json.loads(s.makefile().readline())
        s.close()
        return bool(r.get("ok") and r.get("alive"))
    except Exception:
        return False


def start_daemon(name):
    cfg = load_conf()["servers"].get(name)
    if not cfg:
        return False, "server 未注册: %s" % name
    if alive(name):
        return True, "已在运行"
    tmpcfg = "/tmp/mcp_cfg_%s.json" % name
    json.dump(cfg, open(tmpcfg, "w"), ensure_ascii=False)
    subprocess.Popen(["python3", DAEMON, tmpcfg], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(600):
        time.sleep(0.25)
        if os.path.exists(sock_path(name)) and alive(name):
            return True, "已启动"
    tail = ""
    try:
        with open("/tmp/mcp_%s.log" % name, "rb") as f:
            tail = f.read()[-800:].decode("utf-8", "replace")
    except Exception:
        pass
    return False, "启动超时(检查 command/args, 如 npx/node 是否装好)\n--- server日志 ---\n%s" % tail


def ask(name, method, params=None, timeout=90, notify=False):
    if not alive(name):
        ok, msg = start_daemon(name)
        if not ok:
            return {"ok": False, "error": msg}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout + 10)
    s.connect(sock_path(name))
    req = {"method": method, "params": params, "timeout": timeout, "notify": notify}
    s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
    line = s.makefile().readline()
    s.close()
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": "守护进程无响应"}


def slug(s):
    return re.sub(r"[^a-z0-9_]", "_", str(s).lower())


def to_schema(tool):
    sc = tool.get("inputSchema") or {"type": "object", "properties": {}}
    if "type" not in sc:
        sc["type"] = "object"
    return sc


def main():
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    op = (a.get("op") or "list").lower()
    sv = a.get("server") or ""
    conf = load_conf()

    if op == "add":
        name = a.get("name")
        if not name:
            print("需要 name"); return
        conf["servers"][name] = {
            "name": name,
            "command": a.get("command") or "npx",
            "args": a.get("args") or [],
            "env": a.get("env") or {},
        }
        save_conf(conf)
        print("已注册 MCP server: %s -> %s %s" % (name, conf["servers"][name]["command"], " ".join(conf["servers"][name]["args"])))
        return

    if op == "remove":
        conf["servers"].pop(sv, None)
        save_conf(conf)
        print("已删除: %s" % sv); return

    if op == "list":
        out = []
        for n in conf["servers"]:
            out.append({"server": n, "running": alive(n)})
        print(json.dumps(out, ensure_ascii=False, indent=1)); return

    if op == "start":
        ok, msg = start_daemon(sv)
        print("%s %s: %s" % ("OK" if ok else "ERR", sv, msg)); return

    if op == "stop":
        if alive(sv):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(sock_path(sv))
            s.sendall((json.dumps({"op": "stop"}) + "\n").encode()); s.close(); time.sleep(0.4)
            print("已停: %s" % sv)
        else:
            print("%s 未在运行" % sv)
        return

    if op == "tools":
        r = ask(sv, "tools/list", {})
        resp = r.get("resp") or {}
        tools = (resp.get("result") or {}).get("tools") or []
        print(json.dumps([{"name": t.get("name"), "description": t.get("description"),
                           "schema": to_schema(t)} for t in tools], ensure_ascii=False, indent=1))
        return

    if op == "raw":
        r = ask(sv, a.get("method"), a.get("params"), notify=bool(a.get("notify")))
        print(json.dumps(r, ensure_ascii=False, indent=1)[:6000]); return

    if op == "call":
        tool = a.get("tool"); args = a.get("arguments") or {}
        r = ask(sv, "tools/call", {"name": tool, "arguments": args}, timeout=int(a.get("timeout") or 90))
        resp = (r.get("resp") or {}).get("result")
        if resp is None:
            print(json.dumps(r, ensure_ascii=False)[:4000]); return
        # 展平 content
        parts = []
        for c in (resp.get("content") or []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        print("\n".join(parts) if parts else json.dumps(resp, ensure_ascii=False)[:4000])
        return

    if op == "sync":
        r = ask(sv, "tools/list", {})
        tools = ((r.get("resp") or {}).get("result") or {}).get("tools") or []
        if not tools:
            print("未取到工具(server 是否支持 tools?)"); return
        made = []
        for t in tools:
            tn = t.get("name")
            pname = "mcp_%s_%s" % (slug(sv), slug(tn))
            script = os.path.join(PLUG, pname + ".py")
            with open(script, "w") as f:
                f.write("#!"+sys.executable+"\n# -*- coding: utf-8 -*-\n")
                f.write("import sys,json,subprocess\n")
                f.write("args=json.loads(sys.argv[1]) if len(sys.argv)>1 else {}\n")
                f.write("req=json.dumps({'op':'call','server':%r,'tool':%r,'arguments':args})\n" % (sv, tn))
                f.write("p=subprocess.run(['python3',%r,req],capture_output=True,timeout=%d)\n" % (os.path.join(PLUG, "mcp_bridge.py"), int(a.get("timeout") or 180)))
                f.write("sys.stdout.write(p.stdout.decode('utf-8','replace') or p.stderr.decode('utf-8','replace'))\n")
            desc = (t.get("description") or "")[:400]
            decl = {
                "name": pname,
                "description": "[MCP:%s] %s" % (sv, desc),
                "schema": to_schema(t),
                "exec": script,
                "admin_only": True,
                "timeout": int(a.get("timeout") or 180),
            }
            json.dump(decl, open(os.path.join(PLUG, pname + ".json"), "w"), ensure_ascii=False, indent=1)
            made.append(pname)
        print("已注册 %d 个 MCP 工具为本地插件:\n%s" % (len(made), "\n".join(made)))
        return

    print("未知 op: %s" % op)


if __name__ == "__main__":
    main()
