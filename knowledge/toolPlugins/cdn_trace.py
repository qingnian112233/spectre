#!/usr/bin/env python3
"""cdn_trace 插件执行器 — 声明式封装 /opt/tools/cdn-origin/ 工具链.
用法: cdn_trace_plugin.py '{"tool":"tracer|ranges|monitor|annex|quick","domain":"target.com",...}'
"""
import sys, json, subprocess, os

DIR = "/opt/tools/cdn-origin"

def run_exe(py, args=None):
    cmd = [sys.executable, os.path.join(DIR, py)] + (args or [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = (r.stdout or "").strip()
    if r.returncode not in (0, None) and r.stderr:
        out = (out + "\n[stderr] " + r.stderr[:2000]).strip()
    return out

def main():
    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except Exception as e:
        print(f"usage err: {e}"); return
    tool = args.get("tool", "tracer")
    dom = args.get("domain", args.get("target", ""))
    try:
        if tool == "ranges":
            ip = args.get("ip") or args.get("host") or ""
            if args.get("filter"):  # batch filter
                ips = args["filter"] if isinstance(args["filter"], list) else [args["filter"]]
                extra = ["--filter"] + ips
            else:
                extra = [ip]
            print(run_exe("cdn_ranges.py", extra))
        elif tool == "tracer":
            extra = [dom]
            if args.get("no_verify"): extra.append("--no-verify")
            if args.get("no_fingerprint"): extra.append("--no-fingerprint")
            print(run_exe("cdn_tracer.py", extra))
        elif tool == "monitor":
            extra = [dom]
            if args.get("interval"): extra += ["--interval", str(args["interval"])]
            if args.get("for"): extra += ["--for", str(args["for"])]
            print(run_exe("cdn_origin_monitor.py", extra))
        elif tool == "annex":
            sub = args.get("action", "asn-scan")
            extra = [sub, dom]
            if args.get("host"): extra.append(args["host"])
            print(run_exe("cdn_srcannex.py", extra))
        elif tool == "quick":
            print(subprocess.run(["bash", os.path.join(DIR,"cdn_quick.sh"), dom],
                                 capture_output=True, text=True, timeout=180).stdout)
        else:
            print("unknown tool")
    except Exception as e:
        print(f"exec err: {e}")

if __name__ == "__main__":
    main()
